import pytest
import logging
from pathlib import Path
from tools.mcp_local.tool.video_toolkit import VideoToolkit

logger = logging.getLogger(__name__)

@pytest.mark.asyncio
async def test_video_toolkit_url():
    video_toolkit = VideoToolkit()
    result = await video_toolkit.video_qa(
        video_path="https://vjs.zencdn.net/v/oceans.mp4",
        question="总结视频的内容"
    )
    logger.info(f"result: {result}")

@pytest.mark.asyncio
async def test_video_toolkit_youtube_url():
    video_toolkit = VideoToolkit()
    result = await video_toolkit.video_qa(
        video_path="https://www.youtube.com/watch?v=d7-WQa2_mX8",
        question="总结视频的内容"
    )
    logger.info(f"result: {result}")

@pytest.mark.asyncio
async def test_video_toolkit_file():
    video_toolkit = VideoToolkit()
    video_path = str(Path(__file__).parent / "data" / "oceans.mp4")
    logger.info(f"video_path: {video_path}")
    result = await video_toolkit.video_qa(
        video_path=video_path,
        question="总结视频的内容"
    )
    logger.info(f"result: {result}")
