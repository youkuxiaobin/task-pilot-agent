from openai import AsyncOpenAI
from google import genai
from google.genai.types import Part
from config.config import agentSettings, reveal_secret
from utils.file import FileUtils
from utils.retry import async_run_with_retries
from utils.logger import get_logger

logger = get_logger(__name__)

class VideoToolkit:
    def __init__(self):
        self.api_key = reveal_secret(agentSettings.video_llm.config.api_key)
        self.model = agentSettings.video_llm.config.model
        self.base_url = agentSettings.video_llm.config.site_url
        self.provider = agentSettings.video_llm.provider
        
        if self.provider == "openai":
            # https://docs.siliconflow.cn/cn/userguide/capabilities/multimodal-vision
            self.client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
        elif self.provider == "google":
            # https://ai.google.dev/gemini-api/docs/api-key
            self.client = genai.Client( 
                api_key=self.api_key,
            )
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")
    
    async def video_qa(
        self, 
        video_path: str, 
        question: str
    ) -> str:
        if self.provider == "openai":
            return await self.video_qa_openai(video_path, question)
        
        if self.provider == "google":
            return await self.video_qa_google(video_path, question)
        
        return ""

    async def video_qa_openai(
        self, 
        video_path: str, 
        question: str
    ) -> str:
        if FileUtils.is_web_url(video_path):
            video_url = video_path
            if FileUtils.is_internal_url(video_path):
                # 下载 url 到本地
                tmp_path = FileUtils.download_file(video_path)
                video_url = FileUtils.encode_to_base64(tmp_path)
        else:
            video_url = FileUtils.encode_to_base64(video_path)
        
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "video_url",
                        "video_url": {
                            "url": video_url,
                            "detail": "high",
                            "max_frames": 16,
                            "fps": 1
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
            action_name="VideoToolkit OpenAI chat.completions",
        )
        
        return response.choices[0].message.content

    async def video_qa_google(self, video_url: str, question: str) -> str:
        if not video_url.startswith("http"):
            video_part = Part.from_uri(file_uri=video_url)
        else:
            # e.g. Youtube URL
            video_part = Part.from_uri(
                file_uri=video_url,
                mime_type="video/mp4",
            )

        response = await async_run_with_retries(
            lambda: self.client.aio.models.generate_content(
                model=self.model,
                contents=[
                    question,
                    video_part,
                ],
            ),
            logger=logger,
            action_name="VideoToolkit Gemini video QA",
        )

        logger.debug("Video analysis response received from gemini: text_len=%s", len(response.text or ""))
        return response.text
