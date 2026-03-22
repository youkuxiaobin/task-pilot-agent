import pytest
import logging
from pathlib import Path
from tools.mcp_local.tool.image_toolkit import ImageToolkit

logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_audio_toolkit_url():
    image_toolkit = ImageToolkit()
    result = await image_toolkit.image_qa(
        image_path="https://upload.wikimedia.org/wikipedia/commons/3/3f/Fronalpstock_big.jpg",
        question="图片里主要是什么场景？"
    )
    logger.info(f"result: {result}")

@pytest.mark.asyncio
async def test_audio_toolkit_file():
    image_toolkit = ImageToolkit()
    image_path = str(Path(__file__).parent / "data" / "eef43a411120081fa8dec41133ee8f9c.jpg")
    logger.info(f"image_path: {image_path}")
    result = await image_toolkit.image_qa(
        image_path=image_path,
        question="图片中的会议名称是什么？"
    )
    logger.info(f"result: {result}")
