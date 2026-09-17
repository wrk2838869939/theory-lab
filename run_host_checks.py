"""Run explicitly selected, host-reviewed local Python checks and save evidence.

No model output is interpreted as code. Review selected scripts before use.
"""
import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("scripts", nargs="+", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite execution evidence")
    code = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ("experiments", "research") for p in (ROOT / folder).glob("*.py")}
    interpreter = [sys.executable, "-E"]  # Ignore PYTHONOPTIMIZE and other Python env overrides.
    probe = subprocess.check_output(interpreter + ["-c", "import sys; print(int(__debug__), sys.flags.optimize)"],
                                    cwd=ROOT, text=True).strip()
    if probe != "1 0":
        raise SystemExit("Research checks require enabled assertions")
    record = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "cwd": str(ROOT), "python": sys.version, "executable": sys.executable,
        "platform": platform.platform(), "seed": None,
        "assertion_probe": probe, "python_environment_overrides": "ignored via -E",
        "git_head": (subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
                     if (ROOT / ".git").exists() else None),
        "code_sha256": code, "runs": [],
    }
    for script in args.scripts:
        path = (ROOT / script).resolve()
        path.relative_to(ROOT)
        command = interpreter + [str(path.relative_to(ROOT))]
        started = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        record["runs"].append({"command": command, "returncode": result.returncode,
            "elapsed_seconds": time.monotonic() - started,
            "stdout": result.stdout, "stderr": result.stderr})
        if result.returncode:
            break
    record["code_unchanged"] = all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h
                                   for p, h in code.items())
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record["runs"], ensure_ascii=False, indent=2))
    return 0 if record["code_unchanged"] and all(r["returncode"] == 0 for r in record["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
