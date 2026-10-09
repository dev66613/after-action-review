"""Windows host bridge into its existing WSL executor; no service or trust setup."""
from pathlib import Path, PureWindowsPath
import os
import subprocess
import sys


def wsl_path(path: str) -> str:
    p = PureWindowsPath(path)
    if p.drive.endswith(":"):
        return "/mnt/" + p.drive[0].lower() + "/" + "/".join(p.parts[1:])
    parts = path.replace("\\", "/").split("/")
    if len(parts) > 4 and parts[2].lower() in {"wsl.localhost", "wsl$"}:
        return "/" + "/".join(parts[4:])
    raise ValueError("Plugin must be on a local Windows drive or WSL share")


def bridge(args: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    is_hook = bool(args and args[0] == "hook")
    script = root / ("hooks/handler.py" if is_hook else "scripts/aar.py")
    distro = os.environ.get("AAR_WSL_DISTRO", "Ubuntu")
    # Propagate only optional explicit AAR location; do not copy auth/config.
    command = ["wsl.exe", "-d", distro, "--exec"]
    locations = []
    if os.environ.get("AAR_DATA_DIR"):
        location = os.environ["AAR_DATA_DIR"]
        if PureWindowsPath(location).is_absolute():
            location = wsl_path(location)
        locations = ["env", "AAR_DATA_DIR=" + location]
    command += locations + ["python3", wsl_path(str(script))]
    forwarded = list(args)
    for index in range(1, len(forwarded)):
        if forwarded[index-1] in {"--data-dir", "--root", "--input"} and PureWindowsPath(forwarded[index]).is_absolute():
            forwarded[index] = wsl_path(forwarded[index])
    command += [] if is_hook else forwarded
    result = subprocess.run(command, stdin=sys.stdin.buffer, stdout=sys.stdout.buffer,
                            stderr=sys.stderr.buffer, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(bridge(sys.argv[1:] or ["mcp"]))
