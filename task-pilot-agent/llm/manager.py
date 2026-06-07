import json
import asyncio
import inspect
import threading

from types import GeneratorType
from typing import Any, Dict, Generator, Iterable, List, Optional, Union, Awaitable, Callable
from llm.tokenizer import estimate_tokens_messages
from llm.types import LLMMessage, LLMResponse, RoleType
from llm.providers.base import LLMProvider
from llm.providers.openai_provider import OpenAIProvider, OpenAIThinkingProvider
from llm.providers.claude_provider import ClaudeProvider
from llm.providers.gemini_provider import GeminiProvider
from llm.compressor import compress_messages
from llm.prompt_template import PromptTemplate
from llm.prompt_store import PromptStore
from config.config import agentSettings, LLMSettings, reveal_secret
from utils.logger import get_logger

logger = get_logger(__name__)


ProviderMap = {
    "openai": OpenAIProvider,
    "openai_thinking": OpenAIThinkingProvider,
    "claude": ClaudeProvider,
    "anthropic": ClaudeProvider,
    "gemini": GeminiProvider,
    "google": GeminiProvider,
}


class LLMManager:
    def __init__(self, prompt_store: Optional[PromptStore] = None, default_context: Optional[str] = None) -> None:
        self.cfg = agentSettings.llm
        self.default_context = default_context
        self._providers: Dict[str, LLMProvider] = {}
        self.prompt_store = prompt_store

    def _resolve_llm_config(self, context_name: Optional[str]) -> LLMSettings:
        """Pick an LLM config based on contexts mapping; fallback to primary config."""
        mcfg = self.cfg
        target_context = context_name or self.default_context
        contexts = getattr(mcfg, "contexts", {}) or {}
        configs = getattr(mcfg, "configs", []) or []
        if target_context and contexts and configs:
            target_name = contexts.get(target_context) or contexts.get("default")
            if target_name:
                for item in configs:
                    if getattr(item, "name", None) == target_name:
                        return item  # type: ignore[return-value]
                logger.warning(
                    "LLM context '%s' maps to missing config '%s', falling back to default config",
                    target_context,
                    target_name,
                )
        return mcfg

    def _build_provider(self, mcfg: LLMSettings) -> LLMProvider:
        provider_key = mcfg.provider.lower()
        if provider_key not in ProviderMap:
            raise ValueError(f"Unsupported provider: {mcfg.provider}")
        provider_cls = ProviderMap[provider_key]
        # Resolve API key from providers section if present
        
        api_key = reveal_secret(mcfg.config.api_key)
        thinking_field = getattr(mcfg, "model_thinking_field", None) or getattr(self.cfg, "model_thinking_field", None)
        provider = provider_cls(
            model=mcfg.config.model,
            temperature=mcfg.config.temperature,
            max_output_tokens=mcfg.config.context_length,
            api_key=api_key,
            base_url=mcfg.config.site_url,
            model_thinking_field=thinking_field,
        )
        return provider

    def _get_provider(self, context_name: Optional[str] = None) -> LLMProvider:
        key = context_name or self.default_context or "default"
        cached = self._providers.get(key)
        if cached:
            return cached
        mcfg = self._resolve_llm_config(context_name)
        provider = self._build_provider(mcfg)
        self._providers[key] = provider
        return provider

    def generate(
        self,
        messages: Iterable[LLMMessage],
        stream: bool = False,
        auto_compress: bool = True,
        reserve_response_tokens: Optional[int] = None,
        context_name: Optional[str] = None,
        **kwargs,
    ) -> Union[LLMResponse, Generator[str, None, LLMResponse]]:
    
        provider = self._get_provider(context_name)
        mcfg = self._resolve_llm_config(context_name)
        budget = mcfg.config.context_length
        reserve = reserve_response_tokens if reserve_response_tokens is not None else 50000

        msg_list = list(messages)
        if auto_compress:
            tokens = estimate_tokens_messages(msg_list, model_family=provider.model_family, model_name=provider.model)
            if tokens + reserve > budget:
                msg_list = compress_messages(provider, msg_list, target_budget=budget, reserve_response_tokens=reserve)

        return provider.generate(msg_list, stream=stream, **kwargs)

    def ask_tool(
        self,
        messages: Iterable[LLMMessage],
        tools: object,
        tool_choice: str = "auto",
        stream: bool = False,
        auto_compress: bool = True,
        reserve_response_tokens: Optional[int] = None,
        context_name: Optional[str] = None,
        **kwargs,
    ):
        provider = self._get_provider(context_name)
        mcfg = self._resolve_llm_config(context_name)
        budget = mcfg.config.context_length
        reserve = reserve_response_tokens if reserve_response_tokens is not None else 0

        msg_list = list(messages)
        if auto_compress:
            tokens = estimate_tokens_messages(msg_list, model_family=provider.model_family, model_name=provider.model)
            if tokens + reserve > budget:
                msg_list = compress_messages(provider, msg_list, target_budget=budget, reserve_response_tokens=reserve)

        resp = provider.generate(msg_list, stream=stream, tools=tools, tool_choice=tool_choice, **kwargs)

       
        pairs: List[tuple[str, Dict[str, object]]] = []
        tool_calls = getattr(resp, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                name = getattr(tc.function, "name", None) if hasattr(tc, "function") else None
                args = getattr(tc.function, "arguments", "{}") if hasattr(tc, "function") else "{}"
                try:
                    args_dict = json.loads(args) if isinstance(args, str) else (args or {})
                except Exception:
                    args_dict = {}
                if name:
                    pairs.append((name, args_dict))
            return pairs, resp.text if hasattr(resp, "text") else ""

        raw = getattr(resp, "raw", None)
        try:
            ch0 = raw.choices[0]
            msg = ch0.message
            if getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    name = getattr(tc.function, "name", None)
                    args = getattr(tc.function, "arguments", "{}")
                    try:
                        args_dict = json.loads(args) if isinstance(args, str) else (args or {})
                    except Exception:
                        args_dict = {}
                    if name:
                        pairs.append((name, args_dict))
        except Exception:
            pass

        return pairs, resp.text if hasattr(resp, "text") else ""
    
    async def generate_async(
        self,
        messages: Iterable[LLMMessage],
        stream: bool = False,
        auto_compress: bool = True,
        reserve_response_tokens: Optional[int] = None,
        context_name: Optional[str] = None,
        **kwargs,
    ) -> Union[LLMResponse, Generator[str, None, LLMResponse]]:
        return await asyncio.to_thread(
            self.generate,
            messages,
            stream=stream,
            auto_compress=auto_compress,
            reserve_response_tokens=reserve_response_tokens,
            context_name=context_name,
            **kwargs,
        )

    async def ask_tool_async(
        self,
        messages: Iterable[LLMMessage],
        tools: object,
        tool_choice: str = "auto",
        stream: bool = False,
        auto_compress: bool = True,
        reserve_response_tokens: Optional[int] = None,
        context_name: Optional[str] = None,
        **kwargs,
    ):
        return await asyncio.to_thread(
            self.ask_tool,
            messages,
            tools,
            tool_choice=tool_choice,
            stream=stream,
            auto_compress=auto_compress,
            reserve_response_tokens=reserve_response_tokens,
            context_name=context_name,
            **kwargs,
        )

    async def stream_generate_async(
        self,
        messages: Iterable[LLMMessage],
        chunk_callback: Optional[Callable[[str], Union[Awaitable[None], None]]] = None,
        auto_compress: bool = True,
        reserve_response_tokens: Optional[int] = None,
        enable_thinking: Optional[bool] = True,
        discard_reasoning_content: bool = True,
        context_name: Optional[str] = None,
        **kwargs,
    ) -> Optional[LLMResponse]:
        msg_list = list(messages)
        loop = asyncio.get_running_loop()
        done: asyncio.Future[Optional[LLMResponse]] = loop.create_future()

        def deliver_chunk(chunk: Any) -> None:
            if chunk_callback is None or done.done():
                return
            formatted = self._normalize_stream_chunk(chunk, discard_reasoning_content)
            if not formatted:
                return
            try:
                result = chunk_callback(formatted)
            except Exception as exc:  # pragma: no cover - defensive
                if not done.done():
                    done.set_exception(exc)
                return
            if inspect.isawaitable(result):
                asyncio.create_task(result)

        def worker() -> None:
            try:  # pragma: no cover - integration path
                request_raw_stream = not discard_reasoning_content
                response = self.generate(
                    msg_list,
                    stream=True,
                    auto_compress=auto_compress,
                    reserve_response_tokens=reserve_response_tokens,
                    context_name=context_name,
                    enable_thinking=enable_thinking,
                    raw_stream_events=request_raw_stream,
                    **kwargs,
                )
                final_resp: Optional[LLMResponse] = None
                if isinstance(response, GeneratorType):
                    gen = response
                    try:
                        while True:
                            chunk = next(gen)
                            if chunk:
                                loop.call_soon_threadsafe(deliver_chunk, chunk)
                    except StopIteration as stop:
                        final_resp = getattr(stop, "value", None)
                elif isinstance(response, LLMResponse):
                    final_resp = response
                else:
                    for chunk in response:
                        if chunk:
                            loop.call_soon_threadsafe(deliver_chunk, chunk)
                if not done.done():
                    loop.call_soon_threadsafe(done.set_result, final_resp)
            except Exception as exc:  # pragma: no cover - defensive
                if not done.done():
                    loop.call_soon_threadsafe(done.set_exception, exc)

        threading.Thread(target=worker, daemon=True).start()
        return await done
    def _normalize_stream_chunk(self, chunk: Any, discard_reasoning: bool) -> Optional[str]:
        if chunk is None:
            return None
        data: Any = chunk
        if isinstance(chunk, bytes):
            try:
                data = chunk.decode("utf-8")
            except Exception:
                data = chunk.decode("utf-8", errors="ignore")
        if isinstance(data, str):
            stripped = data.strip()
            if stripped.startswith("{") and ("\"choices\"" in stripped or "\"reasoning_content\"" in stripped):
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    return data
                return self._extract_text_from_event(payload, discard_reasoning)
            return data
        if isinstance(data, dict):
            return self._extract_text_from_event(data, discard_reasoning)
        return str(data)

    def _extract_text_from_event(self, event: Any, discard_reasoning: bool) -> Optional[str]:
        if not isinstance(event, dict):
            return None
        choices = event.get("choices")
        if isinstance(choices, list) and choices:
            choice0 = choices[0]
            delta = choice0.get("delta") if isinstance(choice0, dict) else None
            if isinstance(delta, dict):
                content = self._stringify_stream_value(delta.get("content"))
                if content:
                    return content
                reasoning = self._stringify_stream_value(delta.get("reasoning_content"))
                if reasoning and not discard_reasoning:
                    return reasoning
                return None
        delta = event.get("delta")
        if isinstance(delta, dict):
            text = self._stringify_stream_value(delta.get("text"))
            if text:
                return text
        # Some providers emit reasoning directly on event root
        if not discard_reasoning:
            reasoning = self._stringify_stream_value(event.get("reasoning_content"))
            if reasoning:
                return reasoning
        return None

    def _stringify_stream_value(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: List[str] = []
            for item in value:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        parts.append(str(text))
                else:
                    parts.append(str(item))
            return "".join(parts) if parts else None
        return str(value)

    def generate_from_template(
        self,
        template: PromptTemplate,
        variables: Dict[str, object],
        stream: bool = False,
        auto_compress: bool = True,
        reserve_response_tokens: Optional[int] = None,
        **kwargs,
    ) -> Union[LLMResponse, Generator[str, None, LLMResponse]]:
        rendered = template.render(variables)
        messages = [LLMMessage(role=RoleType.USER.value, content=rendered)]
        return self.generate(messages, stream=stream, auto_compress=auto_compress, reserve_response_tokens=reserve_response_tokens, **kwargs)

    def generate_from_key(
        self,
        key: str,
        variables: Dict[str, object],
        stream: bool = False,
        auto_compress: bool = True,
        reserve_response_tokens: Optional[int] = None,
        prompt_store: Optional[PromptStore] = None,
        **kwargs,
    ) -> Union[LLMResponse, Generator[str, None, LLMResponse]]:
        store = prompt_store or self.prompt_store
        if store is None:
            raise ValueError("No PromptStore provided. Pass 'prompt_store' or set in manager.")
        messages = store.render_messages(key, variables)
        return self.generate(
            messages,
            stream=stream,
            auto_compress=auto_compress,
            reserve_response_tokens=reserve_response_tokens,
            **kwargs,
        )

store = PromptStore.from_file(
    agentSettings.prompt_file,
    language=getattr(agentSettings, "lang", "ch"),
)
mgr = LLMManager(prompt_store=store)
planner_mgr = LLMManager(prompt_store=store, default_context="planner")
executor_mgr = LLMManager(prompt_store=store, default_context="executor")
summary_mgr = LLMManager(prompt_store=store, default_context="summary")
react_mgr = LLMManager(prompt_store=store, default_context="react")
