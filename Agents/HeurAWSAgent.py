import os

import openai
from dotenv import load_dotenv

from Agents.HeurGPTAgent import HeurGPTAgent
from Interfaces.AgentInterface import AgentInterface

load_dotenv()


class HeurAWSAgent(HeurGPTAgent):
    def __init__(self, agent_id, model=None, prompt_size="BIG"):
        AgentInterface.__init__(self, agent_id)
        self._commerce_actions = 0
        self.api_key = os.getenv("AWS_LLM_API_KEY") or "aws"
        self.base_url = os.getenv("AWS_LLM_BASE_URL")
        self.model = model or os.getenv("AWS_LLM_MODEL")
        self.prompt_size = prompt_size

        if not self.base_url:
            print("Error creating AWS client: AWS_LLM_BASE_URL is not set")
            self.client = None
            return
        if not self.model:
            print("Error creating AWS client: AWS_LLM_MODEL is not set")
            self.client = None
            return

        try:
            self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
        except Exception as e:
            print(f"Error creating AWS client: {e}")
            self.client = None
