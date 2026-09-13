#!/usr/bin/env python3
"""Offline regression tests. Receipts are test fixtures, never research evidence."""
import copy
import hashlib
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
import orchestrator as o


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mrlab-test-")
        self.root = Path(self.tmp.name)
        self.old_root, self.old_state = o.ROOT, o.STATE
        o.ROOT, o.STATE = self.root, self.root / "state"
        o.ensure_dirs()
        (self.root / "protocol/role.md").write_text("Test role", encoding="utf-8")
        self.cfg = {
            "policy": {"max_repair_rounds": 2, "max_statement_rounds": 2,
                       "max_paper_reviews": 2, "max_chars_per_artifact": 20},
            "agents": {"test": {"name": "offline-test", "backend": "api", "role_file": "protocol/role.md"}},
            "phase_agent": {phase: "test" for phase in o.BASE_PIPELINE + ["sketch", "rank", "paper_review"]},
        }

    def tearDown(self):
        o.ROOT, o.STATE = self.old_root, self.old_state
        self.tmp.cleanup()

    def item(self, **kwargs):
        item = {"id": "TEST", "stage": "candidate", "confidence": "white", "title": "Test theorem",
                "statement": "Precise statement", "refs": "", "repair_rounds": 0,
                "review_round": 0, "statement_round": 0, "notes": "", "history": []}
        item.update(kwargs)
        return item

    def run_phase(self, item, phase, response, evidence=None, ledger=None):
        return o.finalize(item, phase, response, self.cfg, time.time(), evidence=evidence, ledger=ledger)

    def receipt(self, item, phase, response):
        path = self.root / "state" / (phase + "-host-fixture.log")
        path.write_text("OFFLINE TEST FIXTURE ONLY: trusted bridge output", encoding="utf-8")
        return {"kind": "execution" if phase == "experiment" else "retrieval",
                "report_sha256": o.digest(response), "proof_sha256": o.proof_digest(item),
                "statement_sha256": o.digest(item["statement"]),
                "artifact": str(path.relative_to(self.root)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def verified(self):
        item = self.item(stage="derive")
        self.run_phase(item, "derive", "Full proof with tail PROOF_TAIL\nSTATUS: PROVED")
        self.run_phase(item, "verify", "Full adversarial review REVIEW_TAIL\nVERDICT: SOUND")
        response = "Experiment report EXECUTION_TAIL\nEXPERIMENT: CONSISTENT"
        self.run_phase(item, "experiment", response, self.receipt(item, "experiment", response))
        response = "Source checks CITATION_TAIL\nNOVELTY: CLEAR"
        self.run_phase(item, "lit-check", response, self.receipt(item, "lit-check", response))
        self.assertTrue(o.verification_ready(item))
        self.assertEqual(item["stage"], "integrate")
        return item

    def paper(self):
        return "\\documentclass{article}\n\\begin{document}\n" + "Long text " * 20 + "PAPER_TAIL\n\\end{document}\n"

    def test_markers_require_final_line_and_real_enum_case(self):
        for text in ("VERDICT: SOUND\nFurther caveat", "```\nVERDICT: SOUND", "VERDICT: sound",
                     " VERDICT: SOUND", "VERDICT： SOUND", "VERDICT: SOUND\n```", ""):
            self.assertIsNone(o.parse_marker(text, "VERDICT"), text)
        self.assertEqual(o.parse_marker("VERDICT: GAP\nFinal reasoning\nVERDICT: SOUND\n\n", "VERDICT"), "SOUND")
        self.assertEqual(o.parse_marker("```text\nVERDICT: GAP\n```\nVERDICT: SOUND", "VERDICT"), "SOUND")

    def test_statement_revision_and_missing_revision(self):
        item = self.item()
        self.run_phase(item, "statement", "<revised-statement>S2</revised-statement>\nFIDELITY: REVISE")
        self.assertEqual((item["statement"], item["stage"], item["statement_round"]), ("S2", "statement", 1))
        self.run_phase(item, "statement", "Missing replacement\nFIDELITY: REVISE")
        self.assertEqual(item["stage"], "blocked")

    def test_unretrieved_clear_is_unknown_but_prescan_can_continue(self):
        item = self.item()
        self.run_phase(item, "lit-scan", "I searched the internet and verified everything\nNOVELTY: CLEAR")
        self.assertEqual((item["stage"], item["novelty_status"]), ("derive", "UNKNOWN"))

    def test_conflict_and_refutation_remain_red(self):
        item = self.item()
        self.run_phase(item, "lit-scan", "Known duplicate\nNOVELTY: CONFLICT")
        self.assertEqual((item["stage"], item["confidence"]), ("blocked", "red"))
        self.run_phase(item, "verify", "Counterexample\nVERDICT: REFUTED")
        self.assertEqual((item["stage"], item["confidence"]), ("refuted", "red"))

    def test_repair_limit(self):
        item = self.item(repair_rounds=2)
        self.run_phase(item, "verify", "Bad proof\nVERDICT: UNSOUND")
        self.assertEqual((item["stage"], item["confidence"]), ("blocked", "red"))

    def test_simulated_experiment_cannot_become_execution(self):
        for result in ("CONSISTENT", "NOT-APPLICABLE"):
            item = self.item(stage="experiment")
            self.run_phase(item, "experiment", "I ran this code, all tests pass\nEXPERIMENT: " + result)
            self.assertEqual(item["experiment_status"], "UNEXECUTED")
            self.assertFalse(o.verification_ready(item))

    def test_unknown_cannot_integrate_or_replace_paper(self):
        item = self.item(stage="lit-check")
        self.run_phase(item, "lit-check", "Not checked\nNOVELTY: UNKNOWN")
        self.assertEqual(item["stage"], "blocked")
        path = self.root / "paper/paper.tex"
        path.write_text("Existing paper", encoding="utf-8")
        self.run_phase(item, "integrate", self.paper() + "STATUS: UPDATED")
        self.assertEqual(path.read_text(encoding="utf-8"), "Existing paper")
        self.assertNotEqual(item["stage"], "done")

    def test_host_evidence_allows_integration_and_strips_marker(self):
        item = self.verified()
        self.run_phase(item, "integrate", self.paper() + "STATUS: UPDATED")
        self.assertEqual((item["stage"], item["confidence"]), ("done", "green"))
        self.assertEqual((self.root / "paper/paper.tex").read_text(encoding="utf-8"), self.paper())

    def test_receipts_bind_response_proof_statement_and_log(self):
        item = self.verified()
        experiment = item["checks"]["experiment"]
        text = (self.root / experiment["artifact"]).read_text(encoding="utf-8")
        receipt = experiment["evidence"]
        self.assertTrue(o.evidence_valid(receipt, item, text, "experiment"))
        self.assertFalse(o.evidence_valid(receipt, item, text + " edited", "experiment"))
        for field in ("proof_sha256", "statement_sha256", "sha256", "kind"):
            self.assertFalse(o.evidence_valid(dict(receipt, **{field: "wrong"}), item, text, "experiment"), field)
        item["statement"] += " drift"
        self.assertFalse(o.verification_ready(item))

    def test_edited_proof_or_evidence_invalidates_green(self):
        item = self.verified()
        (self.root / "derivations/TEST.md").write_text("Changed proof", encoding="utf-8")
        self.assertFalse(o.verification_ready(item))
        item = self.verified()
        evidence = item["checks"]["lit-check"]["evidence"]
        (self.root / evidence["artifact"]).write_text("Changed log", encoding="utf-8")
        self.assertFalse(o.verification_ready(item))

    def test_legacy_green_and_partial_proof_are_not_verified(self):
        self.assertFalse(o.verification_ready(self.item(stage="done", confidence="green", novelty_status="CLEAR")))
        item = self.verified()
        self.run_phase(item, "derive", "Known gap\nSTATUS: PARTIAL")
        self.assertFalse(o.verification_ready(item))
        self.assertNotIn("experiment", item["checks"])

    def test_history_versions_survive_repair_and_invalid_outputs(self):
        item = self.item(stage="derive")
        self.run_phase(item, "derive", "Old proof\nSTATUS: PROVED")
        old = self.root / item["history"][-1]["artifact"]
        self.run_phase(item, "derive", "New proof\nSTATUS: PARTIAL")
        self.assertEqual(old.read_text(encoding="utf-8"), "Old proof\nSTATUS: PROVED")
        self.run_phase(item, "derive", "STATUS: PROVED\nUnfinished response")
        self.assertEqual((self.root / "derivations/TEST.md").read_text(encoding="utf-8"), "New proof\nSTATUS: PARTIAL")
        self.assertEqual(len({h["artifact"] for h in item["history"]}), 3)

    def test_invalid_complete_marker_cannot_replace_paper(self):
        item = self.verified()
        path = self.root / "paper/paper.tex"
        path.write_text("Keep this", encoding="utf-8")
        self.run_phase(item, "integrate", "Not a full paper\nSTATUS: UPDATED")
        self.assertEqual(path.read_text(encoding="utf-8"), "Keep this")

    def test_integration_only_injects_checked_items_and_complete_proof(self):
        item = self.verified()
        unverified = self.item(id="OTHER", title="UNVERIFIED_TITLE", statement="UNVERIFIED_STATEMENT")
        (self.root / "derivations/OTHER.md").write_text("UNVERIFIED_PROOF", encoding="utf-8")
        prompt = o.build_user_prompt(item, "integrate", {"items": [item, unverified]}, self.cfg)
        self.assertIn("PROOF_TAIL", prompt)
        self.assertIn("CITATION_TAIL", prompt)
        for bad in ("UNVERIFIED_PROOF", "UNVERIFIED_TITLE", "UNVERIFIED_STATEMENT"):
            self.assertNotIn(bad, prompt)

    def test_paper_review_gets_entire_paper_proof_reviews_and_receipts(self):
        item = self.verified()
        self.run_phase(item, "integrate", self.paper() + "STATUS: UPDATED")
        ledger = {"items": [item], "meta": {}}
        with patch.object(o, "call_agent", return_value="Review\nPAPER: PASS") as call:
            self.assertTrue(o.paper_step(self.cfg, ledger, False))
        for required in ("PAPER_TAIL", "PROOF_TAIL", "REVIEW_TAIL", "EXECUTION_TAIL", "CITATION_TAIL", "OFFLINE TEST FIXTURE ONLY"):
            self.assertIn(required, call.call_args.args[2])
        self.assertEqual(ledger["meta"]["paper_gate"], "PASS")

    def test_paper_gate_rejects_unfinished_or_legacy_items(self):
        (self.root / "paper/paper.tex").write_text(self.paper(), encoding="utf-8")
        for state in ("blocked", "refuted", "candidate", "done"):
            with patch.object(o, "call_agent") as call:
                self.assertFalse(o.paper_step(self.cfg, {"items": [self.item(stage=state, confidence="green")]}, False))
                call.assert_not_called()

    def test_invalid_paper_review_does_not_pass_or_create_fix(self):
        item = self.verified()
        self.run_phase(item, "integrate", self.paper() + "STATUS: UPDATED")
        ledger = {"items": [item], "meta": {}}
        with patch.object(o, "call_agent", return_value="PAPER: PASS\nBut proof is unexamined"):
            self.assertFalse(o.paper_step(self.cfg, ledger, False))
        self.assertNotIn("paper_gate", ledger["meta"])
        self.assertEqual(len(ledger["items"]), 1)

    def test_dry_run_does_not_change_ledger_or_log(self):
        ledger = {"items": [self.item()]}
        before = copy.deepcopy(ledger)
        with patch.object(o, "call_agent", return_value=None):
            self.assertFalse(o.step(self.cfg, ledger, dry=True))
        self.assertEqual(ledger, before)
        self.assertFalse((self.root / "state/events.jsonl").exists())

    def test_explicit_target_is_selected_even_if_another_is_active(self):
        a, b = self.item(id="A", stage="statement"), self.item(id="B")
        with patch.object(o, "call_agent", return_value="Review\nFIDELITY: PASS"):
            self.assertTrue(o.step(self.cfg, {"items": [a, b]}, force_id="B"))
        self.assertEqual((a["stage"], b["stage"]), ("statement", "lit-scan"))

    def test_cli_direct_paper_write_is_rolled_back_on_invalid_result(self):
        item = self.verified()
        path = self.root / "paper/paper.tex"
        path.write_text("Keep paper", encoding="utf-8")
        def direct_write(*args, **kwargs):
            path.write_text("Bypass", encoding="utf-8")
            return "Incomplete\nSTATUS: UPDATED"
        with patch.object(o, "call_agent", side_effect=direct_write):
            self.assertFalse(o.step(self.cfg, {"items": [item]}))
        self.assertEqual(path.read_text(encoding="utf-8"), "Keep paper")

    def test_sketch_failure_preserves_indices_and_resumes_missing_only(self):
        item = self.item(stage="sketch", tournament=True, n_candidates=3)
        ledger = {"items": [item]}
        with patch.object(o, "call_agent", side_effect=["k1\nSTATUS: READY", None, "k3\nSTATUS: READY"]):
            self.assertFalse(o.sketch_step(self.cfg, ledger, item, False))
        self.assertEqual(item["sketch_ids"], [1, 3])
        self.assertEqual(item["stage"], "sketch")
        with patch.object(o, "call_agent", return_value="k2\nSTATUS: READY") as call:
            self.assertTrue(o.sketch_step(self.cfg, ledger, item, False))
            self.assertEqual(call.call_count, 1)
        self.assertEqual(item["sketch_ids"], [1, 2, 3])
        self.assertEqual(item["stage"], "rank")


if __name__ == "__main__":
    unittest.main()
