import os
import time

import httpx
from dotenv import load_dotenv

from Agents.HeurGPTAgent import HeurGPTAgent
from Interfaces.AgentInterface import AgentInterface

load_dotenv()


class HeurOllamaAgent(HeurGPTAgent):
    def __init__(self, agent_id, model=None, prompt_size="BIG"):
        AgentInterface.__init__(self, agent_id)
        self._commerce_actions = 0
        self.api_key = "ollama"
        self.base_url = "http://localhost:11434/v1"
        self.native_base_url = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        self.model = model
        self.prompt_size = prompt_size
        self.request_timeout = self._env_float("HEUR_LLM_TIMEOUT", 45.0)
        self.llm_debug = self._env_bool("HEUR_LLM_DEBUG", default=False)
        self.max_response_tokens = int(self._env_float("HEUR_OLLAMA_MAX_TOKENS", 160))
        self.warmup_enabled = self._env_bool("HEUR_OLLAMA_WARMUP", default=True)
        self.warmup_timeout = self._env_float("HEUR_OLLAMA_WARMUP_TIMEOUT", max(90.0, self.request_timeout))
        self.disable_after_failure = self._env_bool("HEUR_LLM_DISABLE_AFTER_FAILURE", default=True)
        self.llm_available = True
        self.llm_warmed_up = False

        try:
            self.client = httpx.Client(timeout=self.request_timeout)
        except Exception as e:
            print(f"Error creating Ollama client: {e}")
            self.client = None

    def _warmup_model(self):
        if self.client is None or self.llm_warmed_up or not self.warmup_enabled:
            return self.client is not None

        payload = {
            "model": self.model,
            "prompt": "Return exactly {}",
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0,
                "num_predict": 8,
            },
            "keep_alive": "10m",
        }

        start = time.perf_counter()
        try:
            response = self.client.post(
                f"{self.native_base_url}/api/generate",
                json=payload,
                timeout=self.warmup_timeout,
            )
            response.raise_for_status()
            self.llm_warmed_up = True
            if self.llm_debug:
                elapsed = time.perf_counter() - start
                print(
                    f"[{self.__class__.__name__} P{self.id}] warmup ok in {elapsed:.2f}s "
                    f"(model={self.model})"
                )
            return True
        except Exception as e:
            elapsed = time.perf_counter() - start
            print(
                f"[{self.__class__.__name__} P{self.id}] warmup failed after "
                f"{elapsed:.2f}s (model={self.model}): {e}"
            )
            if self.disable_after_failure:
                self.llm_available = False
            return False

    def _request_llm(self, prompt, context="llm_request"):
        if self.client is None or not self.llm_available:
            if self.llm_debug:
                reason = "client unavailable" if self.client is None else "disabled after previous failure"
                print(f"[{self.__class__.__name__} P{self.id}] {context}: {reason}, using heuristic fallback")
            return None

        if not self.llm_warmed_up and not self._warmup_model():
            return None

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0,
                "num_predict": self.max_response_tokens,
            },
            "keep_alive": "10m",
        }

        start = time.perf_counter()
        try:
            response = self.client.post(f"{self.native_base_url}/api/generate", json=payload)
            response.raise_for_status()
            response_json = response.json()
            response_content = response_json.get("response")
        except Exception as e:
            elapsed = time.perf_counter() - start
            print(
                f"[{self.__class__.__name__} P{self.id}] {context} failed after "
                f"{elapsed:.2f}s (model={self.model}, prompt_chars={len(prompt)}): {e}"
            )
            if self.disable_after_failure:
                self.llm_available = False
            return None

        if self.llm_debug:
            elapsed = time.perf_counter() - start
            response_size = len(self._clean_response(response_content) or "")
            print(
                f"[{self.__class__.__name__} P{self.id}] {context} ok in {elapsed:.2f}s "
                f"(model={self.model}, prompt_chars={len(prompt)}, response_chars={response_size})"
            )
        return response_content
