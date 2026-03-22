import base64
import mimetypes
from pathlib import Path
from langfuse.openai import AsyncOpenAI
from config.config import agentSettings, reveal_secret
from utils.file import FileUtils
from utils.logger import get_logger
from utils.retry import async_run_with_retries

logger = get_logger(__name__)

class AudioToolkit:
    def __init__(self):
        self.api_key = reveal_secret(agentSettings.audio_llm.config.api_key)
        self.model = agentSettings.audio_llm.config.model
        self.base_url = agentSettings.audio_llm.config.site_url
        
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
    
    async def transcribe(self, audio_url: str) -> str:
        question = "转录这个音频的内容"
        return await self.audio_qa(audio_url, question)

    async def audio_qa(
        self, 
        audio_path: str, 
        question: str
    ) -> str:
        """
        分析音频文件
        
        Args:
            audio_path: 音频文件路径或 URL
            question: 问题
            
        Returns:
            答案
        """
        if FileUtils.is_web_url(audio_path):
            if FileUtils.is_internal_url(audio_path):
                # 下载 url 到本地
                tmp_path = FileUtils.download_file(audio_path)
                audio_url = FileUtils.encode_to_base64(tmp_path)
            else:
                audio_url = audio_path
        else:
            audio_url = FileUtils.encode_to_base64(audio_path)
        
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "audio_url",
                        "audio_url": {
                            "url": audio_url
                        }
                    },
                    {
                        "type": "text",
                        "text": question
                    }
                ]
            }
        ]
        
        response = await async_run_with_retries(
            lambda: self.client.chat.completions.create(
                model=self.model,
                messages=messages
            ),
            logger=logger,
            action_name="AudioToolkit chat.completions",
        )
        
        return response.choices[0].message.content
