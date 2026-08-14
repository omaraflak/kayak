export type MessageRole = 'user' | 'assistant' | 'system' | 'tool';

export type ConversationStatus = 'active' | 'archived' | 'running';

export type TaskType = 'shell_command' | 'subagent' | 'custom';

export type TaskStatus = 'running' | 'completed' | 'failed' | 'stopped';

export type ToolPermission = 'auto_approve' | 'ask_user' | 'denied';

export type NavigationTab = 'chat' | 'agents' | 'skills' | 'tools' | 'tasks' | 'models' | 'settings';

export type ToolCategory =
  | 'filesystem'
  | 'execution'
  | 'web'
  | 'orchestration'
  | 'knowledge'
  | 'tooling'
  | 'custom';

export type ToolRisk = 'low' | 'moderate' | 'high';

/** Display metadata for a tool category, served by the backend. */
export interface ToolCategoryInfo {
  value: ToolCategory;
  label: string;
  description: string;
}

export type JSONSchemaType = 'string' | 'integer' | 'number' | 'boolean' | 'array' | 'object';

export interface Conversation {
  id: string;
  title: string;
  agent_id: string;
  isolated_container: boolean;
  container_id: string | null;
  status: ConversationStatus;
  /** Set on sub-agent sessions; these are deleted with their parent. */
  parent_conversation_id?: string | null;
  /** Where a branch was taken from. Independent of the parent link above. */
  branched_from_conversation_id?: string | null;
  branched_from_message_id?: string | null;
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
  thinking?: string | null;
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
  category: ToolCategory;
  risk: ToolRisk;
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

export interface ModelItem {
  id: string;
  name: string;
  provider: string;
  description: string;
  context_window?: string | null;
  is_available: boolean;
  is_running_locally?: boolean;
}

export interface ProviderModels {
  provider_id: string;
  provider_name: string;
  icon: string;
  is_configured: boolean;
  status_message: string;
  models: ModelItem[];
}

export interface HuggingFaceModelSearchResult {
  id: string;
  name: string;
  downloads: number;
  likes: number;
  pipeline_tag?: string | null;
  model_string_hf: string;
  model_string_vllm: string;
}

/**
 * A provider and the state of its stored credential.
 *
 * The key itself never leaves the server: `preview` is a masked hint that identifies
 * which key is stored without disclosing it. The list is served rather than hardcoded,
 * so adding a provider does not mean editing the settings form.
 */
export interface ProviderCredential {
  id: string;
  name: string;
  icon: string;
  /** Field name to send this credential under when saving. */
  setting_key: string;
  console_url: string;
  key_hint: string;
  preview: string;
  is_set: boolean;
}

/** How exposed the server is — the page's most consequential fact. */
export interface SecurityPosture {
  auth_required: boolean;
  host: string;
  is_loopback: boolean;
  warning?: string | null;
}

export interface AppSettings {
  providers: ProviderCredential[];
  security: SecurityPosture;
}

/**
 * Only the credentials being changed are sent; anything omitted is left alone.
 * Endpoints, model choices and execution limits come from the environment and are
 * deliberately not writable here.
 */
export interface SettingsUpdate {
  OPENAI_API_KEY?: string;
  GEMINI_API_KEY?: string;
  ANTHROPIC_API_KEY?: string;
  HUGGINGFACE_API_KEY?: string;
}

export type VLLMServerState =
  | 'idle'
  | 'pulling_image'
  | 'starting_container'
  | 'loading'
  | 'ready'
  | 'error'
  | 'stopped';

export interface VLLMDeploymentProgress {
  model_id?: string | null;
  state: VLLMServerState;
  message: string;
  logs_tail: string[];
  port: number;
  endpoint: string;
  container_id?: string | null;
  error?: string | null;
  /** Set when the container stopped on its own, so a crash reads as a crash. */
  exit_code?: number | null;
}

export interface VLLMDeployRequest {
  model_id: string;
  gpu_memory_utilization?: number;
  max_model_len?: number | null;
  enforce_eager?: boolean;
  dtype?: string;
  /** Executes modelling code published in the model repository. Off by default. */
  trust_remote_code?: boolean;
}

export interface GPUDevice {
  name: string;
  total_memory_mb: number;
}

export interface HostCapability {
  docker_available: boolean;
  gpus: GPUDevice[];
  total_vram_mb: number;
  accelerator: 'cuda' | 'cpu';
  image_present?: boolean | null;
}

export interface CachedModel {
  repo_id: string;
  size_bytes: number;
  modified_at: number;
}

export interface ModelCacheInfo {
  path: string;
  total_bytes: number;
  models: CachedModel[];
}
