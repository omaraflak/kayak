from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    """Role of a message in a conversation turn."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ConversationStatus(str, Enum):
    """Lifecycle status of a conversation."""
    ACTIVE = "active"
    RUNNING = "running"
    ARCHIVED = "archived"


class TaskType(str, Enum):
    """Classification of asynchronous tasks."""
    SHELL_COMMAND = "shell_command"
    SUBAGENT = "subagent"
    CUSTOM = "custom"


class TaskStatus(str, Enum):
    """Execution status of a background task."""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class ToolPermission(str, Enum):
    """Permission policy for executing tools."""
    AUTO_APPROVE = "auto_approve"
    ASK_USER = "ask_user"
    DENIED = "denied"


class ToolCategory(str, Enum):
    """Broad capability a tool belongs to, used to group them for configuration."""
    FILESYSTEM = "filesystem"
    EXECUTION = "execution"
    WEB = "web"
    ORCHESTRATION = "orchestration"
    KNOWLEDGE = "knowledge"
    TOOLING = "tooling"
    CUSTOM = "custom"


class ToolRisk(str, Enum):
    """How much damage a tool can do if the model uses it wrongly.

    This is about blast radius, not likelihood: HIGH means the tool can execute
    arbitrary code, reach the host, or change what the platform itself will run.
    """
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class JSONSchemaType(str, Enum):
    """JSON Schema primitive data types."""
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class ToolCall(BaseModel):
    """A structured tool call requested by the model."""
    id: str
    name: str
    arguments: Dict[str, Any]


class ToolCallResult(BaseModel):
    """Result payload returned after executing a tool."""
    tool_call_id: str
    name: str
    output: str
    is_error: bool = False


class Message(BaseModel):
    """A single message item in the conversation history."""
    id: Optional[str] = None
    conversation_id: str
    role: MessageRole
    content: Optional[str] = None
    thinking: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    created_at: Optional[str] = None


class Conversation(BaseModel):
    """A persistent conversation session with an agent."""
    id: str
    title: str
    agent_id: str
    isolated_container: bool = False
    container_id: Optional[str] = None
    status: ConversationStatus = ConversationStatus.ACTIVE
    parent_conversation_id: Optional[str] = None
    created_at: str
    updated_at: str


class CreateConversationRequest(BaseModel):
    """Request body for instantiating a new conversation session."""
    title: Optional[str] = None
    agent_id: str = "general"
    isolated_container: bool = False
    initial_message: Optional[str] = None


class SendMessageRequest(BaseModel):
    """Request body for dispatching a user prompt to a conversation."""
    content: str


class AgentConfig(BaseModel):
    """Configuration profile defining agent permissions, behavior, and capabilities."""
    id: str
    name: str
    description: str
    model: str = "gemini/gemini-3.6-flash"
    temperature: float = 0.7
    system_prompt: str = ""
    allowed_tools: List[str] = Field(default_factory=list)
    allowed_skills: List[str] = Field(default_factory=list)
    preloaded_skills: List[str] = Field(default_factory=list)
    tool_permissions: Dict[str, ToolPermission] = Field(default_factory=dict)


class Skill(BaseModel):
    """Markdown skill package loaded by agents."""
    name: str
    description: str
    instructions: str
    helper_files: List[str] = Field(default_factory=list)


class ToolParamSchema(BaseModel):
    """Schema definition for an individual tool parameter."""
    type: JSONSchemaType
    description: Optional[str] = None
    enum: Optional[List[Any]] = None
    default: Optional[Any] = None


class ToolDefinition(BaseModel):
    """Full tool definition including schema and optional source code."""
    name: str
    description: str
    parameters: Dict[str, Any]
    is_builtin: bool = False
    category: ToolCategory = ToolCategory.CUSTOM
    risk: ToolRisk = ToolRisk.MODERATE
    source_code: Optional[str] = None
    verify_code: Optional[str] = None


class ToolCategoryInfo(BaseModel):
    """Display metadata for a tool category, so clients need no hardcoded list."""
    value: ToolCategory
    label: str
    description: str


class BackgroundTask(BaseModel):
    """A background process or sub-agent executing asynchronously."""
    id: str
    conversation_id: str
    task_type: TaskType
    name: str
    command: Optional[str] = None
    status: TaskStatus = TaskStatus.RUNNING
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    created_at: str
    updated_at: str


class VerifyToolRequest(BaseModel):
    """Payload submitted to test a tool's verification test suite."""
    tool_name: str
    tool_code: str
    verify_code: str


class VerifyToolResponse(BaseModel):
    """Structured result from running verify.py."""
    success: bool
    stdout: str
    stderr: str
    parsed_schema: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class ActivateToolRequest(BaseModel):
    """Payload to save and activate a verified custom tool."""
    tool_name: str
    tool_code: str
    verify_code: str
    description: Optional[str] = None
