"""Budgeted theorem review using run_research.run_one/provider_client only."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from run_research import run_one


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("id")
    parser.add_argument("reviewer", choices=("T2", "ALT"))
    args = parser.parse_args()
    cfg = json.loads((ROOT / "config.json").read_text())
    ledger = json.loads((ROOT / "state/ledger.json").read_text())
    item = next(x for x in ledger["items"] if x["id"] == args.id)
    if item["stage"] != "verify":
        raise SystemExit("The theorem must be at verify")
    task = (ROOT / "protocol/T2-referee.md").read_text()
    task += "\n请审查以下完整陈述和证明，至少列三项实际攻击及推导，核对每个结论。"
    task += "只做数学审稿；没有实验或检索工具，不能自报执行或新颖性。"
    task += "末行必须是 VERDICT: SOUND 或 VERDICT: GAP 或 VERDICT: UNSOUND 或 VERDICT: REFUTED。\n"
    task += "<statement>\n" + item["statement"] + "\n</statement>\n"
    task += "<untrusted-proof>\n" + (ROOT / "derivations" / (args.id + ".md")).read_text() + "\n</untrusted-proof>\n"
    label = args.id + "-" + args.reviewer.lower() + "-review-r" + str(item.get("review_round", 0) + 1)
    result = run_one(label, cfg["agents"][args.reviewer], task)
    return 0 if result else 2


if __name__ == "__main__":
    raise SystemExit(main())
