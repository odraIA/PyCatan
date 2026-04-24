import os

import openai
from dotenv import load_dotenv

from Agents.HeurGPTAgent import HeurGPTAgent
from Interfaces.AgentInterface import AgentInterface

load_dotenv()


class HeurOllamaAgent(HeurGPTAgent):
    def __init__(self, agent_id, model=None, prompt_size="BIG"):
        AgentInterface.__init__(self, agent_id)
        self._commerce_actions = 0
        self.api_key = os.getenv("OLLAMA_API_KEY") or "ollama"
        self.base_url = os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434/v1"
        self.model = model or os.getenv("OLLAMA_MODEL") or "qwen3:32b"
        self.prompt_size = prompt_size

        try:
            self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
        except Exception as e:
            print(f"Error creating Ollama client: {e}")
            self.client = None
