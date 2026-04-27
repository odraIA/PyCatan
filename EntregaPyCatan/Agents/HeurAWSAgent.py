import os

from dotenv import load_dotenv

from Agents.HeurGPTAgent import HeurGPTAgent
from Interfaces.AgentInterface import AgentInterface

load_dotenv()


class HeurAWSAgent(HeurGPTAgent):
    def __init__(self, agent_id, model=None, prompt_size="BIG"):
        AgentInterface.__init__(self, agent_id)
        self._commerce_actions = 0
        self.api_key = os.getenv("AWS_BEARER_TOKEN_BEDROCK") or os.getenv("AWS_API_KEY")
        self.api_key_name = os.getenv("AWS_API_NAME")
        self.model = model or os.getenv("AWS_BEDROCK_MODEL") or os.getenv("AWS_LLM_MODEL")
        self.region = (
            os.getenv("AWS_BEDROCK_REGION")
            or os.getenv("AWS_REGION")
            or os.getenv("AWS_DEFAULT_REGION")
            or "us-east-1"
        )
        self.profile = os.getenv("AWS_PROFILE")
        self.endpoint_url = os.getenv("AWS_BEDROCK_ENDPOINT_URL")
        self.prompt_size = prompt_size
        self._bedrock_disabled_reason = None

        if not self.model:
            print("Error creating AWS Bedrock client: AWS_BEDROCK_MODEL/AWS_LLM_MODEL is not set")
            self.client = None
            return

        try:
            self.client = self._create_bedrock_client()
        except Exception as e:
            print(f"Error creating AWS Bedrock client: {e}")
            self.client = None


    def _create_bedrock_client(self):
        import boto3

        if self.api_key:
            # Amazon Bedrock API keys are picked up from this environment variable by boto3.
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = self.api_key

        session_kwargs = {}
        if self.profile and not self.api_key:
            session_kwargs["profile_name"] = self.profile
        if self.region:
            session_kwargs["region_name"] = self.region

        session = boto3.Session(**session_kwargs)

        client_kwargs = {}
        if self.region:
            client_kwargs["region_name"] = self.region
        if self.endpoint_url:
            client_kwargs["endpoint_url"] = self.endpoint_url

        return session.client("bedrock-runtime", **client_kwargs)

    @staticmethod
    def _bedrock_error_code(error):
        response = getattr(error, "response", None)
        if isinstance(response, dict):
            return response.get("Error", {}).get("Code")
        return None

    def _disable_bedrock(self, reason, error=None):
        if self._bedrock_disabled_reason is not None:
            return

        self._bedrock_disabled_reason = reason
        self.client = None

        auth_mode = "api_key" if self.api_key else f"profile={self.profile!r}"
        message = (
            f"Bedrock disabled for agent {self.id} "
            f"(model={self.model}, region={self.region}, auth={auth_mode}): {reason}."
        )
        if error is not None:
            message = f"{message} Original error: {error}"
        print(message)

    @staticmethod
    def _bedrock_text_from_content(content_blocks):
        if isinstance(content_blocks, str):
            return content_blocks

        chunks = []
        for block in content_blocks or []:
            if not isinstance(block, dict):
                continue

            text = block.get("text")
            if text:
                chunks.append(text)
                continue

            reasoning = block.get("reasoningContent")
            if isinstance(reasoning, dict) and reasoning.get("text"):
                chunks.append(reasoning["text"])

        if not chunks:
            return None

        return "".join(chunks).strip()

    def _request_llm(self, prompt):
        if self.client is None or self._bedrock_disabled_reason is not None:
            return None

        request = {
            "modelId": self.model,
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
        }

        try:
            response = self.client.converse(**request)
        except Exception as e:
            error_code = self._bedrock_error_code(e)
            if error_code in {"ExpiredTokenException", "InvalidClientTokenId", "UnrecognizedClientException"}:
                self._disable_bedrock(error_code, e)
                return None

            print(f"Error requesting Bedrock response: {e}")
            return None

        output_message = response.get("output", {}).get("message", {})
        response_text = self._bedrock_text_from_content(output_message.get("content"))
        if response_text is None:
            print(
                "Error requesting Bedrock response: "
                f"empty content returned (stopReason={response.get('stopReason')})"
            )
            return None

        return response_text
