"""Portable entry point, including real local verification with retained logs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent


def verify():
    directory = ROOT / "state/verification"
    directory.mkdir(parents=True, exist_ok=True)
    jobs = [("tests", ["-m", "unittest", "discover", "-s", "tests", "-v"])]
    cfg_path = ROOT / "config.json"
    scripts = []
    if cfg_path.exists():
        scripts = json.loads(cfg_path.read_text(encoding="utf-8")).get("verify_scripts", [])
    jobs += [(os.path.splitext(os.path.basename(s))[0], [s]) for s in scripts]
    records = []
    for label, args in jobs:
        log = directory / (label + ".log")
        start = time.time()
        with log.open("wb") as f:
            result = subprocess.run([sys.executable, "-B", "-X", "utf8", *args], cwd=ROOT,
                                    stdout=f, stderr=subprocess.STDOUT, timeout=180)
        record = {"check": label, "command": args, "exit_code": result.returncode,
                  "seconds": round(time.time() - start, 3), "log": str(log.relative_to(ROOT)),
                  "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest()}
        if args[0].endswith(".py"):
            record["script_sha256"] = hashlib.sha256((ROOT / args[0]).read_bytes()).hexdigest()
        records.append(record)
        manifest = {"executed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "python": sys.version, "checks": records,
                    "all_passed": len(records) == len(jobs) and all(r["exit_code"] == 0 for r in records)}
        (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(label + (": PASS" if result.returncode == 0 else ": FAIL"), flush=True)
        if result.returncode:
            print(log.read_text(encoding="utf-8", errors="replace")[-2500:])
            return result.returncode
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("route",
                        choices=("status", "check", "pilot", "pipeline", "auto", "verify"),
                        nargs="?", default="status",
                        help="auto=自主模式推进至全部任务终态；pipeline=interactive 推进 N 阶段")
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--mode", choices=("interactive", "autonomous"), default=None,
                        help="pipeline/auto 的运行模式（默认取 config.json policy.default_mode）")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.route == "verify":
        return verify()
    routes = {
        "status": ["orchestrator.py", "--status"],
        "check": ["run_research.py", "--check-models", "--prompt-keys"],
        "pilot": ["run_research.py", "--prompt-keys"],
        "pipeline": ["run_research.py", "--pipeline-steps", str(args.steps), "--prompt-keys"],
        "auto": ["orchestrator.py", "--auto"],
    }
    command = list(routes[args.route])
    if args.route in ("pipeline", "auto") and args.mode:
        command += ["--mode", args.mode]
    if not 1 <= args.steps <= 20:
        parser.error("--steps must be between 1 and 20")
    code = subprocess.call([sys.executable, "-X", "utf8", *command], cwd=ROOT)
    if args.route == "status":
        budget = json.loads((ROOT / "state/budget.json").read_text(encoding="utf-8"))
        print("External calls: {}/{}; per-call output cap: {}".format(
            budget["attempts"], budget["max_calls"], budget["max_output_tokens_per_call"]))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
