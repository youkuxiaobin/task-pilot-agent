from llm.manager import LLMManager
from llm.prompt_template import PromptTemplate
from llm.prompt_store import PromptStore
from llm.types import LLMMessage, LLMResponse
from config.config import agentSettings

__all__ = [
    "LLMManager",
    "PromptTemplate",
    "PromptStore",
    "LLMMessage",
    "LLMResponse",
]


promptStore = PromptStore.from_file(
    agentSettings.prompt_file,
    language=getattr(agentSettings, "lang", "ch"),
)
llmMgr = LLMManager(prompt_store=promptStore)
