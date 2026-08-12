import { 
  AgentConfig, 
  AppSettings, 
  BackgroundTask, 
  Conversation, 
  Message, 
  Skill, 
  ToolDefinition, 
  VerifyToolResponse,
  ProviderModels,
  HuggingFaceModelSearchResult,
  VLLMDeploymentProgress,
  VLLMDeployRequest
} from '../types';

const API_BASE = '/api';

/** Fetches JSON from the API, throwing on non-OK responses. */
async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`API error ${res.status}: ${body || res.statusText}`);
  }
  return res.json();
}

/** Shorthand for JSON POST/PUT requests. */
function postJSON(url: string, data: unknown): Promise<Response> {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}

async function fetchJSONPost<T>(url: string, data: unknown): Promise<T> {
  const res = await postJSON(url, data);
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(`API error ${res.status}: ${body || res.statusText}`);
  }
  return res.json();
}

export const api = {
  // Conversations
  listConversations: (): Promise<Conversation[]> =>
    fetchJSON(`${API_BASE}/conversations`),

  getConversation: (id: string): Promise<{ conversation: Conversation; messages: Message[] }> =>
    fetchJSON(`${API_BASE}/conversations/${id}`),

  createConversation: (data: { title?: string; agent_id: string; isolated_container?: boolean; initial_message?: string }): Promise<Conversation> =>
    fetchJSONPost(`${API_BASE}/conversations`, data),

  deleteConversation: async (id: string): Promise<void> => {
    const res = await fetch(`${API_BASE}/conversations/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`Failed to delete conversation: ${res.statusText}`);
  },

  cancelConversation: (id: string): Promise<{ status: string }> =>
    fetchJSON(`${API_BASE}/conversations/${id}/cancel`, { method: 'POST' }),

  sendMessage: (conversationId: string, content: string): Promise<Message> =>
    fetchJSONPost(`${API_BASE}/conversations/${conversationId}/messages`, { content }),

  // Agents
  listAgents: (): Promise<AgentConfig[]> =>
    fetchJSON(`${API_BASE}/agents`),

  getAgent: (id: string): Promise<AgentConfig> =>
    fetchJSON(`${API_BASE}/agents/${id}`),

  saveAgent: (agent: AgentConfig): Promise<AgentConfig> =>
    fetchJSONPost(`${API_BASE}/agents`, agent),

  deleteAgent: async (id: string): Promise<void> => {
    const res = await fetch(`${API_BASE}/agents/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`Failed to delete agent: ${res.statusText}`);
  },

  // Skills
  listSkills: (): Promise<Skill[]> =>
    fetchJSON(`${API_BASE}/skills`),

  getSkill: (name: string): Promise<Skill> =>
    fetchJSON(`${API_BASE}/skills/${name}`),

  saveSkill: (data: { name: string; description: string; instructions: string }): Promise<Skill> =>
    fetchJSONPost(`${API_BASE}/skills`, data),

  deleteSkill: async (name: string): Promise<void> => {
    const res = await fetch(`${API_BASE}/skills/${name}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`Failed to delete skill: ${res.statusText}`);
  },

  // Tools
  listTools: (): Promise<ToolDefinition[]> =>
    fetchJSON(`${API_BASE}/tools`),

  reloadTools: (): Promise<{ status: string; total_tools: number }> =>
    fetchJSON(`${API_BASE}/tools/reload`, { method: 'POST' }),

  deleteTool: async (name: string): Promise<void> => {
    const res = await fetch(`${API_BASE}/tools/${name}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`Failed to delete tool: ${res.statusText}`);
  },

  // Tool Builder
  verifyTool: (data: { tool_name: string; tool_code: string; verify_code: string }): Promise<VerifyToolResponse> =>
    fetchJSONPost(`${API_BASE}/tool-builder/verify`, data),

  activateTool: (data: { tool_name: string; tool_code: string; verify_code: string }): Promise<ToolDefinition> =>
    fetchJSONPost(`${API_BASE}/tool-builder/activate`, data),

  // Tasks
  listTasks: (conversationId?: string): Promise<BackgroundTask[]> => {
    const url = conversationId ? `${API_BASE}/tasks?conversation_id=${conversationId}` : `${API_BASE}/tasks`;
    return fetchJSON(url);
  },

  stopTask: async (taskId: string): Promise<void> => {
    const res = await fetch(`${API_BASE}/tasks/${taskId}/stop`, { method: 'POST' });
    if (!res.ok) throw new Error(`Failed to stop task: ${res.statusText}`);
  },

  sendTaskInput: (taskId: string, input: string): Promise<{ status: string }> =>
    fetchJSONPost(`${API_BASE}/tasks/${taskId}/input`, { input }),

  // Settings
  getSettings: (): Promise<AppSettings> =>
    fetchJSON(`${API_BASE}/settings`),

  updateSettings: (settings: Partial<AppSettings>): Promise<{ status: string; settings: AppSettings }> =>
    fetchJSONPost(`${API_BASE}/settings`, settings),

  // Models Discovery & Hugging Face Hub
  listModels: (): Promise<ProviderModels[]> =>
    fetchJSON(`${API_BASE}/models`),

  searchHuggingFaceModels: (query: string): Promise<HuggingFaceModelSearchResult[]> =>
    fetchJSON(`${API_BASE}/models/huggingface/search?query=${encodeURIComponent(query)}`),

  // vLLM Local Container Orchestration
  getVLLMStatus: (): Promise<VLLMDeploymentProgress> =>
    fetchJSON(`${API_BASE}/vllm/status`),

  deployVLLMModel: (data: VLLMDeployRequest): Promise<VLLMDeploymentProgress> =>
    fetchJSONPost(`${API_BASE}/vllm/deploy`, data),

  stopVLLMServer: (): Promise<{ status: string; message: string }> =>
    fetchJSON(`${API_BASE}/vllm/stop`, { method: 'POST' }),

  getVLLMServedModels: async (): Promise<any[]> => {
    try {
      return await fetchJSON(`${API_BASE}/vllm/models`);
    } catch {
      return [];
    }
  },
};
