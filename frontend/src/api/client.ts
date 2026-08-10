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
  HuggingFaceModelSearchResult
} from '../types';

const API_BASE = '/api';

export const api = {
  // Conversations
  listConversations: async (): Promise<Conversation[]> => {
    const res = await fetch(`${API_BASE}/conversations`);
    return res.json();
  },

  getConversation: async (id: string): Promise<{ conversation: Conversation; messages: Message[] }> => {
    const res = await fetch(`${API_BASE}/conversations/${id}`);
    return res.json();
  },

  createConversation: async (data: { title?: string; agent_id: string; isolated_container?: boolean; initial_message?: string }): Promise<Conversation> => {
    const res = await fetch(`${API_BASE}/conversations`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  deleteConversation: async (id: string): Promise<void> => {
    await fetch(`${API_BASE}/conversations/${id}`, { method: 'DELETE' });
  },

  cancelConversation: async (id: string): Promise<{ status: string }> => {
    const res = await fetch(`${API_BASE}/conversations/${id}/cancel`, { method: 'POST' });
    return res.json();
  },

  sendMessage: async (conversationId: string, content: string): Promise<Message> => {
    const res = await fetch(`${API_BASE}/conversations/${conversationId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    return res.json();
  },

  // Agents
  listAgents: async (): Promise<AgentConfig[]> => {
    const res = await fetch(`${API_BASE}/agents`);
    return res.json();
  },

  getAgent: async (id: string): Promise<AgentConfig> => {
    const res = await fetch(`${API_BASE}/agents/${id}`);
    return res.json();
  },

  saveAgent: async (agent: AgentConfig): Promise<AgentConfig> => {
    const res = await fetch(`${API_BASE}/agents`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(agent),
    });
    return res.json();
  },

  deleteAgent: async (id: string): Promise<void> => {
    await fetch(`${API_BASE}/agents/${id}`, { method: 'DELETE' });
  },

  // Skills
  listSkills: async (): Promise<Skill[]> => {
    const res = await fetch(`${API_BASE}/skills`);
    return res.json();
  },

  getSkill: async (name: string): Promise<Skill> => {
    const res = await fetch(`${API_BASE}/skills/${name}`);
    return res.json();
  },

  saveSkill: async (data: { name: string; description: string; instructions: string }): Promise<Skill> => {
    const res = await fetch(`${API_BASE}/skills`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  deleteSkill: async (name: string): Promise<void> => {
    await fetch(`${API_BASE}/skills/${name}`, { method: 'DELETE' });
  },

  // Tools
  listTools: async (): Promise<ToolDefinition[]> => {
    const res = await fetch(`${API_BASE}/tools`);
    return res.json();
  },

  reloadTools: async (): Promise<any> => {
    const res = await fetch(`${API_BASE}/tools/reload`, { method: 'POST' });
    return res.json();
  },

  deleteTool: async (name: string): Promise<void> => {
    await fetch(`${API_BASE}/tools/${name}`, { method: 'DELETE' });
  },

  // Tool Builder
  verifyTool: async (data: { tool_name: string; tool_code: string; verify_code: string }): Promise<VerifyToolResponse> => {
    const res = await fetch(`${API_BASE}/tool-builder/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  activateTool: async (data: { tool_name: string; tool_code: string; verify_code: string }): Promise<ToolDefinition> => {
    const res = await fetch(`${API_BASE}/tool-builder/activate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  // Tasks
  listTasks: async (conversationId?: string): Promise<BackgroundTask[]> => {
    const url = conversationId ? `${API_BASE}/tasks?conversation_id=${conversationId}` : `${API_BASE}/tasks`;
    const res = await fetch(url);
    return res.json();
  },

  stopTask: async (taskId: string): Promise<void> => {
    await fetch(`${API_BASE}/tasks/${taskId}/stop`, { method: 'POST' });
  },

  sendTaskInput: async (taskId: string, input: string): Promise<void> => {
    await fetch(`${API_BASE}/tasks/${taskId}/input`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input }),
    });
  },

  // Settings
  getSettings: async (): Promise<AppSettings> => {
    const res = await fetch(`${API_BASE}/settings`);
    return res.json();
  },

  updateSettings: async (settings: Partial<AppSettings>): Promise<{ status: string; settings: AppSettings }> => {
    const res = await fetch(`${API_BASE}/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
    return res.json();
  },

  // Models Discovery & Hugging Face Hub
  listModels: async (): Promise<ProviderModels[]> => {
    const res = await fetch(`${API_BASE}/models`);
    return res.json();
  },

  searchHuggingFaceModels: async (query: string): Promise<HuggingFaceModelSearchResult[]> => {
    const res = await fetch(`${API_BASE}/models/huggingface/search?query=${encodeURIComponent(query)}`);
    return res.json();
  },
};
