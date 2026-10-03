"""Generate ergonomic Python clients from captured endpoints with OpenAI."""
from __future__ import annotations

import json
import os
import re
from typing import Any

DEFAULT_MODEL = os.environ.get("MIMIC_OPENAI_MODEL", "gpt-6-astra")
MAX_DIGEST_CHARS = 250_000
_SENSITIVE_KEY = re.compile(
    r"(?:pass(?:word|wd)?|secret|token|api[_-]?key|authorization|cookie|session[_-]?id|refresh[_-]?token)",
    re.I,
)

PROMPT = """\
You are writing a Python API client. Below is real captured HTTP traffic from \
the app `{host}`, recorded by a proxy while the user exercised the app with \
their own account. Turn it into a clean, ergonomic client library.

Rules:
- Output ONE Python file, nothing else. No prose, no markdown fences.
- Subclass `mimic.App`. Set `HOST = "{host}"`. Auth/device headers are pulled \
automatically by the base class — do NOT hardcode tokens or headers.
- Give methods human names for what they DO (get_posts, like, send_message), \
not the raw path. Infer intent from paths, bodies, and status codes.
- Use self.get(path)/self.post(path, json=body). Both return parsed JSON.
- If an endpoint body reuses an id or token another endpoint returns, chain the \
calls or cache the dependency on the instance.
- Turn values that vary per call into method parameters. Keep stable structural \
values as defaults or instance state.
- Skip telemetry/analytics/config endpoints unless needed as a prerequisite.
- Add a one-line docstring per method. Keep the code tight and readable.
- Never reconstruct any value shown as `<redacted>`.

Captured endpoints for {host}:

{digest}
"""


def _redact_obj(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if _SENSITIVE_KEY.search(str(key)) else _redact_obj(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_obj(item) for item in value]
    return value


def redact_text(text: str) -> str:
    """Redact common credential fields while preserving body structure."""
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return re.sub(
            r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|cookie|"
            r"session[_-]?id|credential|signature)"
            r"(\s*[:=]\s*)([^&\r\n,;}]+)",
            r"\1\2<redacted>",
            text,
        )
    return json.dumps(_redact_obj(parsed), indent=2, ensure_ascii=False)


def build_digest(endpoints):
    """Render endpoint samples into a bounded, redacted block for the model."""
    parts = []
    used = 0
    for e in endpoints:
        block = [f"### {e['method']} {e['path']}  -> {e['status']}"]
        if e.get("query"):
            block.append(f"query: {redact_text(e['query'])}")
        if e.get("request_body"):
            block.append(f"request body:\n{redact_text(e['request_body'])}")
        if e.get("response_body"):
            block.append(f"response body:\n{redact_text(e['response_body'])}")
        rendered = "\n".join(block)
        extra = len(rendered) + 2
        if used + extra > MAX_DIGEST_CHARS:
            if not parts:
                rendered = (
                    rendered[:MAX_DIGEST_CHARS]
                    + "\n### … endpoint body truncated to stay within the prompt budget"
                )
                extra = len(rendered) + 2
            else:
                parts.append("### … additional endpoints omitted to stay within the prompt budget")
                break
        parts.append(rendered)
        used += extra
    return "\n\n".join(parts)


def build_prompt(host, endpoints):
    return PROMPT.format(host=host, digest=build_digest(endpoints))


def _client():
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("OpenAI SDK is missing; reinstall mimic-client") from exc
    return OpenAI(max_retries=3, timeout=180.0)


def generate(host, endpoints, model=None, client=None):
    """Generate Python source through OpenAI's Responses API."""
    client = client or _client()
    model = model or DEFAULT_MODEL
    prompt = build_prompt(host, endpoints)
    try:
        response = client.responses.create(
            model=model,
            input=prompt,
            instructions=(
                "Return only valid Python source code. Do not include Markdown fences, "
                "analysis, or explanations."
            ),
            store=False,
        )
    except Exception as exc:
        raise RuntimeError(f"OpenAI generation failed: {exc}") from exc

    text = getattr(response, "output_text", "") or ""
    if not text.strip():
        raise RuntimeError("OpenAI returned no generated source")
    return _strip_fences(text)


def _strip_fences(text):
    """Defensively remove Markdown fences if a model emits them anyway."""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S | re.I)
    return (m.group(1) if m else text).strip() + "\n"
