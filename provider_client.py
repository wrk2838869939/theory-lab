"""Bounded, credential-scoped provider access; no third-party dependencies."""
import contextlib
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
ALLOWED_BASES = {
    "OPENAI_API_KEY": {"https://api.openai.com/v1"},
    "ZHIPU_API_KEY": {"https://open.bigmodel.cn/api/paas/v4"},
    "KIMI_API_KEY": {"https://api.kimi.com/coding/v1"},
    "MOONSHOT_API_KEY": {"https://api.moonshot.cn/v1", "https://api.moonshot.ai/v1"},
}


class ProviderError(RuntimeError):
    pass


class BudgetExceeded(ProviderError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("Provider redirect refused; credentials were not forwarded")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@contextlib.contextmanager
def state_lock():
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "provider.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def reserve_call(agent, operation, output_limit):
    """Reserve before HTTP. Failed/uncertain requests count; restart cannot reset budget."""
    with state_lock():
        path = STATE / "budget.json"
        if not path.exists():
            raise BudgetExceeded("No approved budget file; configure state/budget.json first")
        budget = json.loads(path.read_text(encoding="utf-8"))
        if budget["attempts"] >= budget["max_calls"]:
            raise BudgetExceeded("Approved call budget exhausted")
        if not 0 <= output_limit <= budget["max_output_tokens_per_call"]:
            raise BudgetExceeded("Output token limit exceeds approved per-call budget")
        budget["attempts"] += 1
        call_id = uuid.uuid4().hex
        budget.setdefault("reservations", []).append({
            "id": call_id, "operation": operation, "agent": agent["name"],
            "model": agent.get("model"), "max_tokens": output_limit,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        })
        atomic_json(path, budget)
        return call_id


def usage_event(**event):
    with state_lock():
        with (STATE / "provider-events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **event},
                               ensure_ascii=False) + "\n")


def redact(text, key=""):
    if key:
        text = text.replace(key, "[REDACTED]")
    return re.sub(r"(?:sk-[A-Za-z0-9_*-]{8,}|[a-f0-9]{32}\.[A-Za-z0-9]{12,})", "[REDACTED]", text)


def read_stream(response):
    """Collect SSE final content, never treating reasoning tokens as the answer."""
    content, finish, usage, model, request_id = [], None, None, None, None
    for raw in response:
        line = raw.decode("utf-8").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        if not payload:
            continue
        chunk = json.loads(payload)
        if chunk.get("type") in ("response.completed", "response.incomplete", "response.failed"):
            result = chunk["response"]
            if result.get("error"):
                raise ProviderError("Response error: " + json.dumps(result["error"], ensure_ascii=False)[:1000])
            final_text = "".join(part.get("text", "")
                                 for item in result.get("output", []) if item.get("type") == "message"
                                 for part in item.get("content", []) if part.get("type") == "output_text")
            return {"model": result.get("model"), "id": result.get("id"), "usage": result.get("usage"),
                    "choices": [{"finish_reason": "stop" if result.get("status") == "completed" else "incomplete",
                                 "message": {"content": final_text}}]}
        if chunk.get("error"):
            raise ProviderError("Stream error: " + json.dumps(chunk["error"], ensure_ascii=False)[:1000])
        model = chunk.get("model", model)
        request_id = chunk.get("id", request_id)
        usage = chunk.get("usage") or usage
        for choice in chunk.get("choices", []):
            if choice.get("index", 0) != 0:
                continue
            text = choice.get("delta", {}).get("content")
            if isinstance(text, str):
                content.append(text)
            finish = choice.get("finish_reason") or finish
    return {"model": model, "id": request_id, "usage": usage,
            "choices": [{"finish_reason": finish, "message": {"content": "".join(content)}}]}


def request_json(agent, operation, body=None):
    key_name = agent.get("api_key_env", "")
    base = agent["base_url"].rstrip("/")
    if base not in ALLOWED_BASES.get(key_name, set()):
        raise ProviderError("Unapproved credential/endpoint pairing")
    key = os.environ.get(key_name, "")
    if not key:
        raise ProviderError("Missing environment variable " + key_name)
    if operation not in ("models", "chat/completions", "responses"):
        raise ProviderError("Unsupported operation")
    limit = body.get("max_output_tokens", body.get("max_tokens", 0)) if body else 0
    call_id = reserve_call(agent, operation, limit)
    req = urllib.request.Request(base + "/" + operation,
        data=json.dumps(body).encode("utf-8") if body else None,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key,
                 "User-Agent": "Theory-Lab/1.0"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=agent.get("timeout", 240)) as response:
            if body and body.get("stream"):
                data = read_stream(response)
            else:
                data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Keep a short sanitized provider message, never headers/request body.
        detail = redact(exc.read(4096).decode("utf-8", errors="replace"), key)[:700]
        usage_event(id=call_id, status="http_error", http_status=exc.code, detail=detail)
        raise ProviderError("HTTP {}: {}".format(exc.code, detail)) from None
    except Exception as exc:
        detail = redact(str(exc), key)[:400]
        usage_event(id=call_id, status="transport_error", detail=detail)
        raise ProviderError(detail) from None
    usage_event(id=call_id, status="response", model=data.get("model"),
                usage=data.get("usage"), request_id=data.get("id"))
    return data


def list_models(agent):
    data = request_json(agent, "models")
    return [entry["id"] for entry in data.get("data", []) if isinstance(entry, dict) and "id" in entry]


def chat_completion(agent, system, user):
    body = {"model": agent["model"], "max_tokens": agent.get("max_tokens", 8192),
            "stream": True,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    for field in ("temperature", "thinking", "reasoning_effort"):
        if field in agent:
            body[field] = agent[field]
    operation = "chat/completions"
    if agent.get("protocol") == "responses":
        operation = "responses"
        body = {"model": agent["model"], "instructions": system, "input": user,
                "reasoning": {"effort": agent.get("reasoning_effort", "high")},
                "max_output_tokens": agent.get("max_tokens", 8192), "stream": True, "store": False}
    data = request_json(agent, operation, body)
    choices = data.get("choices") or []
    if not choices:
        raise ProviderError("Provider returned no choices")
    choice = choices[0]
    content = choice.get("message", {}).get("content")
    if choice.get("finish_reason") not in ("stop", "end_turn"):
        # Preserve partial text for diagnosis, never promote it to a completed proof.
        path = STATE / "incomplete" / (uuid.uuid4().hex + ".json")
        atomic_json(path, {"finish_reason": choice.get("finish_reason"),
                          "content": redact(content or "", os.environ.get(agent["api_key_env"], "")),
                          "model": data.get("model"), "usage": data.get("usage")})
        raise ProviderError("Incomplete generation; saved diagnostic artifact " + str(path.relative_to(ROOT)))
    if not isinstance(content, str) or not content.strip():
        raise ProviderError("Empty final answer; reasoning text is not a completed artifact")
    return redact(content, os.environ.get(agent["api_key_env"], ""))
