from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel
from brain.core.context import FileItem





class AgentMessage(BaseModel):
    role: str
    content: str
    commandCode: Optional[str] = None
    uploadFile: Optional[List[FileItem]] = None
    files: Optional[List[FileItem]] = None


class AgentRequest(BaseModel):
    requestId: str
    erp: Optional[str] = None
    query: str
    agentType: int
    basePrompt: Optional[str] = None
    sopPrompt: Optional[str] = None
    isStream: bool = True
    messages: Optional[List[AgentMessage]] = None
    outputStyle: Optional[str] = None


class GptQueryReq(BaseModel):
    trace_id: Optional[str] = None
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    conversation_id: Optional[str] = None
    outputStyle: Optional[str] = None
    mode: Optional[str] = None
    selected_tools: Optional[List[str]] = None
    approved_tools: Optional[List[str]] = None
    run_environment: Optional[str] = None
    messages: Optional[List[AgentMessage]] = None


class TaskUserInputReq(BaseModel):
    content: str
    user_id: Optional[str] = None
