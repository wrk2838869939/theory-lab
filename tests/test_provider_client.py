import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import provider_client as pc


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state_patch = patch.object(pc, "STATE", self.root)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.addCleanup(self.tmp.cleanup)
        pc.atomic_json(self.root / "budget.json", {"attempts": 0, "max_calls": 2,
                      "max_output_tokens_per_call": 8192})
        self.agent = {"name": "test", "model": "test-model", "max_tokens": 8192,
                      "base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key_env": "ZHIPU_API_KEY"}

    def test_budget_survives_restart_and_rejects_extra_output(self):
        pc.reserve_call(self.agent, "models", 0)
        with self.assertRaises(pc.BudgetExceeded):
            pc.reserve_call(self.agent, "chat/completions", 8193)
        pc.reserve_call(self.agent, "chat/completions", 8192)
        with self.assertRaises(pc.BudgetExceeded):
            pc.reserve_call(self.agent, "models", 0)

    def test_secret_cannot_be_sent_to_another_provider(self):
        self.agent["base_url"] = "https://api.kimi.com/coding/v1"
        with self.assertRaises(pc.ProviderError):
            pc.list_models(self.agent)
        self.assertEqual(json.loads((self.root / "budget.json").read_text())["attempts"], 0)

    def test_truncated_output_never_returned_as_proof(self):
        response = {"choices": [{"finish_reason": "length", "message": {"content": "STATUS: PROVED"}}]}
        with patch.object(pc, "ROOT", self.root), patch.object(pc, "request_json", return_value=response):
            with self.assertRaisesRegex(pc.ProviderError, "Incomplete"):
                pc.chat_completion(self.agent, "system", "task")
        self.assertEqual(len(list((self.root / "incomplete").glob("*.json"))), 1)

    def test_error_redaction_and_failed_attempt_accounting(self):
        error = urllib.error.HTTPError("https://open.bigmodel.cn", 401, "failed", {},
                                      io.BytesIO(b'{"error":"example-test-credential"}'))
        with patch.dict(os.environ, ZHIPU_API_KEY="example-test-credential"), \
             patch("urllib.request.OpenerDirector.open", side_effect=error):
            with self.assertRaises(pc.ProviderError):
                pc.list_models(self.agent)
        self.assertNotIn("example-test-credential", (self.root / "provider-events.jsonl").read_text())
        self.assertEqual(json.loads((self.root / "budget.json").read_text())["attempts"], 1)

    def test_redirect_refused(self):
        with self.assertRaises(pc.ProviderError):
            pc.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://example.org")

    def test_stream_preserves_finish_and_ignores_reasoning(self):
        chunks = [
            {"choices": [{"delta": {"reasoning_content": "private reasoning"}}]},
            {"choices": [{"delta": {"content": "answer"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"total_tokens": 5}},
        ]
        stream = io.BytesIO(("\n".join("data: " + json.dumps(chunk) for chunk in chunks) +
                             "\ndata: [DONE]\n").encode())
        data = pc.read_stream(stream)
        self.assertEqual(data["choices"][0]["message"]["content"], "answer")
        self.assertEqual(data["choices"][0]["finish_reason"], "stop")
        self.assertEqual(data["usage"]["total_tokens"], 5)

    def test_responses_completed_stream_and_budget_field(self):
        event = {"type": "response.completed", "response": {"status": "completed", "model": "test-model-resp",
                 "output": [{"type": "reasoning"}, {"type": "message", "content": [
                     {"type": "output_text", "text": "proof"}]}], "usage": {"output_tokens": 7}}}
        data = pc.read_stream(io.BytesIO(("data: " + json.dumps(event) + "\n").encode()))
        self.assertEqual(data["choices"][0]["message"]["content"], "proof")
        agent = {**self.agent, "protocol": "responses", "model": "test-model-resp",
                 "base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"}
        with patch.object(pc, "request_json", return_value=data) as request:
            self.assertEqual(pc.chat_completion(agent, "sys", "task"), "proof")
            self.assertEqual(request.call_args.args[1], "responses")
            body = request.call_args.args[2]
            self.assertEqual(body["max_output_tokens"], 8192)
            self.assertFalse(body["store"])

    def test_incomplete_responses_event_cannot_pass(self):
        event = {"type": "response.incomplete", "response": {"status": "incomplete", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "STATUS: PROVED"}]}]}}
        data = pc.read_stream(io.BytesIO(("data: " + json.dumps(event) + "\n").encode()))
        self.assertEqual(data["choices"][0]["finish_reason"], "incomplete")


if __name__ == "__main__":
    unittest.main()
