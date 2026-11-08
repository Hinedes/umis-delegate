"""Vultr Serverless Inference client (OpenAI tool-calling shape).

The client holds no ORM or database access. Tests replace it with a fake.
"""

import requests
from django.conf import settings

DEFAULT_BASE_URL = 'https://api.vultrinference.com/v1/chat/completions'
DEFAULT_MODEL = 'deepseek-v4.1-flash-1017'


class LLMConfigurationError(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


class VultrLLMClient:
    def __init__(self, api_key=None, model=None, base_url=None, timeout=None, reasoning_effort=''):
        if api_key is None:
            api_key = getattr(settings, 'VULTR_SERVERLESS_INFERENCE_API_KEY', '') or ''
        self.api_key = api_key
        self.model = model or getattr(settings, 'VULTR_INFERENCE_MODEL', DEFAULT_MODEL)
        self.base_url = base_url or getattr(
            settings, 'VULTR_INFERENCE_BASE_URL', DEFAULT_BASE_URL,
        )
        self.timeout = timeout or int(getattr(settings, 'VULTR_INFERENCE_TIMEOUT', 90))
        self.reasoning_effort = reasoning_effort
        if not self.api_key:
            raise LLMConfigurationError(
                'VULTR_SERVERLESS_INFERENCE_API_KEY is not configured. '
                'Set it in the environment to run the agent.'
            )

    def chat(self, messages, tools):
        payload = {
            'model': self.model,
            'messages': messages,
            'tools': tools,
            'tool_choice': 'auto',
        }
        if self.reasoning_effort:
            payload['reasoning_effort'] = self.reasoning_effort
        try:
            response = requests.post(
                self.base_url,
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                },
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise LLMError(f'Inference request failed: {exc}') from exc

        if response.status_code in (401, 403):
            raise LLMConfigurationError(
                'The inference service rejected the API key (HTTP '
                f'{response.status_code}). Check VULTR_SERVERLESS_INFERENCE_API_KEY.'
            )
        if response.status_code >= 400:
            raise LLMError(
                f'Inference service returned HTTP {response.status_code}: '
                f'{response.text[:300]}'
            )
        try:
            data = response.json()
            choice = (data.get('choices') or [])[0]
            message = choice.get('message') or {}
        except (ValueError, IndexError, AttributeError) as exc:
            raise LLMError('The inference service returned an invalid response.') from exc

        return {
            'role': 'assistant',
            'content': message.get('content') or '',
            'tool_calls': message.get('tool_calls') or [],
        }


def get_llm_client(reasoning_effort=''):
    return VultrLLMClient(reasoning_effort=reasoning_effort)
