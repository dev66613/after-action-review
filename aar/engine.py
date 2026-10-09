"""Deterministic, private file transactions. Validation is structural only.

Only an explicit caller invokes apply; proposal source text grants no approval.
Secure POSIX directory handles are required; no validator executes commands.
"""
from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tomllib
import uuid
from datetime import datetime, timezone


SAFE_KINDS = frozenset({'skill', 'prompt', 'workflow', 'workspace_doc'})
REVIEW_KINDS = frozenset({'config', 'hook', 'tool', 'workspace_code'})
VALIDATORS = frozenset({'markdown', 'skill', 'json', 'toml', 'python'})
PRIVATE_NAMES = frozenset({'auth', 'auth.json', 'credentials', 'credentials.json',
                           'secrets', 'keys', 'id_rsa', 'id_ed25519', 'id_dsa'})
SECRET_CONTENT = re.compile(
    r'-----BEGIN (?:[A-Z ]*PRIVATE KEY)-----|'
    r'\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b|'
    r'^\s*["\']?(?:[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|SECRET_KEY|PASSWORD))'
    r'["\']?\s*[:=]\s*["\']?(?!\s*(?:["\']?\s*$|\$\{|<|REDACTED|example|placeholder))[^\s"\']{8,}', re.IGNORECASE | re.MULTILINE)
WEAKENING = re.compile(
    r'(?:disable|bypass|ignore|remove|turn\s+off|weaken|skip)\b.{0,65}'
    r'\b(?:sandbox|approval|security\s+(?:check|guard)|hook\s+(?:check|trust|validation))|'
    r'\b(?:sandbox_mode)["\']?\s*[:=]\s*["\']?(?:danger-full-access|off|disabled)|'
    r'\b(?:approval_policy)["\']?\s*[:=]\s*["\']?never|'
    r'\btrust\b.{0,35}\b(?:all|unverified|untrusted)\b.{0,25}\b(?:hooks?|tool\s+output)|'
    r'(?:отключ|обход|игнорир|убрать).{0,50}(?:sandbox|песочниц|подтвержден|провер[кц].{0,15}безопасност)', re.IGNORECASE | re.DOTALL)


