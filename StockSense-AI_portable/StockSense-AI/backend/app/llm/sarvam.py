"""Sarvam chat client (OpenAI-compatible) with JSON-output helpers.

sarvam-105b is a reasoning model: hidden thinking tokens count against max_tokens, so the limit is generous
and an empty reply (budget spent on thinking) is retried with a larger budget.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from typing import Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app import config

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


_local = threading.local()


def call_stats() -> list[dict]:
    """Per-thread log of LLM calls made since reset_stats(): seconds + token usage (for latency tuning)."""
    if not hasattr(_local, "calls"):
        _local.calls = []
    return _local.calls


def reset_stats() -> None:
    _local.calls = []


_THINK = re.compile(r"<think>.*?</think>", re.S)


def chat(messages: list[dict], max_tokens: int | None = None, temperature: float = 0.0, retries: int = 5,
         label: str = "", effort: str | None = None) -> str:
    if not config.LLM_API_KEY:
        raise LLMError("LLM API key missing: set llm_api_key in .env")
    budget = max_tokens or config.LLM_MAX_TOKENS
    last = ""
    for attempt in range(retries):
        try:
            body = {"model": config.LLM_MODEL, "messages": messages, "temperature": temperature, "max_tokens": budget}
            level = effort or config.LLM_REASONING_EFFORT
            body["reasoning_effort"] = None if level in ("off", "", None) else level  # explicit null = no thinking
            t0 = time.time()
            r = httpx.post(f"{config.LLM_BASE_URL}/chat/completions",
                           headers={"Authorization": f"Bearer {config.LLM_API_KEY}"}, json=body, timeout=180)
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(min(2 ** (attempt + 1), 20) + random.random())  # exponential backoff + jitter
                continue
            if r.status_code >= 400:
                raise LLMError(f"LLM HTTP {r.status_code}: {r.text[:300]}")
            data = r.json()
            usage = data.get("usage") or {}
            call_stats().append({"label": label, "seconds": round(time.time() - t0, 1),
                                 "completion_tokens": usage.get("completion_tokens"),
                                 "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")})
            choice = data["choices"][0]
            text = _THINK.sub("", choice["message"].get("content") or "").strip()
            if not text:  # thinking consumed the whole budget
                budget = min(budget * 2, 32000)
                last = "empty reply"
                continue
            return text
        except httpx.HTTPError as e:
            last = str(e)
            time.sleep(2 * (attempt + 1))
    raise LLMError(f"LLM call failed after {retries} attempts ({last})")


def _first_json_object(text: str) -> str | None:
    """Return the first balanced {...} block (handles code fences / prose around it)."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        start = text.find("{", start + 1)
    return None


def chat_json(messages: list[dict], model: Type[T], max_tokens: int | None = None, label: str = "") -> T:
    """Call the LLM expecting a JSON object matching `model`; one repair retry on invalid output."""
    msgs = list(messages)
    err = ""
    for _ in range(2):
        text = chat(msgs, max_tokens=max_tokens, label=label)
        blob = _first_json_object(text)
        try:
            if blob is None:
                raise ValueError("no JSON object found")
            return model.model_validate(json.loads(blob))
        except (ValueError, ValidationError) as e:
            err = str(e)[:300]
            msgs = msgs + [
                {"role": "assistant", "content": text[:1500]},
                {"role": "user", "content": f"Your reply was not valid. Problem: {err}\nReply again with ONLY the corrected JSON object."},
            ]
    raise LLMError(f"LLM did not return valid JSON: {err}")
