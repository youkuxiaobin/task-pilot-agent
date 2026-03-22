from typing import Any, Generator, Iterable, Union

from llm.types import LLMMessage, LLMResponse, RoleType
from llm.providers.base import LLMProvider
from utils.logger import get_logger
from utils.retry import run_with_retries


logger = get_logger(__name__)


class ClaudeProvider(LLMProvider):
    @property
    def model_family(self) -> str:
        return "claude"

    def generate(
        self,
        messages: Iterable[LLMMessage],
        stream: bool = False,
        **kwargs,
    ) -> Union[LLMResponse, Generator[Any, None, LLMResponse]]:
        try:
            import anthropic  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "Missing dependency 'anthropic'. Install with: pip install anthropic"
            ) from e

        if self.api_key or self.base_url:
            client = anthropic.Anthropic(api_key=self.api_key, base_url=self.base_url)
        else:
            client = anthropic.Anthropic()
        # Convert to Claude's message format: list of {role, content}
        claude_messages = []
        system = None
        for m in messages:
            if m.role == RoleType.SYSTEM.value:
                system = (system + "\n" + m.content) if system else m.content
            else:
                claude_messages.append({"role": m.role, "content": m.content})

        if stream:
            def _gen() -> Generator[Any, None, LLMResponse]:
                acc = []
                stream_ctx = run_with_retries(
                    lambda: client.messages.stream(
                        model=self.model,
                        system=system,
                        messages=claude_messages,
                        max_tokens=self.max_output_tokens,
                        temperature=self.temperature,
                        **kwargs,
                    ),
                    logger=logger,
                    action_name="Claude messages.stream",
                )
                with stream_ctx as stream_obj:
                    for event in stream_obj:
                        # Newer SDK event types: content.delta carries text deltas
                        if getattr(event, "type", "") == "content.delta":
                            text = getattr(getattr(event, "delta", None), "text", None)
                            if text:
                                acc.append(text)
                                yield text
                    final = stream_obj.get_final_message()
                    text = "".join([b.get("text", "") for b in final.content]) if getattr(final, "content", None) else ""  # type: ignore
                    return LLMResponse(text=text, model=self.model, raw=final)

            return _gen()

        resp = run_with_retries(
            lambda: client.messages.create(
                model=self.model,
                system=system,
                messages=claude_messages,
                max_tokens=self.max_output_tokens,
                temperature=self.temperature,
                **kwargs,
            ),
            logger=logger,
            action_name="Claude messages.create",
        )
        text = "".join([b.get("text", "") for b in resp.content]) if getattr(resp, "content", None) else ""  # type: ignore
        return LLMResponse(text=text, model=self.model, raw=resp)
