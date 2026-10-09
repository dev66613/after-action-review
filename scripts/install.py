"""Local native install with no cache edits, hook-trust writes or publication."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys

PLUGIN = Path(__file__).resolve().parents[1]


def windows_path(path: Path) -> str:
    text = str(path.absolute())
    parts = path.absolute().parts
    if len(parts) > 3 and parts[1] == 'mnt' and len(parts[2]) == 1:
        return parts[2].upper() + ':/' + '/'.join(parts[3:])
    raise ValueError('Windows install source must be on a mounted local drive')


def run(command: list[str]) -> dict:
    p = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=60)
    if p.returncode:
        raise RuntimeError(p.stderr.strip()[:1000] or p.stdout.strip()[:1000] or 'Native install command failed')
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {'output': p.stdout.strip()[:1500]}


def native_codex(host: str, explicit: Path | None) -> str:
    if explicit:
        if not explicit.is_file() or (host == 'windows' and explicit.suffix.lower() != '.exe'):
            raise ValueError('Supply the existing native Codex executable, not a shell wrapper')
        return str(explicit.absolute())
    if host == 'windows':
        found = shutil.which('codex.exe')
        if found:
            return found
        if os.name == 'nt':
            appdata = Path(os.environ.get('APPDATA', str(Path.home() / 'AppData' / 'Roaming')))
        else:
            home = Path(os.environ.get('CODEX_HOME', ''))
            if not str(home).startswith('/mnt/'):
                raise ValueError('Supply --codex-executable for the Windows host')
            appdata = home.parent / 'AppData' / 'Roaming'
        package = appdata / 'npm' / 'node_modules' / '@openai' / 'codex' / 'node_modules' / '@openai'
        for arch, target in [('x64', 'x86_64'), ('arm64', 'aarch64')]:
            candidate = package / f'codex-win32-{arch}' / 'vendor' / f'{target}-pc-windows-msvc' / 'bin' / 'codex.exe'
            if candidate.is_file():
                return str(candidate)
        raise ValueError('Native Codex executable unavailable; supply --codex-executable')
    found = shutil.which('codex')
    if not found:
        raise ValueError('Codex CLI unavailable on this execution host')
    return found


def main():
    parser = argparse.ArgumentParser(description='Prepare/install an AAR local marketplace')
    parser.add_argument('--host', choices=['auto','linux','windows'], default='auto')
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--codex-executable', type=Path, help='Existing target-host executable; Windows requires codex.exe')
    parser.add_argument('--format', choices=['codex', 'portable'], default='codex',
                        help='Codex compatibility layout avoids root-manifest hook discovery gaps in current hosts')
    parser.add_argument('--install', action='store_true', help='Register and install using the native host CLI')
    args = parser.parse_args()
    host = args.host
    if host == 'auto':
        host = 'windows' if os.name == 'nt' or os.environ.get('CODEX_HOME','').startswith('/mnt/c/') else 'linux'
    if args.destination:
        dest = args.destination.absolute()
    elif host == 'windows' and os.name != 'nt':
        home = Path(os.environ.get('CODEX_HOME',''))
        if not str(home).startswith('/mnt/c/'):
            raise ValueError('Supply the Windows marketplace destination')
        dest = home / 'local-marketplaces' / 'aar-local'
    else:
        dest = Path.home() / '.codex' / 'local-marketplaces' / 'aar-local'
    if dest.exists():
        raise ValueError('Destination already exists; use a new version directory or review/update the existing source explicitly')
    executable = native_codex(host, args.codex_executable) if args.install else None
    dest.mkdir(parents=True, mode=0o700)
    source = dest / 'plugins' / 'after-action-review'
    shutil.copytree(PLUGIN, source, ignore=shutil.ignore_patterns('__pycache__','*.pyc','.git','.github','artifacts','dist'))
    portable = source / 'plugin.json'
    preserved = source / 'metadata' / 'portable-plugin.json'
    if args.format == 'codex' and portable.exists():
        preserved.parent.mkdir(exist_ok=True)
        portable.replace(preserved)
    elif args.format == 'portable' and not portable.exists():
        if not preserved.is_file():
            raise ValueError('Portable manifest unavailable in this package')
        preserved.rename(portable)
    catalog = {'name':'aar-local','interface':{'displayName':'Local After Action Review'},
               'plugins':[{'name':'after-action-review','source':{'source':'local','path':'./plugins/after-action-review'},
                           'policy':{'installation':'AVAILABLE','authentication':'ON_INSTALL'},'category':'Productivity'}]}
    path = dest / '.agents' / 'plugins' / 'marketplace.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    result = {'status':'prepared','marketplace':str(dest),'source':str(source),'host':host,
              'format':args.format,
              'hook_trust':'pending human review in /hooks','publication':'none'}
    if args.install:
        if host == 'windows' and os.name != 'nt':
            target = windows_path(dest)
        else:
            target = str(dest)
        def native(values):
            return run([executable] + values)
        result['registration'] = native(['plugin','marketplace','add',target,'--json'])
        result['installation'] = native(['plugin','add','after-action-review@aar-local','--json'])
        result['status'] = 'installed_pending_hook_trust'
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:
        main()
    except (ValueError,RuntimeError,OSError,subprocess.TimeoutExpired) as exc:
        print(json.dumps({'status':'installation_failed','cause':str(exc)[:1200]},ensure_ascii=False),file=sys.stderr)
        raise SystemExit(1)
