from abc import ABC, abstractmethod
from typing import Dict, Generator, Iterable, Optional, Union

from ..types import LLMMessage, LLMResponse, RoleType


class LLMProvider(ABC):
    def __init__(self, model: str, temperature: float = 0.7, max_output_tokens: int = 512, api_key: Optional[str] = None, base_url: Optional[str] = None, model_thinking_field: Optional[Dict[str, str]] = None) -> None:
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.api_key = api_key
        self.base_url = base_url
        # model_thinking_field maps model name/prefix -> request field for thinking capability.
        self.model_thinking_field = model_thinking_field or {}

    @abstractmethod
    def generate(
        self,
        messages: Iterable[LLMMessage],
        stream: bool = False,
        **kwargs,
    ) -> Union[LLMResponse, Generator[str, None, LLMResponse]]:
        ...

    @property
    def model_family(self) -> str:
        return "generic"

    def summarize_text(self, text: str, goal: str = "Summarize and keep key facts.", stream: bool = False) -> Union[LLMResponse, Generator[str, None, LLMResponse]]:
        messages = [
            LLMMessage(role=RoleType.SYSTEM.value, content=goal),
            LLMMessage(role=RoleType.USER.value, content=text),
        ]
        return self.generate(messages, stream=stream)
