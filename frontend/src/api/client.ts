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
  ToolCategoryInfo,
  VLLMDeploymentProgress,
  VLLMDeployRequest,
  HostCapability,
  InstalledVersions,
  MetalStatus,
  ModelCacheInfo,
  SettingsUpdate,
  WorkspaceDirectoryListing
} from '../types';

const API_BASE = '/api';
const TOKEN_STORAGE_KEY = 'kayak_auth_token';
const TOKEN_HEADER = 'X-Kayak-Token';

/** Reads the shared secret this browser was configured with, if any. */
export function getStoredAuthToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

/** Persists the shared secret for subsequent requests in this browser. */
export function setStoredAuthToken(token: string | null): void {
  try {
    if (token) window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
    else window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    /* private browsing modes can reject storage writes */
  }
}

/** Error carrying the HTTP status, so callers can distinguish auth failures. */
export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

/**
 * The sentence to show a user for a thrown value.
 *
 * `String(error)` prefixes it with the class name, so a message written to be read by
 * someone who does not code arrived as "ApiError: No Google Gemini API key is
 * configured...". The prefix says nothing they can act on.
 */
export function errorMessage(error: unknown): string {
  // An Error is asked for its message directly: String() on a message-less one yields
  // the bare word "Error", which reads as a bug rather than an explanation.
  if (error instanceof Error) return error.message.trim() || 'Something went wrong.';
  const text = String(error).trim();
  return text || 'Something went wrong.';
}

function withAuth(init?: RequestInit): RequestInit {
  const token = getStoredAuthToken();
  const headers = new Headers(init?.headers);
  if (token) headers.set(TOKEN_HEADER, token);
  // Credentials carry the session cookie, which is what lets EventSource streams
  // authenticate: they cannot send custom headers.
  return { ...init, headers, credentials: 'same-origin' };
}

/**
 * Extracts the human-readable part of an error response.
 *
 * FastAPI answers with `{"detail": ...}`, so showing the raw body put JSON braces in
 * front of users for every failure the UI reports.
 */
function readErrorDetail(body: string): string | null {
  if (!body) return null;
  try {
    const parsed = JSON.parse(body);
    const detail = parsed?.detail;
    if (typeof detail === 'string') return detail;
    // Validation errors arrive as a list of {loc, msg, type} entries.
    if (Array.isArray(detail)) {
      const messages = detail.map((item) => item?.msg).filter(Boolean);
      if (messages.length) return messages.join('; ');
    }
  } catch {
    /* not JSON; fall through to the raw text */
  }
  return body;
}

async function assertOk(res: Response): Promise<void> {
  if (res.ok) return;
  const body = await res.text().catch(() => '');
  throw new ApiError(res.status, readErrorDetail(body) || res.statusText);
}

/** Fetches JSON from the API, throwing on non-OK responses. */
async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, withAuth(init));
  await assertOk(res);
  return res.json();
}

