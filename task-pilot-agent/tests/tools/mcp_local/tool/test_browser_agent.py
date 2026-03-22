import logging
import pytest
from tools.mcp_local.tool.browser_agent import BrowserAgent

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_browser_agent_1():
    task = "python 3.10 最新的版本是多少"
    browser_agent = BrowserAgent()
    ret = await browser_agent.run(task)
    logger.info(f"ret: {ret}")
    assert ret is not None
    assert ret.success is True
    assert "3.10.19" in ret.content


@pytest.mark.asyncio
async def test_browser_agent_2():
    task = "Who nominated the only Featured Article on English Wikipedia about a dinosaur that was promoted in November 2016?. "
    browser_agent = BrowserAgent()
    ret = await browser_agent.run(task)
    logger.info(f"ret: {ret}")
    assert ret is not None
    assert ret.success is True
    assert "FunkMonk" in ret.content