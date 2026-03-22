from __future__ import annotations
from typing import Any, Dict, Optional
from pydantic import BaseModel


class GptProcessResult(BaseModel):
    status: str
    finished: bool
    responseType: str
    response: str = ""
    responseAll: str = ""
    useTimes: int = 0
    useTokens: int = 0
    reqId: Optional[str] = None
    packageType: Optional[str] = None
    encrypted: bool = False
    resultMap: Optional[Dict[str, Any]] = None


class AgentResponse(BaseModel):
    requestId: str
    messageId: str
    messageType: str
    messageTime: str
    resultMap: Dict[str, Any]
    finish: bool = False
    isFinal: Optional[bool] = None
    digitalEmployee: Optional[str] = None
    toolThought: Optional[str] = None
    planThought: Optional[str] = None
    taskSummary: Optional[str] = None
    plan: Optional[Dict[str, Any]] = None
    toolResult: Optional[Dict[str, Any]] = None
    result: Optional[str] = None

