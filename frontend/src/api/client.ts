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
  AudioItem,
  AudioKind,
  Modality,
  ModelClassification,
  RuntimeDescriptor,
  SpeechRequest,
  AudioJob,
  JobKind,
  MessageSpeech,
  VoiceList,
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

/** Shown whenever the server cannot be reached at all. */
export const OFFLINE_MESSAGE =
  "Kayak can't reach its server right now. It may be restarting, so try again in a moment.";

/** Shown when a failure carries nothing a person could act on. */
const GENERIC_MESSAGE = 'Something went wrong. Try again.';

/**
 * Error carrying the HTTP status, so callers can distinguish auth failures.
 *
 * `readable` marks a message written to be shown as it is -- the server's own
 * explanation. Anything else (a bare "Internal Server Error", a validation dump)
 * is replaced by the caller's fallback rather than put in front of someone.
 */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly readable = true
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/**
 * The sentence to show a user for a thrown value.
 *
 * Only messages written for people are passed through. The browser's own errors --
 * "Load failed" for a server that is restarting, "The string did not match the
 * expected pattern." for a response that was not JSON -- are accurate and mean
 * nothing to someone who does not code, so they become `fallback`, which callers
 * phrase as what failed and what to do ("Couldn't save the skill. Try again.").
 * `String(error)` was worse still: it prefixed every message with the class name.
 */
export function errorMessage(error: unknown, fallback: string = GENERIC_MESSAGE): string {
  if (error instanceof ApiError) {
    if (error.status === 0) return OFFLINE_MESSAGE;
    const text = error.message.trim();
    return error.readable && text ? text : fallback;
  }
  // Raised by the browser rather than written by Kayak.
  if (error instanceof TypeError || error instanceof SyntaxError) return fallback;
  if (typeof DOMException !== 'undefined' && error instanceof DOMException) return fallback;
  if (error instanceof Error) return error.message.trim() || fallback;
  const text = typeof error === 'string' ? error.trim() : '';
  return text || fallback;
}

/**
 * `fetch`, with an unreachable server reported as an ApiError.
 *
 * The browser rejects with a TypeError whose text differs per engine, so a server
 * restarting under the page was reported as "Load failed" in one place and
 * "Failed to fetch" in another, and neither said what to do.
 */
async function request(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, withAuth(init));
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(0, OFFLINE_MESSAGE);
  }
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
 * The explanation a failed response carries, if it carries one.
 *
 * Kayak answers every failure with `{"detail": "<sentence>"}`. Anything else -- a
 * plain-text error page, FastAPI's list of validation problems -- was not written
 * for a person, so it is reported as unreadable rather than shown.
 */
export function readErrorDetail(body: string): string | null {
  if (!body) return null;
  try {
    const detail = JSON.parse(body)?.detail;
    return typeof detail === 'string' && detail.trim() ? detail.trim() : null;
  } catch {
    return null;
  }
}

async function assertOk(res: Response): Promise<void> {
  if (res.ok) return;
  const body = await res.text().catch(() => '');
  const detail = readErrorDetail(body);
  throw new ApiError(res.status, detail ?? (body || res.statusText), detail !== null);
}

/** Fetches JSON from the API, throwing on non-OK responses. */
async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await request(url, init);
  await assertOk(res);
  return res.json();
}

