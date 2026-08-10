export type MessageRole = 'user' | 'assistant' | 'system' | 'tool';

export type ConversationStatus = 'active' | 'archived' | 'running';

export type TaskType = 'shell_command' | 'subagent' | 'custom';

export type TaskStatus = 'running' | 'completed' | 'failed' | 'stopped';

export type ToolPermission = 'auto_approve' | 'ask_user' | 'denied';

export type NavigationTab = 'chat' | 'agents' | 'skills' | 'tools' | 'tasks' | 'settings';

export type JSONSchemaType = 'string' | 'integer' | 'number' | 'boolean' | 'array' | 'object';

export interface Conversation {
  id: string;
  title: string;
  agent_id: string;
  isolated_container: boolean;
  container_id: string | null;
  status: ConversationStatus;
  created_at: string;
  updated_at: string;
}

export interface ToolCallFunction {
  name: string;
  arguments: string;
}

export interface ToolCallItem {
  id: string;
  type: 'function';
  function: ToolCallFunction;
}

export interface Message {
  id?: string;
  conversation_id: string;
  role: MessageRole;
  content?: string | null;
  tool_calls?: ToolCallItem[] | null;
  tool_call_id?: string | null;
  name?: string | null;
  created_at?: string;
}

export interface AgentConfig {
  id: string;
  name: string;
  description: string;
  model: string;
  temperature: number;
  system_prompt: string;
  allowed_tools: string[];
  allowed_skills: string[];
  preloaded_skills: string[];
  tool_permissions: Record<string, ToolPermission>;
}

export interface Skill {
  name: string;
  description: string;
  instructions: string;
  helper_files: string[];
}

export interface ToolParamProperty {
  type: JSONSchemaType;
  description?: string;
  enum?: (string | number | boolean)[];
  default?: any;
}

export interface ToolParametersSchema {
  type: 'object';
  properties?: Record<string, ToolParamProperty>;
  required?: string[];
}

export interface ToolDefinition {
  name: string;
  description: string;
  parameters: ToolParametersSchema;
  is_builtin: boolean;
  source_code?: string;
  verify_code?: string;
}

export interface BackgroundTask {
  id: string;
  conversation_id: string;
  task_type: TaskType;
  name: string;
  command?: string;
  status: TaskStatus;
  pid?: number;
  exit_code?: number;
  stdout: string;
  stderr: string;
  created_at: string;
  updated_at: string;
}

export interface VerifyToolResponse {
  success: boolean;
  stdout: string;
  stderr: string;
  parsed_schema?: ToolParametersSchema | null;
  error?: string | null;
}

export interface AppSettings {
  DEFAULT_MODEL: string;
  OPENAI_API_KEY: string;
  GEMINI_API_KEY: string;
  ANTHROPIC_API_KEY: string;
  VLLM_API_BASE: string;
  OLLAMA_API_BASE: string;
  DOCKER_AVAILABLE: boolean;
}
