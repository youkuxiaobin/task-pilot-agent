from openai import AsyncOpenAI
from config.config import agentSettings, reveal_secret
from utils.file import FileUtils
from utils.logger import get_logger
from utils.retry import async_run_with_retries

logger = get_logger(__name__)

class ImageToolkit:
    def __init__(self):
        self.api_key = reveal_secret(agentSettings.image_llm.config.api_key)
        self.model = agentSettings.image_llm.config.model
        self.base_url = agentSettings.image_llm.config.site_url
        
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
    
    async def image_qa(
        self, 
        image_path: str, 
        question: str
    ) -> str:
        if FileUtils.is_web_url(image_path):
            image_url = image_path
            if FileUtils.is_internal_url(image_path):
                # 下载 url 到本地
                tmp_path = FileUtils.download_file(image_path)
                image_url = FileUtils.encode_to_base64(tmp_path)
        else:
            image_url = FileUtils.encode_to_base64(image_path)
        
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_url,
                            "detail": "high" # 细节级别 (auto, low, high)
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
            action_name="ImageToolkit chat.completions",
        )
        
        return response.choices[0].message.content
