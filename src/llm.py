"""
llm.py — Provider-agnostic LLM interface
Fallback chain: Gemini → Groq → OpenRouter → Offline
"""
from __future__ import annotations
import json
import os
import re
import time
from pathlib import Path
from typing import Optional, Any

from src.utils import get_logger, load_settings, ROOT

log = get_logger("llm")
PROMPTS_DIR = ROOT / "prompts"


class LLMResponse:
    def __init__(self, text: str, provider: str, tokens_used: int = 0,
                 latency_ms: int = 0):
        self.text = text
        self.provider = provider
        self.tokens_used = tokens_used
        self.latency_ms = latency_ms

    def as_json(self) -> dict:
        """Parse JSON from response text, stripping markdown fences."""
        text = self.text.strip()
        # Remove ```json ... ``` fences
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text)


class LLMProvider:
    """Base class for LLM providers."""

    def __init__(self, name: str, model: str, env_key: str, budget: int):
        self.name = name
        self.model = model
        self.env_key = env_key
        self.budget = budget
        self._calls_today = 0

    @property
    def api_key(self) -> Optional[str]:
        return os.environ.get(self.env_key)

    @property
    def available(self) -> bool:
        return bool(self.api_key) and self._calls_today < self.budget

    def call(self, prompt: str, temperature: float = 0.7,
             system: str = "") -> LLMResponse:
        raise NotImplementedError


class GeminiProvider(LLMProvider):
    def call(self, prompt: str, temperature: float = 0.7,
             system: str = "") -> LLMResponse:
        import google.generativeai as genai
        genai.configure(api_key=self.api_key)
        model = genai.GenerativeModel(
            self.model,
            system_instruction=system if system else None,
        )
        t0 = time.monotonic()
        full_prompt = f"{system}\n\n{prompt}" if system and not model._system_instruction else prompt
        resp = model.generate_content(
            full_prompt,
            generation_config={"temperature": temperature, "max_output_tokens": 2000},
        )
        ms = int((time.monotonic() - t0) * 1000)
        self._calls_today += 1
        return LLMResponse(
            text=resp.text,
            provider=self.name,
            tokens_used=getattr(resp.usage_metadata, "total_token_count", 0),
            latency_ms=ms,
        )


class GroqProvider(LLMProvider):
    def call(self, prompt: str, temperature: float = 0.7,
             system: str = "") -> LLMResponse:
        from groq import Groq
        client = Groq(api_key=self.api_key)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        t0 = time.monotonic()
        resp = client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=2000,
        )
        ms = int((time.monotonic() - t0) * 1000)
        self._calls_today += 1
        return LLMResponse(
            text=resp.choices[0].message.content,
            provider=self.name,
            tokens_used=resp.usage.total_tokens,
            latency_ms=ms,
        )


class OpenRouterProvider(LLMProvider):
    def call(self, prompt: str, temperature: float = 0.7,
             system: str = "") -> LLMResponse:
        import requests
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        t0 = time.monotonic()
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"model": self.model, "messages": messages,
                  "temperature": temperature, "max_tokens": 2000},
            timeout=30,
        )
        resp.raise_for_status()
        ms = int((time.monotonic() - t0) * 1000)
        data = resp.json()
        self._calls_today += 1
        return LLMResponse(
            text=data["choices"][0]["message"]["content"],
            provider=self.name,
            tokens_used=data.get("usage", {}).get("total_tokens", 0),
            latency_ms=ms,
        )


class OfflineProvider(LLMProvider):
    """Template-based fallback — no API needed."""

    def __init__(self):
        super().__init__("offline", "template_fallback", "", 9999)

    @property
    def available(self) -> bool:
        return True

    def call(self, prompt: str, temperature: float = 0.7,
             system: str = "") -> LLMResponse:
        from src.utils import load_themes, load_voice_examples
        import random

        # Return a template-based caption from voice_examples
        examples = load_voice_examples()
        lang = "hinglish"
        for key in examples:
            if key.lower() in prompt.lower():
                lang = key
                break
        pool = examples.get(lang, examples.get("hinglish", []))
        text = random.choice(pool) if pool else "Wear what you think. Shop at gyodify.shop"
        log.warning("Using offline fallback — no LLM provider available")
        return LLMResponse(text=text, provider="offline", tokens_used=0, latency_ms=0)


