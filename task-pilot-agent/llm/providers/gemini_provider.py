from typing import Any, Generator, Iterable, Union

from llm.types import LLMMessage, LLMResponse, RoleType
from llm.providers.base import LLMProvider
from utils.logger import get_logger
from utils.retry import run_with_retries


logger = get_logger(__name__)


class GeminiProvider(LLMProvider):
    @property
    def model_family(self) -> str:
        return "gemini"

    def generate(
        self,
        messages: Iterable[LLMMessage],
        stream: bool = False,
        **kwargs,
    ) -> Union[LLMResponse, Generator[Any, None, LLMResponse]]:
        try:
            import google.generativeai as genai  # type: ignore
        except Exception as e:
            raise RuntimeError(
                "Missing dependency 'google-generativeai'. Install with: pip install google-generativeai"
            ) from e

        if self.api_key or self.base_url:
            kwargs = {"api_key": self.api_key} if self.api_key else {}
            if self.base_url:
                # google-generativeai supports custom endpoint via client_options.api_endpoint
                kwargs["client_options"] = {"api_endpoint": self.base_url}
            genai.configure(**kwargs)

        # Gemini typically expects a single prompt; we will merge messages.
        system_parts = []
        convo_parts = []
        for m in messages:
            if m.role == RoleType.SYSTEM.value:
                system_parts.append(m.content)
            else:
                prefix = m.role.upper()
                convo_parts.append(f"[{prefix}] {m.content}")
        prompt = "\n".join(["\n".join(system_parts), "\n".join(convo_parts)]).strip()

        model = genai.GenerativeModel(self.model)
        gen_kwargs = {
            "generation_config": {
                "temperature": self.temperature,
                "max_output_tokens": self.max_output_tokens,
            }
        }
        gen_kwargs.update(kwargs or {})

        if stream:
            def _gen() -> Generator[Any, None, LLMResponse]:
                acc = []
                stream_resp = run_with_retries(
                    lambda: model.generate_content(prompt, stream=True, **gen_kwargs),
                    logger=logger,
                    action_name="Gemini generate_content stream",
                )
                for chunk in stream_resp:
                    text = getattr(chunk, "text", None)
                    if text:
                        acc.append(text)
                        yield text
                full = "".join(acc)
                return LLMResponse(text=full, model=self.model, raw=None)

            return _gen()

        resp = run_with_retries(
            lambda: model.generate_content(prompt, **gen_kwargs),
            logger=logger,
            action_name="Gemini generate_content",
        )
        text = getattr(resp, "text", "") or ""
        return LLMResponse(text=text, model=self.model, raw=resp)