/** Shorthand for a JSON request body on any method. */
function sendJSON(url: string, method: string, data: unknown): Promise<Response> {
  return request(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
}

async function fetchJSONSend<T>(url: string, method: string, data: unknown): Promise<T> {
  const res = await sendJSON(url, method, data);
  await assertOk(res);
  return res.json();
}

function fetchJSONPost<T>(url: string, data: unknown): Promise<T> {
  return fetchJSONSend<T>(url, 'POST', data);
}

async function requestVoid(url: string, method: string): Promise<void> {
  await assertOk(await request(url, { method }));
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
    requestVoid(`${API_BASE}/conversations/${id}`, 'DELETE'),

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

  /**
   * Fetches the start of a workspace file as text, for the text preview.
   *
   * Only the first `maxBytes` are asked for. The whole file used to be downloaded
   * and then cut down, so opening an agent's multi-gigabyte log hung the window
   * for the sake of showing its first half-megabyte.
   */
  readWorkspaceFileText: async (
    conversationId: string,
    path: string,
    maxBytes: number
  ): Promise<{ text: string; truncated: boolean }> => {
    const res = await request(api.workspaceFileUrl(conversationId, path), {
      headers: { Range: `bytes=0-${maxBytes - 1}` },
    });
    // An empty file has no first byte to send, which the server reports as an
    // unsatisfiable range rather than as empty.
    if (res.status === 416) return { text: '', truncated: false };
    await assertOk(res);
    const total = Number(res.headers.get('Content-Range')?.split('/')[1]);
    const text = await res.text();
    // A server that ignores the range sends everything; it is still cut here.
    return {
      text: text.length > maxBytes ? text.slice(0, maxBytes) : text,
      truncated: (Number.isFinite(total) && total > maxBytes) || text.length > maxBytes,
    };
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
    return request(`${API_BASE}/conversations/${conversationId}/fs/upload`, {
      method: 'POST',
      body: form,
    }).then(async (res) => {
      await assertOk(res);
      return res.json();
    });
  },

  resolveToolApproval: (
    conversationId: string,
    callId: string,
    approved: boolean
  ): Promise<{ status: string }> =>
    // The call id goes in the body, not the path: Gemini's ids are long and contain
    // "/", which no amount of encoding survives a round trip through the router.
    fetchJSONPost(
      `${API_BASE}/conversations/${conversationId}/tool-approvals`,
      { call_id: callId, approved }
    ),

  // Agents
  listAgents: (): Promise<AgentConfig[]> =>
    fetchJSON(`${API_BASE}/agents`),

  saveAgent: (agent: AgentConfig): Promise<AgentConfig> =>
    fetchJSONPost(`${API_BASE}/agents`, agent),

  deleteAgent: (id: string): Promise<void> =>
    requestVoid(`${API_BASE}/agents/${id}`, 'DELETE'),

  // Skills
  listSkills: (): Promise<Skill[]> =>
    fetchJSON(`${API_BASE}/skills`),

  saveSkill: (data: { name: string; description: string; instructions: string }): Promise<Skill> =>
    fetchJSONPost(`${API_BASE}/skills`, data),

  deleteSkill: (name: string): Promise<void> =>
    requestVoid(`${API_BASE}/skills/${encodeURIComponent(name)}`, 'DELETE'),

  // Tools
  listTools: (): Promise<ToolDefinition[]> =>
    fetchJSON(`${API_BASE}/tools`),

  listToolCategories: (): Promise<ToolCategoryInfo[]> =>
    fetchJSON(`${API_BASE}/tools/categories`),

  reloadTools: (): Promise<{ status: string; total_tools: number }> =>
    fetchJSON(`${API_BASE}/tools/reload`, { method: 'POST' }),

  deleteTool: (name: string): Promise<void> =>
    requestVoid(`${API_BASE}/tools/${encodeURIComponent(name)}`, 'DELETE'),

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

  /**
   * Rewrites one memory, or forgets it when `replacement` is empty, leaving every
   * other memory as the server has it -- including any an agent added meanwhile.
   */
  editMemory: (previous: string, replacement: string): Promise<string[]> =>
    fetchJSONSend<{ memories: string[] }>(`${API_BASE}/memories`, 'PATCH', {
      previous,
      replacement,
    }).then((data) => data.memories),

  // Tasks
  listTasks: (conversationId?: string): Promise<BackgroundTask[]> => {
    const url = conversationId ? `${API_BASE}/tasks?conversation_id=${conversationId}` : `${API_BASE}/tasks`;
    return fetchJSON(url);
  },

  stopTask: (taskId: string): Promise<void> =>
    requestVoid(`${API_BASE}/tasks/${taskId}/stop`, 'POST'),

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

  /** Searches one Hugging Face task; the task comes from a runtime descriptor. */
  searchHuggingFaceModels: (
    query: string,
    pipelineTag = 'text-generation'
  ): Promise<HuggingFaceModelSearchResult[]> =>
    fetchJSON(
      `${API_BASE}/models/huggingface/search?query=${encodeURIComponent(query)}` +
        `&pipeline_tag=${encodeURIComponent(pipelineTag)}`
    ),

  // Local model servers, one per modality.

  /** What each local runtime serves and can be told, so no client hardcodes it. */
  listInferenceRuntimes: (): Promise<RuntimeDescriptor[]> =>
    fetchJSON(`${API_BASE}/inference/runtimes`),

  /** Every local server's state, keyed by modality. */
  getInferenceStatus: (): Promise<Record<Modality, VLLMDeploymentProgress>> =>
    fetchJSON(`${API_BASE}/inference/status`),

  deployVLLMModel: (
    data: VLLMDeployRequest,
    modality: Modality = 'text',
  ): Promise<VLLMDeploymentProgress> =>
    fetchJSONPost(`${API_BASE}/inference/${modality}/deploy`, data),

  stopVLLMServer: (modality: Modality = 'text'): Promise<{ status: string; message: string }> =>
    fetchJSON(`${API_BASE}/inference/${modality}/stop`, { method: 'POST' }),

  getHostCapability: (): Promise<HostCapability> =>
    fetchJSON(`${API_BASE}/inference/hardware`),

  getVersions: (): Promise<InstalledVersions> =>
    fetchJSON(`${API_BASE}/settings/versions`),

  /** Plain-text diagnostics bundle, for a user to attach to a problem report. */
  getSupportBundle: async (): Promise<string> => {
    const response = await request(`${API_BASE}/settings/support-bundle`);
    await assertOk(response);
    return response.text();
  },

  /** Whether the launcher can serve MLX models on the Apple GPU, and why not if not. */
  getMetalStatus: (): Promise<MetalStatus> =>
    fetchJSON(`${API_BASE}/inference/metal`),

  getModelCache: (): Promise<ModelCacheInfo> =>
    fetchJSON(`${API_BASE}/inference/cache`),

  deleteCachedModel: (repoId: string): Promise<{ status: string; repo_id: string; freed_bytes: number }> =>
    fetchJSON(`${API_BASE}/inference/cache/${repoId.split('/').map(encodeURIComponent).join('/')}`, {
      method: 'DELETE',
    }),

  // The Audio workbench. Requests go through Kayak rather than to the audio server
  // directly: the browser may not be on the machine the server is published on, and
  // going through Kayak is what stores the result.

  /** Which local runtime should serve a repository, from the Hub's own metadata. */
  classifyModel: (repoId: string): Promise<ModelClassification> =>
    fetchJSON(`${API_BASE}/inference/classify?repo_id=${encodeURIComponent(repoId)}`),

  /** Voices the running speech model offers; empty when none is running. */
  getVoices: (): Promise<VoiceList> => fetchJSON(`${API_BASE}/audio/voices`),

  /**
   * Queues text to be spoken. Returns the job, not the audio: synthesis runs for
   * minutes and holding the request open would tie the work to this tab.
   */
  synthesizeSpeech: (data: SpeechRequest): Promise<AudioJob> =>
    fetchJSONPost(`${API_BASE}/audio/speech`, data),

  /**
   * Ensures a chat message has speech, and says where that stands.
   *
   * Returns immediately rather than the audio: the result is kept against the
   * message, so a reload can find work still in flight and asking twice is free.
   */
  requestMessageSpeech: (
    messageId: string,
    text: string,
    voice?: string | null
  ): Promise<MessageSpeech> =>
    fetchJSONPost(`${API_BASE}/audio/messages/${encodeURIComponent(messageId)}/speech`, {
      text,
      voice: voice ?? null,
    }),

  /** What speech exists, or is being made, for these messages. */
  getMessageSpeech: (messageIds: string[]): Promise<MessageSpeech[]> =>
    fetchJSON(
      `${API_BASE}/audio/messages/speech?ids=${messageIds.map(encodeURIComponent).join(',')}`
    ),

  /** Where a message's finished audio can be played from. */
  messageSpeechUrl: (messageId: string): string =>
    `${API_BASE}/audio/messages/${encodeURIComponent(messageId)}/speech/audio`,

  /** Transcribes a recording and returns the text, storing nothing. */
  listen: async (recording: Blob): Promise<string> => {
    const body = new FormData();
    // Named after what was actually recorded: Safari and the launcher's WebKit
    // record MP4, and a .webm name sends the decoder after the wrong container.
    const extension = recording.type.includes('mp4') ? 'm4a' : 'webm';
    body.append('file', recording, `dictation.${extension}`);
    const response = await request(`${API_BASE}/audio/listen`, { method: 'POST', body });
    await assertOk(response);
    return (await response.json()).text ?? '';
  },

  /** The queues: waiting, running, and recently finished, optionally by kind. */
  listAudioJobs: (kind?: JobKind): Promise<AudioJob[]> =>
    fetchJSON(`${API_BASE}/audio/jobs${kind ? `?kind=${kind}` : ''}`),

  /** Drops a queued job, or dismisses a finished one. */
  cancelAudioJob: (jobId: string): Promise<{ status: string; id: string }> =>
    fetchJSON(`${API_BASE}/audio/jobs/${jobId}`, { method: 'DELETE' }),

  /** Queues one or more recordings to be transcribed. Returns their jobs. */
  transcribeAudio: async (files: File[], language?: string): Promise<AudioJob[]> => {
    const body = new FormData();
    for (const file of files) body.append('files', file);
    if (language) body.append('language', language);
    // No Content-Type header: the browser has to set the multipart boundary itself.
    const response = await request(`${API_BASE}/audio/transcriptions`, { method: 'POST', body });
    await assertOk(response);
    return response.json();
  },

  listAudioItems: (kind?: AudioKind): Promise<AudioItem[]> =>
    fetchJSON(`${API_BASE}/audio/items${kind ? `?kind=${kind}` : ''}`),

  /** URL the <audio> element and the download link both point at. */
  audioFileUrl: (itemId: string): string => `${API_BASE}/audio/items/${itemId}/file`,

  deleteAudioItem: (itemId: string): Promise<{ status: string; id: string }> =>
    fetchJSON(`${API_BASE}/audio/items/${itemId}`, { method: 'DELETE' }),
};
