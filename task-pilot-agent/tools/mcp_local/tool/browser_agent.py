from agent_sandbox import Sandbox
from browser_use import Agent, Tools
from browser_use.browser import BrowserProfile, BrowserSession
from browser_use.llm import ChatOpenAI, BaseChatModel
from config.config import agentSettings, LLMConfigSettings, reveal_secret
from pydantic import BaseModel
from utils.logger import get_logger

logger = get_logger(__name__)


class BrowserAgentOutput(BaseModel):
    content: str
    success: bool

class BrowserAgent:
    def __init__(self):
        browser_use_settings = agentSettings.browser_use
        sandbox = Sandbox(base_url=browser_use_settings.sandbox_url)
        cdp_url = sandbox.browser.get_info().data.cdp_url

        self.browser_session = BrowserSession(
            browser_profile=BrowserProfile(cdp_url=cdp_url, is_local=True)
        )

        self.use_vision = "auto"

        llm_settings = browser_use_settings.config
        self.llm = self._get_llm(browser_use_settings.provider, llm_settings)

        self.tools = Tools()
    
    def _get_llm(self, provider: str, config: LLMConfigSettings) -> BaseChatModel:
        if provider == "openai":
            return ChatOpenAI(
                model=config.model,
                api_key=reveal_secret(config.api_key),
                base_url=config.site_url
            )
        elif provider == "moonshot":
            # k2 默认不支持视觉
            self.use_vision = False
            return ChatOpenAI(
                model=config.model,
                base_url=config.site_url,
                api_key=reveal_secret(config.api_key),
                add_schema_to_system_prompt=True,
                remove_min_items_from_schema=True,  # Moonshot doesn't support minItems in JSON schema
                remove_defaults_from_schema=True,  # Moonshot doesn't allow default values with anyOf
            )
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    
    async def run(self, task: str) -> BrowserAgentOutput:
        logger.info("BrowserAgent run started: task_len=%s", len(task or ""))
        agent = Agent(
            task=task,
            llm=self.llm,
            tools=self.tools,
            browser_session=self.browser_session,
            llm_timeout=120,
            step_timeout=120,
            max_steps=20,
            use_vision=self.use_vision,
        )
        history_list = await agent.run()
        logger.debug("BrowserAgent run finished")
        final_result = history_list.final_result() if history_list.final_result() else ""
        success = history_list.is_successful() if history_list.is_successful() else False
        return BrowserAgentOutput(content=final_result, success=success)
