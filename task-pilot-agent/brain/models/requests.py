from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field
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
    session_message_id: Optional[str] = None
    language: Optional[str] = None
    outputStyle: Optional[str] = None
    mode: Optional[str] = None
    selected_tools: Optional[List[str]] = None
    approved_tools: Optional[List[str]] = None
    run_environment: Optional[str] = None
    messages: Optional[List[AgentMessage]] = None


class TaskUserInputReq(BaseModel):
    content: str
    user_id: Optional[str] = None
    language: Optional[str] = None


class AgentSessionCreateReq(BaseModel):
    session_id: Optional[str] = None
    sessionId: Optional[str] = None
    title: Optional[str] = None
    agent_id: Optional[str] = None
    agentId: Optional[str] = None
    metadata: Optional[dict] = None


class AgentSessionUpdateReq(BaseModel):
    title: Optional[str] = None
    agent_id: Optional[str] = None
    agentId: Optional[str] = None
    pinned: Optional[bool] = None
    archived: Optional[bool] = None
    metadata: Optional[dict] = None


class AgentRunOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: Optional[str] = None
    language: Optional[str] = None
    output_style: Optional[str] = None
    mode: Optional[str] = None
    selected_tools: Optional[List[str]] = None
    approved_tools: Optional[List[str]] = None
    run_environment: Optional[str] = None


class AgentSessionMessageReq(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    files: Optional[List[FileItem]] = None
    options: Optional[AgentRunOptions] = None


class AgentRunApprovalReq(BaseModel):
    approved: bool
    approved_tools: Optional[List[str]] = None
    approvedTools: Optional[List[str]] = None
    approval_type: Optional[str] = "high_risk_tools"
    approvalType: Optional[str] = None
    reason: Optional[str] = None
    rerun: bool = True


class AgentMCPToolTestReq(BaseModel):
    tool_name: Optional[str] = None
    toolName: Optional[str] = None
    arguments: Dict[str, Any] = Field(default_factory=dict)
    agent_id: Optional[str] = None
    agentId: Optional[str] = None
    approved_tools: Optional[List[str]] = None
    approvedTools: Optional[List[str]] = None
    run_environment: Optional[str] = None
    runEnvironment: Optional[str] = None


class AgentMCPToolDryRunReq(BaseModel):
    arguments: Dict[str, Any] = Field(default_factory=dict)
    agent_id: Optional[str] = None
    agentId: Optional[str] = None
    approved_tools: Optional[List[str]] = None
    approvedTools: Optional[List[str]] = None
    run_environment: Optional[str] = None
    runEnvironment: Optional[str] = None