/** Shorthand for a JSON request body on any method. */
function sendJSON(url: string, method: string, data: unknown): Promise<Response> {
  return fetch(
    url,
    withAuth({
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
  );
}

async function fetchJSONSend<T>(url: string, method: string, data: unknown): Promise<T> {
  const res = await sendJSON(url, method, data);
  await assertOk(res);
  return res.json();
}

function fetchJSONPost<T>(url: string, data: unknown): Promise<T> {
  return fetchJSONSend<T>(url, 'POST', data);
}

async function requestVoid(url: string, method: string, action: string): Promise<void> {
  const res = await fetch(url, withAuth({ method }));
  if (!res.ok) throw new ApiError(res.status, `Failed to ${action}: ${res.statusText}`);
}

export const api = {
  // Conversations
  listConversations: (): Promise<Conversation[]> =>
    fetchJSON(`${API_BASE}/conversations`),

  getConversation: (id: string): Promise<{ conversation: Conversation; messages: Message[] }> =>
    fetchJSON(`${API_BASE}/conversations/${id}`),

  createConversation: (data: { title?: string; agent_id: string; initial_message?: string }): Promise<Conversation> =>
    fetchJSONPost(`${API_BASE}/conversations`, data),

  deleteConversation: (id: string): Promise<void> =>
    requestVoid(`${API_BASE}/conversations/${id}`, 'DELETE', 'delete conversation'),

  cancelConversation: (id: string): Promise<{ status: string }> =>
    fetchJSON(`${API_BASE}/conversations/${id}/cancel`, { method: 'POST' }),

  /** Truncates the conversation at a turn and returns the prompt that started it. */
  revertToMessage: (
    id: string,
    messageId: string
  ): Promise<{ status: string; removed: number; prompt: string | null }> =>
    fetchJSONPost(`${API_BASE}/conversations/${id}/revert`, { message_id: messageId }),

  /** Discards a turn and generates it again from the same history. */
  retryFromMessage: (id: string, messageId: string): Promise<{ status: string }> =>
    fetchJSONPost(`${API_BASE}/conversations/${id}/retry`, { message_id: messageId }),

  /** Copies the conversation up to a turn into a new one. */
  branchFromMessage: (id: string, messageId: string): Promise<Conversation> =>
    fetchJSONPost(`${API_BASE}/conversations/${id}/branch`, { message_id: messageId }),

  sendMessage: (conversationId: string, content: string): Promise<Message> =>
    fetchJSONPost(`${API_BASE}/conversations/${conversationId}/messages`, { content }),

  // Conversation workspace: filesystem, uploads, and container terminal
  listWorkspaceFiles: (conversationId: string, path = '.'): Promise<WorkspaceDirectoryListing> =>
    fetchJSON(
      `${API_BASE}/conversations/${conversationId}/fs?path=${encodeURIComponent(path)}`
    ),

  /** URL serving a workspace file's raw bytes, for previews and downloads. */
  workspaceFileUrl: (conversationId: string, path: string): string =>
    `${API_BASE}/conversations/${conversationId}/fs/file?path=${encodeURIComponent(path)}`,

  /** Fetches a workspace file as text, for the text preview. */
  readWorkspaceFileText: async (conversationId: string, path: string): Promise<string> => {
    const res = await fetch(api.workspaceFileUrl(conversationId, path), withAuth());
    await assertOk(res);
    return res.text();
  },

  /**
   * Uploads files into the workspace. Each entry's `path` is the destination
   * relative to the workspace root, which is how folder uploads keep their shape.
   */
  uploadWorkspaceFiles: (
    conversationId: string,
    entries: Array<{ file: File; path: string }>
  ): Promise<{ status: string; count: number }> => {
    const form = new FormData();
    for (const entry of entries) form.append('files', entry.file, entry.path);
    return fetch(
      `${API_BASE}/conversations/${conversationId}/fs/upload`,
      withAuth({ method: 'POST', body: form })
    ).then(async (res) => {
      await assertOk(res);
      return res.json();
    });
  },

  resolveToolApproval: (
    conversationId: string,
    callId: string,
    approved: boolean
  ): Promise<{ status: string }> =>
    fetchJSONPost(
      `${API_BASE}/conversations/${conversationId}/tool-approvals/${encodeURIComponent(callId)}`,
      { approved }
    ),

  // Agents
  listAgents: (): Promise<AgentConfig[]> =>
    fetchJSON(`${API_BASE}/agents`),

  getAgent: (id: string): Promise<AgentConfig> =>
    fetchJSON(`${API_BASE}/agents/${id}`),

  saveAgent: (agent: AgentConfig): Promise<AgentConfig> =>
    fetchJSONPost(`${API_BASE}/agents`, agent),

  deleteAgent: (id: string): Promise<void> =>
    requestVoid(`${API_BASE}/agents/${id}`, 'DELETE', 'delete agent'),

  // Skills
  listSkills: (): Promise<Skill[]> =>
    fetchJSON(`${API_BASE}/skills`),

  getSkill: (name: string): Promise<Skill> =>
    fetchJSON(`${API_BASE}/skills/${name}`),

  saveSkill: (data: { name: string; description: string; instructions: string }): Promise<Skill> =>
    fetchJSONPost(`${API_BASE}/skills`, data),

  deleteSkill: (name: string): Promise<void> =>
    requestVoid(`${API_BASE}/skills/${encodeURIComponent(name)}`, 'DELETE', 'delete skill'),

  // Tools
  listTools: (): Promise<ToolDefinition[]> =>
    fetchJSON(`${API_BASE}/tools`),

  listToolCategories: (): Promise<ToolCategoryInfo[]> =>
    fetchJSON(`${API_BASE}/tools/categories`),

  reloadTools: (): Promise<{ status: string; total_tools: number }> =>
    fetchJSON(`${API_BASE}/tools/reload`, { method: 'POST' }),

  deleteTool: (name: string): Promise<void> =>
    requestVoid(`${API_BASE}/tools/${encodeURIComponent(name)}`, 'DELETE', 'delete tool'),

  // Tool Builder
  verifyTool: (data: { tool_name: string; tool_code: string; verify_code: string }): Promise<VerifyToolResponse> =>
    fetchJSONPost(`${API_BASE}/tool-builder/verify`, data),

  activateTool: (data: { tool_name: string; tool_code: string; verify_code: string }): Promise<ToolDefinition> =>
    fetchJSONPost(`${API_BASE}/tool-builder/activate`, data),

  // Memory -- what Kayak has learned from the user, shared by every agent.
  listMemories: (): Promise<string[]> =>
    fetchJSON<{ memories: string[] }>(`${API_BASE}/memories`).then((data) => data.memories),

  addMemory: (content: string): Promise<string[]> =>
    fetchJSONPost<{ memories: string[] }>(`${API_BASE}/memories`, { content })
      .then((data) => data.memories),

  replaceMemories: (memories: string[]): Promise<string[]> =>
    fetchJSONSend<{ memories: string[] }>(`${API_BASE}/memories`, 'PUT', { memories })
      .then((data) => data.memories),

  // Tasks
  listTasks: (conversationId?: string): Promise<BackgroundTask[]> => {
    const url = conversationId ? `${API_BASE}/tasks?conversation_id=${conversationId}` : `${API_BASE}/tasks`;
    return fetchJSON(url);
  },

  stopTask: (taskId: string): Promise<void> =>
    requestVoid(`${API_BASE}/tasks/${taskId}/stop`, 'POST', 'stop task'),

  sendTaskInput: (taskId: string, input: string): Promise<{ status: string }> =>
    fetchJSONPost(`${API_BASE}/tasks/${taskId}/input`, { input }),

  // Auth (only enforced when the server has KAYAK_AUTH_TOKEN set)
  getAuthStatus: (): Promise<{ auth_required: boolean; authenticated: boolean }> =>
    fetchJSON(`${API_BASE}/auth/status`),

  createAuthSession: (token: string): Promise<{ status: string }> =>
    fetchJSONPost(`${API_BASE}/auth/session`, { token }),

  // Settings
  getSettings: (): Promise<AppSettings> =>
    fetchJSON(`${API_BASE}/settings`),

  updateSettings: (update: SettingsUpdate): Promise<{ status: string; settings: AppSettings }> =>
    fetchJSONPost(`${API_BASE}/settings`, update),

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

  getHostCapability: (): Promise<HostCapability> =>
    fetchJSON(`${API_BASE}/vllm/hardware`),

  getVersions: (): Promise<InstalledVersions> =>
    fetchJSON(`${API_BASE}/settings/versions`),

  /** Plain-text diagnostics bundle, for a user to attach to a problem report. */
  getSupportBundle: async (): Promise<string> => {
    const response = await fetch(`${API_BASE}/settings/support-bundle`, withAuth());
    if (!response.ok) throw new Error(`Could not build the report (${response.status})`);
    return response.text();
  },

  getMetalStatus: (): Promise<MetalStatus> =>
    fetchJSON(`${API_BASE}/vllm/metal`),

  startMetal: (modelId: string): Promise<MetalStatus> =>
    fetchJSONPost(`${API_BASE}/vllm/metal/start`, { model_id: modelId }),

  stopMetal: (): Promise<MetalStatus> =>
    fetchJSON(`${API_BASE}/vllm/metal/stop`, { method: 'POST' }),

  getModelCache: (): Promise<ModelCacheInfo> =>
    fetchJSON(`${API_BASE}/vllm/cache`),

  deleteCachedModel: (repoId: string): Promise<{ status: string; repo_id: string; freed_bytes: number }> =>
    fetchJSON(`${API_BASE}/vllm/cache/${repoId.split('/').map(encodeURIComponent).join('/')}`, {
      method: 'DELETE',
    }),
};
