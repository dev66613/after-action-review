"""Private state and policy. Event text never becomes trusted instructions."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
EVENTS = {"SessionStart", "SessionEnd", "UserPromptSubmit", "PostToolUse", "PreCompact", "Stop", "Interrupt"}
INTERNAL = "[AAR internal review "


def data_path() -> Path:
    explicit = os.environ.get("AAR_DATA_DIR")
    if explicit:
        return Path(explicit).expanduser().absolute()
    # WSL native storage protects backup permissions; CODEX_HOME can be NTFS.
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "CodexAAR"
    return Path.home() / ".local" / "state" / "codex-aar"


def private_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("AAR storage must not be a symbolic link")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
            raise ValueError("Private AAR storage requires native filesystem permissions")


def defaults(data: Path) -> dict:
    return {"auto_apply": True, "confidence_threshold": 0.9,
            "max_patch_bytes": 16384, "max_daily_changes": 5,
            "long_session_minutes": 30, "checkpoint_tool_calls": 50,
            "repeated_error_count": 2, "survey_enabled": True,
            "max_continuations_per_turn": 1,
            "roots": [{"path": str(data / "lessons"), "auto": True,
                       "kinds": ["skill", "prompt", "workflow", "workspace_doc"]}]}


def policy(data: Path) -> dict:
    path = data / "policy.json"
    result = defaults(data)
    if path.exists():
        if path.is_symlink():
            raise ValueError("Policy cannot be a symbolic link")
        supplied = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(supplied, dict):
            raise ValueError("Policy must be an object")
        result.update(supplied)
    for key in ("max_daily_changes", "long_session_minutes", "checkpoint_tool_calls", "repeated_error_count"):
        if type(result[key]) is not int or result[key] < 1:
            raise ValueError(f"Invalid policy setting: {key}")
    if type(result["auto_apply"]) is not bool or type(result["survey_enabled"]) is not bool:
        raise ValueError("auto_apply and survey_enabled must be booleans")
    if not 0.9 <= float(result["confidence_threshold"]) <= 1:
        raise ValueError("confidence_threshold must be between 0.9 and 1")
    return result


def redact(value: str, limit: int = 1200) -> str:
    value = re.sub(r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*\S+", r"\1=[REDACTED]", value)
    value = re.sub(r"(?i)Bearer\s+\S+|sk-[A-Za-z0-9_-]{12,}", "[REDACTED]", value)
    value = re.sub(r"https?://\S+\?\S+", "[URL query redacted]", value)
    return value[:limit]


def redact_object(value):
    if isinstance(value, str):
        return redact(value, 4000)
    if isinstance(value, list):
        return [redact_object(item) for item in value]
    if isinstance(value, dict):
        return {key: "[REDACTED]" if re.fullmatch(r"(?i)(api_key|password|secret|authorization|access_token)", str(key))
                else redact_object(item) for key, item in value.items()}
    return value


class Store:
    def __init__(self, data: Path | None = None):
        self.data = data or data_path()
        private_dir(self.data)
        private_dir(self.data / "lessons")
        path = self.data / "reviews.sqlite3"
        if path.is_symlink():
            raise ValueError("State cannot be a symbolic link")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            if os.name != "nt" and (path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077 or path.stat().st_nlink != 1):
                raise ValueError("Existing AAR database is not private")
        self.db = sqlite3.connect(path, timeout=2)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
          id TEXT PRIMARY KEY, started REAL, checkpoint REAL, tools INTEGER DEFAULT 0,
          user_turn TEXT DEFAULT '', internal INTEGER DEFAULT 0, continued INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS events (
          id TEXT PRIMARY KEY, session TEXT, turn TEXT, kind TEXT, fingerprint TEXT,
          created REAL, reviewed INTEGER DEFAULT 0, metadata TEXT);
        CREATE TABLE IF NOT EXISTS reviews (
          id TEXT PRIMARY KEY, session TEXT, turn TEXT, created REAL, report TEXT,
          outcome TEXT, survey TEXT DEFAULT 'not_requested', feedback TEXT);
        """)
        self.db.commit()
        if os.name != "nt":
            path.chmod(0o600)

    def close(self):
        self.db.close()

    def ensure_session(self, session: str) -> None:
        now = time.time()
        self.db.execute("INSERT OR IGNORE INTO sessions(id,started,checkpoint) VALUES(?,?,?)", (session, now, now))

    def session(self, session: str) -> dict:
        self.ensure_session(session)
        return dict(self.db.execute("SELECT * FROM sessions WHERE id=?", (session,)).fetchone())

    def event(self, session: str, turn: str, kind: str, key: str, metadata: dict | None = None) -> str:
        # IDs and fingerprints are generated locally, never interpreted as paths.
        identity = hashlib.sha256(json.dumps([session, turn, kind, key]).encode()).hexdigest()[:32]
        self.db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,0,?)",
                        (identity, session, turn, kind, key, time.time(), json.dumps(metadata or {})))
        return identity

    def pending(self, session: str | None = None) -> list[dict]:
        sql = "SELECT * FROM events WHERE reviewed=0"
        args = ()
        if session:
            sql += " AND session=?"
            args = (session,)
        sql += " ORDER BY created DESC LIMIT 100"
        rows = [dict(r) for r in self.db.execute(sql, args)]
        for row in rows:
            row["metadata"] = json.loads(row["metadata"])
        return rows

    def complete_review(self, session: str, turn: str, report: dict, survey: bool = True) -> dict:
        if not isinstance(report, dict) or not str(report.get("expected", "")).strip() or not str(report.get("observed", "")).strip():
            raise ValueError("Report requires expected and observed outcomes")
        outcome = report.get("outcome")
        if outcome not in {"success", "partial", "blocked", "failed", "checkpoint"}:
            raise ValueError("Report outcome must be success, partial, blocked, failed or checkpoint")
        if len(json.dumps(report, ensure_ascii=False).encode()) > 24000:
            raise ValueError("Review exceeds 24 KiB")
        safe = redact_object(report)
        if outcome == "checkpoint":
            trigger_ids = sorted(e["id"] for e in self.pending(session) if e["turn"] == turn)
            key = str(report.get("checkpoint_id") or "checkpoint:" + hashlib.sha256(json.dumps(trigger_ids).encode()).hexdigest())
        else:
            key = "completion"
        identity = hashlib.sha256(json.dumps([session, turn, key]).encode()).hexdigest()[:24]
        with self.db:
            self.ensure_session(session)
            existing = self.db.execute("SELECT id FROM reviews WHERE id=?", (identity,)).fetchone()
            if not existing:
                self.db.execute("INSERT INTO reviews(id,session,turn,created,report,outcome,survey) VALUES(?,?,?,?,?,?,?)",
                                (identity, session, turn, time.time(), json.dumps(safe, ensure_ascii=False), outcome,
                                 "pending" if survey else "not_requested"))
                self.db.execute("UPDATE events SET reviewed=1 WHERE session=? AND turn=?", (session, turn))
                for event_id in report.get("event_ids", []):
                    self.db.execute("UPDATE events SET reviewed=1 WHERE session=? AND id=?", (session, str(event_id)))
                self.db.execute("UPDATE sessions SET checkpoint=?,tools=0 WHERE id=?", (time.time(), session))
        saved = self.db.execute("SELECT outcome,survey FROM reviews WHERE id=?", (identity,)).fetchone()
        return {"review_id": identity, "outcome": saved["outcome"], "survey": saved["survey"], "status": "already_recorded" if existing else "recorded"}

    def feedback(self, review_id: str, rating: str, comment: str = "") -> dict:
        if rating not in {"good", "issue", "skip"}:
            raise ValueError("Feedback rating must be good, issue or skip")
        with self.db:
            row = self.db.execute("SELECT session,turn FROM reviews WHERE id=?", (review_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown review")
            self.db.execute("UPDATE reviews SET survey=?,feedback=? WHERE id=?",
                            ("skipped" if rating == "skip" else "answered", json.dumps({"rating": rating, "comment": redact(comment)}), review_id))
            if rating == "issue":
                self.event(row["session"], row["turn"], "confirmed_user_feedback", str(uuid.uuid4()), {"review_id": review_id})
        return {"review_id": review_id, "status": "feedback_recorded", "rating": rating}

    def status(self) -> dict:
        return {"data_dir": str(self.data), "pending_events": self.pending(),
                "recent_reviews": [dict(r) for r in self.db.execute("SELECT id,session,turn,created,outcome,survey FROM reviews ORDER BY created DESC LIMIT 20")],
                "scope": policy(self.data)["roots"], "hook_trust": "Must be verified in the host; files on disk do not prove trust or loading."}
