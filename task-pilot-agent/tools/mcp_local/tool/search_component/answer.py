import os
import time

from tools.mcp_local.util.log_util import timer
from tools.mcp_local.util.prompt_util import get_prompt
from llm.manager import mgr as llm_mgr
from llm.types import LLMMessage, RoleType

@timer()
async def answer_question(query: str, search_content: str, stream: bool = False):
	prompt_template = get_prompt("deepsearch")["answer_prompt"]

	model = os.getenv("SEARCH_ANSWER_MODEL", "gpt-4.1")
	answer_length = os.getenv("SEARCH_ANSWER_LENGTH", "1000")

	prompt = prompt_template.format(
		query=query,
		sub_qa=search_content,
		current_time=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
		response_length=answer_length
	)
	
	response = llm_mgr.generate(
		messages=[LLMMessage(role=RoleType.USER.value, content=prompt)],
		stream=stream,
	)
	
	if stream:
		# 流式模式：返回生成器
		for chunk in response:
			if chunk:
				yield chunk
	else:
		# 非流式模式：返回完整响应
		if hasattr(response, 'text'):
			yield response.text
		else:
			yield str(response)


if __name__ == "__main__":
	pass 
