"""Validate source and export reproducible public plugin archives using stdlib."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = {"aar", "hooks", "scripts", "skills", "tests", "assets", "docs", ".codex-plugin"}
FILES = {"plugin.json", "metadata/portable-plugin.json", "mcp.json", ".mcp.json", "README.md", "README.ru.md",
         "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def inventory() -> dict[str, bytes]:
    selected = {}
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if relative.parts[0] not in DIRECTORIES and relative.as_posix() not in FILES:
            continue
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        require(not path.is_symlink(), "Symlink in package: " + relative.as_posix())
        if path.is_file():
            require(path.suffix not in {".sqlite", ".sqlite3", ".db", ".zip"},
                    "Runtime or generated artifact in source: " + relative.as_posix())
            require("backups" not in relative.parts and "state" not in relative.parts,
                    "Private state directory in source")
            selected[relative.as_posix()] = path.read_bytes()
    preserved = selected.pop("metadata/portable-plugin.json", None)
    if preserved is not None:
        require("plugin.json" not in selected or selected["plugin.json"] == preserved,
                "Conflicting portable manifests")
        selected.setdefault("plugin.json", preserved)
    return selected


def validate(selected: dict[str, bytes]) -> str:
    portable = json.loads(selected["plugin.json"])
    overlay = json.loads(selected[".codex-plugin/plugin.json"])
    version = portable["version"]
    require(re.fullmatch(r"\d+\.\d+\.\d+", version) is not None, "Invalid version")
    require(portable["name"] == overlay["name"] == "after-action-review", "Identity mismatch")
    require(overlay["version"] == version, "Manifest version mismatch")
    runtime_version = re.search(r'__version__\s*=\s*"([^"]+)"', selected["aar/__init__.py"].decode())
    require(runtime_version is not None and runtime_version.group(1) == version,
            "Runtime version mismatch")
    interface = portable["extensions"]["com.openai"]["interface"]
    require(interface == overlay["interface"], "Presentation mismatch")
    require(len(interface["shortDescription"]) <= 30, "Subtitle exceeds 30 characters")
    for field in ("logo", "composerIcon"):
        relative = interface[field]
        require(relative.startswith("./") and ".." not in Path(relative).parts,
                "Invalid icon path")
        data = selected[relative[2:]]
        require(data[:8] == b"\x89PNG\r\n\x1a\n", "Icon must be a PNG")
        require(data[12:16] == b"IHDR" and len(data) >= 33, "Invalid PNG header")
        width, height = struct.unpack(">II", data[16:24])
        require(48 <= width == height <= 4096, "Icon dimensions out of range")
        require(len(data) <= 5 * 1024 * 1024, "Icon exceeds 5 MiB")
        require(data[25] in {4, 6} or b"tRNS" in data, "Icon needs an alpha channel")
    for filename in ("mcp.json", ".mcp.json"):
        server = json.loads(selected[filename])["mcpServers"]["after_action_review"]
        require(server["cwd"] == "./" and server["args"] == ["scripts/aar.py", "mcp"],
                "Packaged MCP launch regression")
    pattern = re.compile(r'\[[^\]]*\]\(([^)]+)\)|(?:src|href)="([^"]+)"')
    for filename, content in selected.items():
        if not filename.endswith(".md"):
            continue
        for match in pattern.finditer(content.decode("utf-8")):
            link = next(value for value in match.groups() if value is not None)
            if link.startswith(("https://", "http://", "#", "mailto:")):
                continue
            target = link.split("#", 1)[0]
            require(not target.startswith("/"), "Absolute documentation link: " + filename)
            resolved = (ROOT / filename).parent.joinpath(target).resolve()
            require(resolved.is_relative_to(ROOT) and resolved.is_file(),
                    "Broken documentation link in " + filename + ": " + link)
    return version


def archive(output: Path, version: str, selected: dict[str, bytes], layout: str) -> dict:
    members = dict(selected)
    if layout == "codex":
        members["metadata/portable-plugin.json"] = members.pop("plugin.json")
    destination = output / f"after-action-review-{version}-{layout}.zip"
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for name, content in sorted(members.items()):
            item = zipfile.ZipInfo("after-action-review/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.create_system = 3
            item.external_attr = 0o100644 << 16
            handle.writestr(item, content)
    with zipfile.ZipFile(destination) as handle:
        require(handle.testzip() is None, "Archive CRC failure")
        require(len(handle.namelist()) == len(members), "Archive member mismatch")
        for name, content in members.items():
            require(handle.read("after-action-review/" + name) == content,
                    "Archive bytes mismatch: " + name)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    records = [{"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
               for name, content in sorted(members.items())]
    return {"filename": destination.name, "sha256": digest, "file_count": len(members), "files": records}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(ROOT):
        require(output != ROOT and output.relative_to(ROOT).parts[0] == "dist",
                "Use dist/ or an output directory outside the source tree")
    selected = inventory()
    version = validate(selected)
    output.mkdir(parents=True, exist_ok=True)
    archives = [archive(output, version, selected, layout) for layout in ("codex", "portable")]
    manifest = {"version": version, "archive_root": "after-action-review", "archives": archives,
                "excludes": ["private runtime state", "backups", "bytecode", "Git metadata", "research logs"]}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    sums = "".join(f"{item['sha256']}  {item['filename']}\n" for item in archives)
    (output / "SHA256SUMS").write_text(sums, encoding="utf-8")
    print(json.dumps({"status": "verified", "version": version, "output": str(output),
                      "archives": [{key: item[key] for key in ("filename", "sha256", "file_count")}
                                   for item in archives]}, indent=2))


if __name__ == "__main__":
    main()
