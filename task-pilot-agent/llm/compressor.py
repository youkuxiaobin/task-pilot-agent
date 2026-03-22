from typing import Iterable, List, Tuple

from llm.tokenizer import estimate_tokens_messages
from llm.types import LLMMessage, RoleType
from llm.providers.base import LLMProvider


def _split_messages(messages: List[LLMMessage]) -> Tuple[List[LLMMessage], List[LLMMessage]]:
    system_msgs: List[LLMMessage] = []
    others: List[LLMMessage] = []
    for m in messages:
        if m.role == RoleType.SYSTEM.value:
            system_msgs.append(m)
        else:
            others.append(m)
    return system_msgs, others


def compress_messages(
    provider: LLMProvider,
    messages: List[LLMMessage],
    target_budget: int,
    reserve_response_tokens: int,
) -> List[LLMMessage]:
    """
    Compress chat history with a simple strategy:
    - Keep system messages intact.
    - Keep the most recent messages as-is.
    - Summarize older messages into a concise summary using the provider itself.
    This runs iterative summarization until it fits the token budget.
    """

    if not messages:
        return messages

    system_msgs, non_system = _split_messages(messages)

    # If already within budget, return
    current_tokens = estimate_tokens_messages(messages, model_family=provider.model_family, model_name=provider.model)
    if current_tokens + reserve_response_tokens <= target_budget:
        return messages

    # Keep recent tail, summarize the head
    head: List[LLMMessage] = []
    tail: List[LLMMessage] = []
    # Start from the end and keep adding to tail until tail + system fits the budget
    for m in reversed(non_system):
        tentative = list(reversed(tail)) + [m]
        candidate = system_msgs + tentative
        tokens = estimate_tokens_messages(candidate, model_family=provider.model_family, model_name=provider.model)
        if tokens + reserve_response_tokens <= target_budget:
            tail.insert(0, m)
        else:
            head = non_system[: len(non_system) - len(tail)]
            break
    if not head and tail:
        # Could be that all non-system messages fit already - still here means total exceeded due to system
        head = []

    if not head:
        # Nothing to summarize; try to summarize the earliest of the tail to squeeze further
        if tail:
            head = [tail.pop(0)]
        else:
            head = []

    if head:
        # Summarize head into a compact note
        head_text = "\n\n".join([f"[{m.role}] {m.content}" for m in head])
        goal = (
            "你是一个总结助手。将以下历史对话压缩为尽可能简短的摘要，"
            "保留关键信息、事实、结论、约束和未解决问题；去掉寒暄。"
        )
        summary_resp = provider.summarize_text(head_text, goal=goal, stream=False)
        summary_msg = LLMMessage(role=RoleType.SYSTEM.value, content=f"对话历史摘要：\n{summary_resp.text}")
        new_messages = system_msgs + [summary_msg] + tail
    else:
        new_messages = system_msgs + tail

    # If still too large, recurse with the new_messages
    new_tokens = estimate_tokens_messages(new_messages, model_family=provider.model_family, model_name=provider.model)
    if new_tokens + reserve_response_tokens > target_budget and len(new_messages) > 1:
        return compress_messages(provider, new_messages, target_budget, reserve_response_tokens)
    return new_messages

