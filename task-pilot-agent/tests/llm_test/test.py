from llm import LLMManager, PromptTemplate, LLMMessage, PromptStore
from config.config import agentSettings


def main():
 
    store = PromptStore.from_file(
        agentSettings.prompt_file,
        language=getattr(agentSettings, "lang", "ch"),
    )
    # With single `llm` config, default alias is created automatically
    mgr = LLMManager(prompt_store=store)

    # 1) Non-streaming simple template
    tmpl = PromptTemplate("请总结以下内容的要点：\n{content}")
    resp = mgr.generate_from_template(tmpl, {"content": "OpenAI、Claude、Gemini 统一调用管理。"}, stream=False)
    print("Non-streaming response:\n", resp)

    # 2) Streaming chat completion
    messages = [
        LLMMessage(role="system", content="你是一个简洁中文助手。"),
        LLMMessage(role="user", content="用一句话解释向量数据库。"),
    ]
    stream = mgr.generate(messages, stream=True)
    full = []
    for chunk in stream:
        print(chunk, end="", flush=True)
        full.append(chunk)
    print("\n---\nDONE")

    # 3) Using prompt store by key
    resp2 = mgr.generate_from_key("hello_simple", {"name": "小红", "day": "周三"})
    print("\nBy key (hello_simple):\n", resp2.text)

    resp3 = mgr.generate_from_key("summary_with_system", {"content": "报错的根因很简单：你的 AgentSettings（Pydantic Settings）里把 db 定义成必填字段，但当前加载到的配置/环境变量里没有提供 db，因此在 config/config.py 顶层执行 agentSettings = get_settings() 时触发了校验错误。关键点：确保你的 AgentSettings 确实在读取这个 YAML。很多项目里有两个 config/ 目录（你项目就有），容易读错路径。常用做法是在 config.py 里用 Path(__file__).parent / test_config.yaml 这样的绝对路径，并在 settings_customise_sources 里把它加入 source 顺序（见“附：常见写法”）"})
    print("\nBy key (summary_with_system):\n", resp3.text)


if __name__ == "__main__":
    main()
