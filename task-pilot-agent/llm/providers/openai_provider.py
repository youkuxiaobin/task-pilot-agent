import json
from typing import Any, Dict, Generator, Iterable, List, Optional, Union

from llm.types import LLMMessage, LLMResponse
from llm.providers.base import LLMProvider
from utils.logger import get_logger
from utils.retry import run_with_retries

# Default field used when no specific override is found.
DEFAULT_THINKING_FIELD = "enable_thinking"
logger = get_logger(__name__)

class OpenAIProvider(LLMProvider):
    @property
    def model_family(self) -> str:
        return "openai"

    def generate(
        self,
        messages: Iterable[LLMMessage],
        stream: bool = False,
        **kwargs,
    ) -> Union[LLMResponse, Generator[Any, None, LLMResponse]]:
        try:
            from langfuse.openai import OpenAI  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "Missing dependency 'openai'. Install with: pip install openai"
            ) from e

        if self.api_key or self.base_url:
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        else:
            client = OpenAI()
        chat_messages = [
            {"role": m.role, "content": m.content} for m in messages
        ]

        enable_thinking = kwargs.pop("enable_thinking", None)
        thinking_budget = kwargs.pop("thinking_budget", None)
        extra_body = kwargs.pop("extra_body", None)
        raw_stream_events = kwargs.pop("raw_stream_events", False)
        merged_body = self._build_extra_body(extra_body, enable_thinking, thinking_budget)
        if merged_body is not None:
            kwargs["extra_body"] = merged_body

        if stream:
            def _gen() -> Generator[Any, None, LLMResponse]:
                acc: list[str] = []
                events = run_with_retries(
                    lambda: client.chat.completions.create(
                        model=self.model,
                        messages=chat_messages,
                        temperature=self.temperature,
                        #max_tokens=self.max_output_tokens,
                        stream=True,
                        **kwargs,
                    ),
                    logger=logger,
                    action_name="OpenAI chat.completions stream",
                )
                for event in events:
                    payload = self._event_to_dict(event)
                    if payload is None:
                        continue
                    #logger.info(f"payload:{payload}")
                    text_chunk = self._extract_content_from_payload(payload)
                    if text_chunk:
                        acc.append(text_chunk)
                    if raw_stream_events:
                        yield payload
                    elif text_chunk:
                        yield text_chunk
                full = "".join(acc)
                return LLMResponse(text=full, model=self.model, raw=None)

            return _gen()

        # Non-streaming
        resp = run_with_retries(
            lambda: client.chat.completions.create(
                model=self.model,
                messages=chat_messages,
                temperature=self.temperature,
                #max_tokens=self.max_output_tokens,
                **kwargs,
            ),
            logger=logger,
            action_name="OpenAI chat.completions",
        )
        logger.debug(
            "openai provider response received: model=%s id=%s",
            self.model,
            getattr(resp, "id", None),
        )
        text = resp.choices[0].message.content  # type: ignore
        return LLMResponse(text=text or "", model=self.model, raw=resp)

    def _build_extra_body(
        self,
        extra_body: Optional[Dict[str, Any]],
        enable_thinking: Optional[bool],
        thinking_budget: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        """Merge caller-provided extra_body with model-specific thinking flags."""
        merged_body: Dict[str, Any] = dict(extra_body) if extra_body is not None else {}
        if enable_thinking is not None:
            field_name = self._resolve_thinking_field()
            if field_name:
                merged_body[field_name] = enable_thinking
        if thinking_budget is not None:
            merged_body["thinking_budget"] = thinking_budget
        return merged_body or None

    def _resolve_thinking_field(self) -> Optional[str]:
        """Return the request field name for thinking capability based on model."""
        normalized = (self.model or "").lower()
        mapping = getattr(self, "model_thinking_field", {}) or {}
        for key, field in mapping.items():
            if normalized.startswith(key.lower()):
                return field
        return DEFAULT_THINKING_FIELD

    def _event_to_dict(self, event: Any) -> Optional[Dict[str, Any]]:
        if isinstance(event, dict):
            return event
        for attr in ("model_dump", "dict"):
            fn = getattr(event, attr, None)
            if callable(fn):
                try:
                    data = fn()
                except Exception:
                    continue
                if isinstance(data, dict):
                    return data
        payload = getattr(event, "__dict__", None)
        if isinstance(payload, dict):
            return payload
        json_fn = getattr(event, "json", None)
        if callable(json_fn):
            try:
                raw_json = json_fn()
                if isinstance(raw_json, str):
                    return json.loads(raw_json)
            except Exception:
                pass
        if isinstance(event, str):
            try:
                return json.loads(event)
            except Exception:
                return None
        return None

    def _extract_content_from_payload(self, payload: Dict[str, Any]) -> Optional[str]:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            delta = choice.get("delta") if isinstance(choice, dict) else None
            if isinstance(delta, dict):
                content = self._stringify_stream_value(delta.get("content"))
                if content:
                    return content
                text = self._stringify_stream_value(delta.get("text"))
                if text:
                    return text
        delta_root = payload.get("delta")
        if isinstance(delta_root, dict):
            text = self._stringify_stream_value(delta_root.get("text"))
            if text:
                return text
            content = self._stringify_stream_value(delta_root.get("content"))
            if content:
                return content
        return self._stringify_stream_value(payload.get("content"))

    def _stringify_stream_value(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: List[str] = []
            for item in value:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if text:
                        parts.append(str(text))
                else:
                    parts.append(str(item))
            return "".join(parts) if parts else None
        return str(value)


class OpenAIThinkingProvider(OpenAIProvider):
    """Backward-compatible alias that enables thinking fields via OpenAIProvider."""
    pass
