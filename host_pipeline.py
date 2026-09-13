"""Import host-reviewed phase artifacts through the existing state machine.

This is a trusted-host interface, not an evidence generator or model tool.
The caller must actually inspect the report and evidence before invoking it.
"""
import argparse
import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import orchestrator as o


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("id")
    parser.add_argument("phase", choices=o.BASE_PIPELINE[:-1])
    parser.add_argument("report", type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    cfg = o.load(o.ROOT / "config.json")
    ledger = o.load(o.STATE / "ledger.json")
    item = next(x for x in ledger["items"] if x["id"] == args.id)
    report = args.report.read_text(encoding="utf-8")
    if item["stage"] == "candidate" and args.phase == "statement":
        o.promote_candidate(ledger, args.id)
    if item["stage"] != args.phase:
        raise SystemExit("Phase mismatch: " + item["stage"])
    receipt = None
    if args.evidence:
        path = args.evidence.resolve()
        receipt = {
            "kind": "execution" if args.phase == "experiment" else "retrieval",
            "report_sha256": o.digest(report),
            "proof_sha256": o.proof_digest(item),
            "statement_sha256": o.digest(item["statement"]),
            "artifact": str(path.relative_to(o.ROOT)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if not o.evidence_valid(receipt, item, report, args.phase):
            raise SystemExit("Invalid evidence binding")
        receipt_path = o.STATE / "verification" / (args.id + "-" + args.phase + "-receipt.json")
        o.save(receipt_path, receipt)
    if args.phase in ("experiment", "lit-scan", "lit-check") and receipt is None:
        raise SystemExit("Host import requires a reviewed execution/retrieval record")
    o.finalize(item, args.phase, report, cfg, time.time(), evidence=receipt, ledger=ledger)
    o.save(o.STATE / "ledger.json", ledger)


if __name__ == "__main__":
    main()
