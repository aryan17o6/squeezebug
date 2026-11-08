"""Optional LLM explanations, through pluggable providers.

Nothing in squeezebug *needs* a model: shrinking and test generation are fully
deterministic and offline. A provider only adds a short natural-language
diagnosis of the minimised failure. Providers use the standard library's
``urllib`` so no SDK is required.

Selection (``--provider auto``):

1. ``gemini`` if ``GEMINI_API_KEY`` is set (model: ``SQUEEZEBUG_GEMINI_MODEL``,
   default ``gemini-2.5-flash``).
2. ``ollama`` if a local server answers at ``OLLAMA_HOST``
   (default ``http://localhost:11434``; model: ``SQUEEZEBUG_OLLAMA_MODEL``,
   default ``llama3.2``).
3. otherwise no provider, and explanations are skipped with a notice.
"""

from __future__ import annotations

import inspect
import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Optional, Protocol

from .shrink import ShrinkResult


class LLMError(RuntimeError):
    """A provider was reachable but the request failed."""


class Provider(Protocol):
    """Minimal interface every provider implements."""

    name: str

    def complete(self, prompt: str) -> str:
        """Return the model's text answer to ``prompt``."""
        ...


def _post_json(url: str, payload: dict[str, Any], timeout: float, headers: Optional[dict[str, str]] = None) -> Any:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed schemes
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LLMError(f"request to {url.split('?')[0]} failed: {exc}") from exc


class GeminiProvider:
    """Google Gemini via the public ``generateContent`` REST endpoint."""

    name = "gemini"

    def __init__(self, api_key: str, model: Optional[str] = None, timeout: float = 30.0) -> None:
        self.api_key = api_key
        self.model = model or os.environ.get("SQUEEZEBUG_GEMINI_MODEL", "gemini-2.5-flash")
        self.timeout = timeout

    def complete(self, prompt: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}}
        data = _post_json(url, body, self.timeout, headers={"x-goog-api-key": self.api_key})
        try:
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected Gemini response: {str(data)[:200]}") from exc


class OllamaProvider:
    """A local Ollama server (``/api/generate``)."""

    name = "ollama"

    def __init__(self, host: Optional[str] = None, model: Optional[str] = None, timeout: float = 120.0) -> None:
        self.host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        self.model = model or os.environ.get("SQUEEZEBUG_OLLAMA_MODEL", "llama3.2")
        self.timeout = timeout

    def available(self) -> bool:
        """Return True when the server answers quickly."""
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=0.5):  # noqa: S310
                return True
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def complete(self, prompt: str) -> str:
        data = _post_json(
            f"{self.host}/api/generate",
            {"model": self.model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}},
            self.timeout,
        )
        if not isinstance(data, dict) or "response" not in data:
            raise LLMError(f"unexpected Ollama response: {str(data)[:200]}")
        return str(data["response"]).strip()


def get_provider(name: str = "auto") -> Optional[Provider]:
    """Return a provider by name, or None when none is usable.

    Args:
        name: ``"auto"``, ``"gemini"``, ``"ollama"`` or ``"none"``.
    """
    name = name.lower()
    if name == "none":
        return None
    key = os.environ.get("GEMINI_API_KEY")
    if name in ("auto", "gemini") and key:
        return GeminiProvider(key)
    if name == "gemini":
        return None
    if name in ("auto", "ollama"):
        ollama = OllamaProvider()
        if ollama.available():
            return ollama
    return None


def build_prompt(result: ShrinkResult, func: Callable[..., Any], traceback_text: str = "") -> str:
    """Compose the diagnosis prompt from the minimal repro and the source."""
    try:
        source = inspect.getsource(func)
    except (OSError, TypeError):
        source = "<source unavailable>"
    if len(source) > 6000:
        source = source[:6000] + "\n# ... truncated ..."
    call = result.minimized.as_tree()
    return (
        "You are helping a Python developer fix a bug. A test-case reducer shrank a real "
        "failing input to a minimal one that still raises the same exception at the same line.\n\n"
        f"Function source:\n```python\n{source}\n```\n\n"
        f"Minimal failing call arguments (args/kwargs): {call!r}\n"
        f"Failure: {result.final_signature.short()}\n"
        + (f"\nTraceback of the original failure:\n{traceback_text[-2000:]}\n" if traceback_text else "")
        + "\nIn at most 6 short lines: say what property of the input triggers the bug, "
        "why the code fails on it, and suggest a concrete fix. No preamble."
    )


def explain(result: ShrinkResult, func: Callable[..., Any], provider: Optional[Provider], traceback_text: str = "") -> str:
    """Ask ``provider`` to explain ``result``; return "" when no provider is set."""
    if provider is None:
        return ""
    return provider.complete(build_prompt(result, func, traceback_text))