class LLMClient:
    """Orchestrates the provider fallback chain."""

    def __init__(self):
        cfg = load_settings()["llm"]
        self._providers: list[LLMProvider] = []

        for entry in cfg["chain"]:
            p = entry["provider"]
            if p == "gemini":
                self._providers.append(GeminiProvider(
                    p, entry["model"], entry["env_key"],
                    entry.get("daily_request_budget", 40)
                ))
            elif p == "groq":
                self._providers.append(GroqProvider(
                    p, entry["model"], entry["env_key"],
                    entry.get("daily_request_budget", 30)
                ))
            elif p == "openrouter":
                self._providers.append(OpenRouterProvider(
                    p, entry["model"], entry["env_key"],
                    entry.get("daily_request_budget", 20)
                ))
            elif p == "offline":
                self._providers.append(OfflineProvider())

        self._temperature = cfg.get("temperature", {})
        self._repair_retries = cfg.get("json_repair_retries", 2)
        self._usage_log: list[dict] = []

    def _get_temperature(self, task: str) -> float:
        return self._temperature.get(task, 0.7)

    def complete(self, prompt: str, task: str = "copywriting",
                 system: str = "", require_json: bool = False) -> LLMResponse:
        """
        Run prompt through the fallback chain.
        task: 'ideation' | 'copywriting' | 'qa_critic'
        """
        temp = self._get_temperature(task)

        for provider in self._providers:
            if not provider.available:
                log.info(f"Skipping {provider.name} (unavailable or budget hit)")
                continue
            try:
                log.info(f"Calling {provider.name} ({provider.model})")
                resp = provider.call(prompt, temperature=temp, system=system)
                self._usage_log.append({
                    "provider": provider.name,
                    "task": task,
                    "tokens": resp.tokens_used,
                    "latency_ms": resp.latency_ms,
                })
                return resp
            except Exception as e:
                log.warning(f"{provider.name} failed: {e}")
                if "429" in str(e) or "rate" in str(e).lower():
                    time.sleep(5)
                continue

        # All providers failed — use offline
        log.error("All providers failed, using offline fallback")
        return OfflineProvider().call(prompt, temperature=temp, system=system)

    def complete_json(self, prompt: str, task: str = "ideation",
                      system: str = "",
                      validator=None) -> tuple[dict | list, str]:
        """
        Run prompt expecting JSON output. Auto-repairs on parse failure.
        Returns (parsed_data, provider_name).
        """
        full_system = (system or "") + "\nRespond ONLY with valid JSON. No markdown, no explanation."
        retries = self._repair_retries

        for attempt in range(retries + 1):
            resp = self.complete(prompt, task=task, system=full_system)
            try:
                data = resp.as_json()
                if validator:
                    validator(data)
                return data, resp.provider
            except (json.JSONDecodeError, ValueError) as e:
                if attempt < retries:
                    repair_msg = f"\n\nYour previous response failed JSON validation: {e}\nPlease return ONLY valid JSON."
                    prompt = prompt + repair_msg
                    log.warning(f"JSON repair attempt {attempt + 1}: {e}")
                else:
                    log.error(f"JSON parsing failed after {retries} retries: {e}")
                    raise

    def usage_summary(self) -> dict:
        return {
            "calls": len(self._usage_log),
            "total_tokens": sum(u["tokens"] for u in self._usage_log),
            "providers_used": list({u["provider"] for u in self._usage_log}),
            "detail": self._usage_log,
        }


def load_prompt(name: str, **kwargs) -> str:
    """Load a prompt template from prompts/ and format with kwargs."""
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        template = f.read()
    return template.format(**kwargs) if kwargs else template
