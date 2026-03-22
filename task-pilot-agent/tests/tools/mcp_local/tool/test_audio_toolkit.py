import pytest
import logging
from pathlib import Path
from tools.mcp_local.tool.audio_toolkit import AudioToolkit

logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_audio_toolkit_url():
    audio_toolkit = AudioToolkit()
    result = await audio_toolkit.audio_qa(
        audio_path="http://www.pthxx.com/b_audio/pthxx_com_mp3/01_langdu/02.mp3",
        question="转录这个音频的内容"
    )
    assert result.startswith("作品二号")
    logger.info(f"result: {result}")

@pytest.mark.asyncio
async def test_audio_toolkit_internal_url():
    audio_toolkit = AudioToolkit()
    result = await audio_toolkit.audio_qa(
        audio_path="http://0.0.0.0:9010/file/v1/download_file/bbeff05c-b87c-4c3a-8cd9-6ef81de04ef7/2b3ef98c-cc05-450b-a719-711aee40ac65.mp3",
        question="转录这个音频的内容"
    )
    logger.info(f"result: {result}")

@pytest.mark.asyncio
async def test_audio_toolkit_file():
    audio_toolkit = AudioToolkit()
    audio_path = str(Path(__file__).parent / "data" / "99c9cc74-fdc8-46c6-8f8d-3ce2d3bfeea3.mp3")
    logger.info(f"audio_path: {audio_path}")
    result = await audio_toolkit.audio_qa(
        audio_path=audio_path,
        question="转录这个音频的内容"
    )
    assert result.startswith("In a saucepan")
    logger.info(f"result: {result}")
