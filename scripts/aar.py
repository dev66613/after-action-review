#!/usr/bin/env python3
"""Portable CLI and MCP entry point. Run `python scripts/aar.py --help`."""
from pathlib import Path
import argparse
import json
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aar.runtime import Store, data_path, defaults, policy, private_dir
from aar.service import dispatch, serve


def main():
    parser = argparse.ArgumentParser(description="After Action Review, journal and guarded rollback")
    parser.add_argument("--data-dir", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "doctor", "changes", "recover", "mcp"):
        sub.add_parser(command)
    for command in ("review", "feedback", "propose"):
        p = sub.add_parser(command)
        p.add_argument("--input", type=Path, help="UTF-8 JSON input; stdin when omitted")
    p = sub.add_parser("apply")
    p.add_argument("change_id")
    p.add_argument("--approved", action="store_true")
    p.add_argument("--user-decision", default="")
    p = sub.add_parser("rollback")
    p.add_argument("change_id")
    p = sub.add_parser("configure")
    p.add_argument("--root", type=Path, action="append", default=[])
    p.add_argument("--auto", action="store_true", help="Authorize safe kinds in specified owned roots")
    p.add_argument("--surveys", choices=["on", "off"])
    p.add_argument("--automatic", choices=["on", "off"])
    args = parser.parse_args()
    if args.data_dir:
        os.environ["AAR_DATA_DIR"] = str(args.data_dir.absolute())
    data = data_path()
    if args.command == "mcp":
        serve()
        return
    if args.command == "configure":
        private_dir(data)
        current = policy(data)
        for path in args.root:
            if not path.is_absolute() or not path.is_dir() or path.is_symlink():
                raise ValueError("Registered roots must be existing absolute owned directories without symlinks")
            candidate = {"path": str(path), "auto": args.auto, "kinds": ["skill", "prompt", "workflow", "workspace_doc", "config", "hook", "tool", "workspace_code"]}
            current["roots"] = [r for r in current["roots"] if r["path"] != str(path)] + [candidate]
        if args.surveys:
            current["survey_enabled"] = args.surveys == "on"
        if args.automatic:
            current["auto_apply"] = args.automatic == "on"
        path = data / "policy.json"
        if path.is_symlink():
            raise ValueError("Policy cannot be symlinked")
        if path.exists():
            backup = data / "policy.previous.json"
            backup.write_bytes(path.read_bytes())
            if os.name != "nt":
                backup.chmod(0o600)
        temp = data / "policy.pending.json"
        if temp.exists() or temp.is_symlink():
            raise ValueError("Resolve existing policy.pending.json before configuring")
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        result = {"status": "configured", "data_dir": str(data), "policy": current, "note": "Configuration records explicit scope; hook trust is separate and must be reviewed in the host."}
    elif args.command == "doctor":
        store = Store(data)
        try:
            from aar.engine import Engine
            result = {"status": "healthy", "python": sys.version.split()[0], "data_dir": str(data),
                      "auto_apply": policy(data)["auto_apply"], "survey_enabled": policy(data)["survey_enabled"],
                      "recovery": Engine(data, policy(data)).recover(),
                      "hook_trust": "not certified by CLI; review /hooks in the host", "cloud": "requires separately connected execution host"}
        finally:
            store.close()
    else:
        if args.command in {"review", "feedback", "propose"}:
            text = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read(1_048_577)
            if len(text.encode()) > 1_048_576:
                raise ValueError("Input exceeds limit")
            values = json.loads(text)
            if args.command == "propose" and "proposal" not in values:
                values = {"proposal": values}
        elif args.command in {"apply", "rollback"}:
            values = {"change_id": args.change_id}
            if args.command == "apply":
                values.update(approved=args.approved, user_decision=args.user_decision)
        else:
            values = {}
        result = dispatch("aar_" + args.command, values, data)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        if os.name == "nt":
            from windows_bridge import bridge
            raise SystemExit(bridge(sys.argv[1:]))
        main()
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)[:500]}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
