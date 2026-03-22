from typing import Iterable, List, Optional

from llm.types import LLMMessage


def _tiktoken_encode_length(text: str, model: Optional[str] = None) -> Optional[int]:
    try:
        import tiktoken  # type: ignore

        enc = tiktoken.encoding_for_model(model) if model else tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return None


def estimate_tokens_text(text: str, model_family: str = "openai", model_name: Optional[str] = None) -> int:
    # Try tiktoken for OpenAI models first
    if model_family.lower() == "openai":
        length = _tiktoken_encode_length(text, model_name)
        if length is not None:
            return length
    # Heuristic fallback: ~4 chars per token (English), ~1.5 for CJK can be smaller
    # Using 0.25 tokens per char => len(text) * 0.25
    # Clamp minimum at 1 for non-empty strings
    approx = max(1, int(len(text) * 0.25)) if text else 0
    return approx


def estimate_tokens_messages(messages: Iterable[LLMMessage], model_family: str = "openai", model_name: Optional[str] = None) -> int:
    # Simple sum of text token estimates plus small overhead per message
    total = 0
    for m in messages:
        total += estimate_tokens_text(m.content, model_family, model_name)
        total += 4  # role + structural overhead heuristic
    return total