def _sha(content: bytes | None) -> str:
    return 'absent' if content is None else hashlib.sha256(content).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Engine:
    """Scoped UTF-8 proposals, hash guards, durable journal, and fast rollback.

    Unsafe storage/policy raises ValueError; transaction failures return records.
    """

    def __init__(self, data_dir: Path, policy: dict):
        self.data_dir = Path(data_dir).absolute()
        self.policy = json.loads(json.dumps(policy))
        self._check_policy()
        with self._lock():
            pass

    def _check_policy(self):
        if not isinstance(self.policy, dict):
            raise ValueError('policy must be an object')
        threshold = self.policy.get('confidence_threshold', .9)
        if isinstance(threshold, bool) or not isinstance(threshold, (float, int)) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError('invalid confidence_threshold')
        for key, default in [('max_patch_bytes', 16384), ('max_daily_changes', 5)]:
            value = self.policy.get(key, default)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f'invalid {key}')
        roots = self.policy.get('roots', [])
        if not isinstance(roots, list):
            raise ValueError('roots must be a list')
        for root in roots:
            if not isinstance(root, dict) or not isinstance(root.get('path'), str) or not Path(root['path']).is_absolute():
                raise ValueError('each root must have an absolute path')
            if '..' in Path(root['path']).parts or not isinstance(root.get('kinds', []), list):
                raise ValueError('invalid root')
        if not isinstance(self.policy.get('auto_apply', False), bool):
            raise ValueError('auto_apply must be boolean')

    @staticmethod
    def _no_symlinks(path: Path):
        for candidate in [*reversed(path.parents), path]:
            try:
                if stat.S_ISLNK(candidate.lstat().st_mode):
                    raise ValueError('symlink traversal is forbidden')
            except FileNotFoundError:
                continue

    def _storage(self, inspect_files=True):
        # The stdlib cannot attest Windows ACL confidentiality; fail closed.
        if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW'):
            raise ValueError('private POSIX storage required; Windows ACL verification unavailable')
        if len(self.data_dir.parts) > 2 and self.data_dir.parts[1] == 'mnt' and re.fullmatch(r'[a-zA-Z]', self.data_dir.parts[2]):
            raise ValueError('use native POSIX storage; mounted Windows drive confidentiality is not verified')
        self._no_symlinks(self.data_dir)
        if not self.data_dir.exists():
            self.data_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._private(self.data_dir, directory=True)
        if inspect_files:
            for entry in self.data_dir.iterdir():
                try:
                    self._private(entry, directory=entry.is_dir())
                except FileNotFoundError:
                    # Unrelated SQLite journals can disappear under their own lock.
                    continue
            active = self.data_dir / 'lessons' / 'active.md'
            if active.exists() or active.is_symlink():
                self._private(active)

    @staticmethod
    def _private(path: Path, directory=False):
        meta = path.lstat()
        if meta.st_uid != os.getuid() or meta.st_mode & 0o077:
            raise ValueError('storage must be owned by current user and private (0700 directories / 0600 files)')
        if directory and not stat.S_ISDIR(meta.st_mode):
            raise ValueError('storage must be a directory')
        if not directory and (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1):
            raise ValueError('storage files must be regular without symlinks or hardlinks')

    @contextlib.contextmanager
    def _lock(self):
        import fcntl
        self._storage(inspect_files=False)
        fd = os.open(self.data_dir / '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._storage()
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    @contextlib.contextmanager
    def _parent(target: Path):
        # A parent swapped for a symlink cannot redirect the held directory fd.
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in target.parent.parts[1:]:
                new_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = new_fd
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    def _read_at(fd: int, name: str):
        try:
            file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        except FileNotFoundError:
            return None, None
        try:
            meta = os.fstat(file_fd)
            if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
                raise ValueError('target must be a regular file without hardlinks')
            with os.fdopen(file_fd, 'rb', closefd=False) as stream:
                return stream.read(), stat.S_IMODE(meta.st_mode)
        finally:
            os.close(file_fd)

    def _atomic(self, path: Path, content: bytes, mode=0o600):
        with self._parent(path) as fd:
            self._replace_at(fd, path.name, content, mode)

    @staticmethod
    def _replace_at(fd: int, name: str, content: bytes | None, mode=0o600):
        if content is None:
            os.unlink(name, dir_fd=fd)
            os.fsync(fd)
            return
        stage = '.aar-' + uuid.uuid4().hex
        out = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(out, 'wb', closefd=False) as stream:
                stream.write(content)
                stream.flush()
                os.fchmod(out, mode)
                os.fsync(out)
            os.replace(stage, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            os.close(out)
            try:
                os.unlink(stage, dir_fd=fd)
            except FileNotFoundError:
                pass

    def _save(self, record: dict):
        self._atomic(self.data_dir / (record['id'] + '.json'), json.dumps(record, ensure_ascii=False, indent=2).encode())

    def _load(self, change_id: str):
        if not isinstance(change_id, str) or not re.fullmatch(r'[0-9a-f]{32}', change_id):
            raise ValueError('invalid change id')
        return json.loads((self.data_dir / (change_id + '.json')).read_text())

    def _records(self):
        return [json.loads(path.read_text()) for path in sorted(self.data_dir.glob('*.json'))
                if re.fullmatch(r'[0-9a-f]{32}\.json', path.name)]

    @staticmethod
    def _public(record):
        result = {key: value for key, value in record.items() if key not in {'proposal', 'before_mode'}}
        proposal = record.get('proposal')
        if isinstance(proposal, dict):
            for key in ('title', 'kind', 'risk', 'confidence', 'reason', 'evidence', 'validation'):
                result[key] = proposal.get(key)
        return result

    def _scope(self, target: str, kind: str):
        if not isinstance(target, str) or not target or '\x00' in target:
            raise ValueError('target must be an absolute path')
        path = Path(target)
        if not path.is_absolute() or '..' in path.parts:
            raise ValueError('path escape or relative target is forbidden')
        self._no_symlinks(path)
        parts = [part.lower() for part in path.parts]
        if 'sources' in parts or any(parts[i:i+2] == ['plugins', 'cache'] for i in range(len(parts)-1)):
            raise ValueError('synced sources and managed plugin cache are protected')
        if '.system' in parts:
            raise ValueError('system skills are protected even within registered roots')
        if any(part in PRIVATE_NAMES or part.startswith('.env') or part.endswith(('.key', '.pem', '.p12', '.pfx', '.keystore')) for part in parts):
            raise ValueError('credential and key paths are protected')
        plugin_root = Path(__file__).resolve().parent.parent
        if path == plugin_root or plugin_root in path.parents:
            raise ValueError('plugin source and policy are protected')
        if path == self.data_dir or self.data_dir in path.parents:
            if path not in {self.data_dir / 'learned.md', self.data_dir / 'lessons' / 'active.md'}:
                raise ValueError('transaction storage is protected')
        matched = []
        for root in self.policy.get('roots', []):
            root_path = Path(root['path'])
            self._no_symlinks(root_path)
            if path != root_path and root_path not in path.parents:
                continue
            if kind in root.get('kinds', []):
                matched.append(root)
        if not matched:
            raise ValueError('target/kind is outside explicitly registered roots')
        depth = max(len(Path(root['path']).parts) for root in matched)
        return path, all(root.get('auto') is True for root in matched if len(Path(root['path']).parts) == depth)

    @staticmethod
    def _validate(content: str, validator: str):
        if validator not in VALIDATORS:
            raise ValueError('unknown structural validator')
        if '\x00' in content:
            raise ValueError('NUL bytes are forbidden')
        if validator == 'json':
            def bad_constant(value):
                raise ValueError('nonfinite JSON number')
            json.loads(content, parse_constant=bad_constant)
        elif validator == 'toml':
            tomllib.loads(content)
        elif validator == 'python':
            compile(content, '<aar-candidate>', 'exec', dont_inherit=True)
        elif validator in {'markdown', 'skill'}:
            if not content.strip():
                raise ValueError('empty markdown')
            fence = None
            for line in content.splitlines():
                match = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
                if match:
                    token, suffix = match.groups()
                    if fence is None:
                        fence = token
                    elif token[0] == fence[0] and len(token) >= len(fence) and not suffix.strip():
                        fence = None
            if fence:
                raise ValueError('unclosed markdown fence')
            if validator == 'skill':
                if not content.startswith('---\n'):
                    raise ValueError('skill must begin with YAML frontmatter')
                closing = re.search(r'(?m)^---\s*$', content[4:])
                if closing is None or not content[4 + closing.end():].strip():
                    raise ValueError('skill needs closed frontmatter and a body')
                front = content[4:4 + closing.start()]
                for required in ('name', 'description'):
                    match = re.search(r'(?m)^' + required + r':[ \t]*(.+)$', front)
                    if not match or not match.group(1).strip().strip('"\''):
                        raise ValueError('skill frontmatter requires name and description')

    def _assess(self, proposal: dict, before: bytes | None):
        hard, review = [], []
        kind = proposal.get('kind')
        if not isinstance(kind, str) or kind not in SAFE_KINDS | REVIEW_KINDS:
            hard.append('unknown change kind')
        risk = proposal.get('risk')
        if not isinstance(risk, str) or risk not in {'low', 'medium', 'high'}:
            hard.append('invalid risk')
        confidence = proposal.get('confidence')
        valid_confidence = not isinstance(confidence, bool) and isinstance(confidence, (float, int)) and math.isfinite(confidence) and 0 <= confidence <= 1
        if not valid_confidence:
            hard.append('invalid confidence')
        for key in ('title', 'reason'):
            if not isinstance(proposal.get(key), str) or not proposal[key].strip():
                hard.append(f'{key} is required')
        evidence = proposal.get('evidence')
        if not isinstance(evidence, list) or any(not isinstance(item, dict) or not isinstance(item.get('source'), str) or not item['source'].strip() or not isinstance(item.get('observation'), str) or not item['observation'].strip() for item in evidence):
            hard.append('evidence must contain sources and nonempty observations')
            evidence = []
        sources = {item['source'].strip() for item in evidence}
        auto_root = False
        try:
            _, auto_root = self._scope(proposal.get('target'), kind)
        except (ValueError, OSError) as exc:
            hard.append(str(exc))
        after = proposal.get('after')
        after_bytes = None
        if kind == 'skill' and proposal.get('validation') != 'skill':
            hard.append('skill changes require the skill structural validator')
        if not isinstance(after, str):
            hard.append('after must be UTF-8 text')
        else:
            try:
                after_bytes = after.encode('utf-8')
                self._validate(after, proposal.get('validation'))
                if len(after_bytes) > self.policy.get('max_patch_bytes', 16384):
                    hard.append('candidate exceeds max_patch_bytes')
                if SECRET_CONTENT.search(after):
                    hard.append('candidate contains credential material')
            except (ValueError, TypeError, SyntaxError, UnicodeError):
                hard.append('structural validation failed (unknown validator or invalid content)')
        old_text = ''
        if before is not None:
            try:
                old_text = before.decode('utf-8')
                if SECRET_CONTENT.search(old_text):
                    hard.append('refusing to back up credential material')
            except UnicodeError:
                hard.append('existing target must be UTF-8 text')
        if proposal.get('before_sha256') != _sha(before):
            hard.append('current target hash does not match before_sha256')
        if after_bytes == before:
            hard.append('proposal makes no change')
        if isinstance(after, str):
            added = '\n'.join(line[2:] for line in difflib.ndiff(old_text.splitlines(), after.splitlines()) if line.startswith('+ '))
            if WEAKENING.search(added):
                review.append('security weakening instructions are forbidden; approval cannot authorize this action')
        if not self.policy.get('auto_apply', False):
            review.append('automatic apply is disabled')
        if not auto_root:
            review.append('root requires review')
        target_suffix = Path(proposal.get('target') or '').suffix.lower() if isinstance(proposal.get('target'), str) else ''
        if (isinstance(kind, str) and kind in REVIEW_KINDS) or proposal.get('validation') == 'python' or target_suffix not in {'.md', '.txt'}:
            review.append('config/hook/tool/workspace_code or executable/non-document target requires review')
        if risk != 'low':
            review.append('risk requires review')
        if valid_confidence and confidence < self.policy.get('confidence_threshold', .9):
            review.append('confidence is below threshold')
        if len(sources) < 2:
            review.append('automatic apply needs two distinct evidence sources')
        return hard, review, after_bytes

    def propose(self, proposal: dict) -> dict:
        if not isinstance(proposal, dict):
            raise ValueError('proposal must be an object')
        with self._lock():
            proposal = json.loads(json.dumps(proposal))
            before, read_error = None, None
            try:
                path, _ = self._scope(proposal.get('target'), proposal.get('kind'))
                with self._parent(path) as fd:
                    before, _ = self._read_at(fd, path.name)
            except (ValueError, OSError) as exc:
                read_error = str(exc)
            hard, review, after = self._assess(proposal, before)
            if read_error and read_error not in hard:
                hard.append(read_error)
            status = 'rejected' if hard else ('needs_review' if review else 'ready')
            diff = ''
            if not hard:
                diff = ''.join(difflib.unified_diff((before or b'').decode().splitlines(True), proposal['after'].splitlines(True), fromfile='before', tofile='after'))[:12000]
            record = {'id': uuid.uuid4().hex, 'status': status, 'reasons': hard + review,
                      'target': proposal.get('target'), 'before_sha256': _sha(before),
                      'after_sha256': _sha(after), 'diff_preview': diff,
                      'validation_scope': 'structural only; no behavioral proof',
                      'created_at': _now(), 'proposal': None if hard else proposal}
            self._save(record)
            return self._public(record)

    def list_changes(self) -> list[dict]:
        with self._lock():
            return [self._public(record) for record in self._records()]

    def _finish(self, record, status, reason):
        finished = {**record, 'status': status, 'reasons': [reason]}
        self._save(finished)
        record.update(finished)
        return self._public(record)

    def _daily_count(self):
        day = _now()[:10]
        return sum(record.get('applied_at', '').startswith(day) for record in self._records())

    def apply(self, change_id: str, approved: bool = False, user_decision: str | None = None) -> dict:
        with self._lock():
            record = self._load(change_id)
            if record['status'] not in {'ready', 'needs_review'}:
                return self._public(record)
            proposal = record['proposal']
            try:
                path, _ = self._scope(record['target'], proposal['kind'])
                with self._parent(path) as fd:
                    before, mode = self._read_at(fd, path.name)
                    hard, review, after = self._assess(proposal, before)
                    if hard:
                        return self._finish(record, 'conflict' if _sha(before) != record['before_sha256'] else 'rejected', '; '.join(hard))
                    if any('security weakening' in item for item in review) or (review and approved is not True):
                        return self._finish(record, 'needs_review', '; '.join(review))
                    if any(item['status'] in {'prepared', 'applying', 'rolling_back'} for item in self._records()):
                        return self._finish(record, 'needs_review', 'recover pending transactions before another apply')
                    if self._daily_count() >= self.policy.get('max_daily_changes', 5):
                        return self._finish(record, 'needs_review', 'daily committed action cap reached')
                    if approved is True:
                        record['approval'] = {'accepted_at': _now(),
                                              'decision': user_decision,
                                              'source': 'caller-supplied decision' if user_decision else 'explicit engine API approval'}
                    record['before_mode'] = mode
                    self._atomic(self.data_dir / (change_id + '.before'), before or b'')
                    self._atomic(self.data_dir / (change_id + '.after'), after)
                    record['status'], record['reasons'] = 'prepared', []
                    self._save(record)
                    current, _ = self._read_at(fd, path.name)
                    if _sha(current) != record['before_sha256']:
                        return self._finish(record, 'conflict', 'target changed during preparation')
                    record['status'] = 'applying'
                    self._save(record)
                    try:
                        current, _ = self._read_at(fd, path.name)
                        if _sha(current) != record['before_sha256']:
                            return self._finish(record, 'conflict', 'target changed before atomic replacement')
                        self._replace_at(fd, path.name, after, mode if mode is not None else 0o600)
                        written, _ = self._read_at(fd, path.name)
                        if _sha(written) != record['after_sha256']:
                            raise ValueError('post-write hash mismatch')
                        self._validate(written.decode(), proposal['validation'])
                    except Exception:
                        current, _ = self._read_at(fd, path.name)
                        if _sha(current) == record['after_sha256']:
                            self._replace_at(fd, path.name, before, mode if mode is not None else 0o600)
                            return self._finish(record, 'failed', 'apply failed; original content restored')
                        if _sha(current) == record['before_sha256']:
                            return self._finish(record, 'failed', 'apply failed; original content unchanged')
                        return self._finish(record, 'conflict', 'apply failed with later target edits; no overwrite')
                    record['applied_at'] = _now()
                    return self._finish(record, 'applied', 'applied with structural validation only')
            except (ValueError, OSError) as exc:
                if record['status'] in {'prepared', 'applying'}:
                    record['reasons'] = ['interrupted transaction requires recover: ' + str(exc)]
                    return self._public(record)
                return self._finish(record, 'failed', str(exc))

    def rollback(self, change_id: str) -> dict:
        with self._lock():
            record = self._load(change_id)
            if record['status'] != 'applied':
                return self._public(record)
            try:
                path, _ = self._scope(record['target'], record['proposal']['kind'])
                with self._parent(path) as fd:
                    current, _ = self._read_at(fd, path.name)
                    if _sha(current) != record['after_sha256']:
                        record['reasons'] = ['rollback conflict: target has later user edits']
                        self._save(record)
                        return {**self._public(record), 'status': 'conflict'}
                    before = self._backup(record)
                    record['status'] = 'rolling_back'
                    self._save(record)
                    self._replace_at(fd, path.name, before, record['before_mode'] if record['before_mode'] is not None else 0o600)
                    record['rolled_back_at'] = _now()
                    return self._finish(record, 'rolled_back', 'original content restored')
            except (ValueError, OSError) as exc:
                if record['status'] == 'rolling_back':
                    record['reasons'] = ['interrupted rollback requires recover: ' + str(exc)]
                    return self._public(record)
                record['reasons'] = ['rollback not started; retry when available: ' + str(exc)]
                self._save(record)
                return self._public(record)

    def _backup(self, record):
        before = (self.data_dir / (record['id'] + '.before')).read_bytes()
        if record['before_sha256'] == 'absent':
            if before:
                raise ValueError('backup hash mismatch')
            return None
        if _sha(before) != record['before_sha256']:
            raise ValueError('backup hash mismatch')
        return before

    def recover(self) -> dict:
        with self._lock():
            recovered = []
            for record in self._records():
                status = record['status']
                if status not in {'prepared', 'applying', 'rolling_back'}:
                    continue
                try:
                    path, _ = self._scope(record['target'], record['proposal']['kind'])
                    with self._parent(path) as fd:
                        current, _ = self._read_at(fd, path.name)
                        current_hash = _sha(current)
                        self._backup(record)
                        if current_hash == record['before_sha256']:
                            result = self._finish(record, 'rolled_back' if status == 'rolling_back' else 'failed', 'recovered original state; no target write')
                        elif current_hash == record['after_sha256']:
                            self._validate(current.decode(), record['proposal']['validation'])
                            if status == 'rolling_back':
                                before = self._backup(record)
                                self._replace_at(fd, path.name, before, record['before_mode'] if record['before_mode'] is not None else 0o600)
                                result = self._finish(record, 'rolled_back', 'completed interrupted rollback')
                            else:
                                record['applied_at'] = record.get('applied_at') or _now()
                                result = self._finish(record, 'applied', 'recovered committed candidate with structural validation only')
                        else:
                            result = self._finish(record, 'conflict', 'recovery conflict: later target edits; no write')
                except OSError as exc:
                    # Keep the durable pending state when a mount/parent is offline.
                    record['reasons'] = ['recovery deferred; retry when available: ' + str(exc)]
                    self._save(record)
                    result = self._public(record)
                except (ValueError, SyntaxError, UnicodeError) as exc:
                    result = self._finish(record, 'conflict', 'recovery refused: ' + str(exc))
                recovered.append(result)
            return {'recovered': recovered, 'count': len(recovered)}
