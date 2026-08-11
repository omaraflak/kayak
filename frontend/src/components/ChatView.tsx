import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Conversation, AgentConfig, VLLMDeploymentProgress } from '../types';
import { api } from '../api/client';
import { ChatPane } from './ChatPane';
import { 
  Bot, 
  Cpu, 
  Check,
  Sparkles,
  Send,
  Loader2,
  CircleDot,
  AlertCircle,
  Play
} from 'lucide-react';

interface ChatViewProps {
  conversationId: string | null;
  agents: AgentConfig[];
  onCreateConversation: (data: { title?: string; agent_id: string; isolated_container: boolean; initial_message?: string }) => void;
  onRefreshConversations?: () => void;
}

export const ChatView: React.FC<ChatViewProps> = ({ 
  conversationId, 
  agents,
  onCreateConversation,
  onRefreshConversations
}) => {
  const [conversation, setConversation] = useState<Conversation | null>(null);

  // Draft mode state (when conversationId is null)
  const [draftAgentId, setDraftAgentId] = useState<string>(agents[0]?.id || 'general');
  const [draftUseContainer, setDraftUseContainer] = useState<boolean>(false);
  const [draftInput, setDraftInput] = useState<string>('');
  const [vllmStatus, setVllmStatus] = useState<VLLMDeploymentProgress | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  const loadConversationData = async () => {
    if (!conversationId) {
      setConversation(null);
      return;
    }
    try {
      const data = await api.getConversation(conversationId);
      setConversation(data.conversation);
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
  };

  useEffect(() => {
    loadConversationData();
  }, [conversationId]);

  useEffect(() => {
    if (agents.length > 0 && !agents.some((agent) => agent.id === draftAgentId)) {
      setDraftAgentId(agents[0].id);
    }
  }, [agents]);

  // Check if any agent uses a vLLM model
  const hasAnyVllmAgent = agents.some((a) => a.model.startsWith('vllm/'));

  // Subscribe to vLLM status via SSE when any agent uses vLLM
  useEffect(() => {
    if (!hasAnyVllmAgent) {
      setVllmStatus(null);
      return;
    }

    // Fetch initial status
    api.getVLLMStatus()
      .then((data) => setVllmStatus(data))
      .catch((err) => console.error('Failed to fetch vLLM status:', err));

    // SSE for real-time updates
    const es = new EventSource('/api/vllm/events');
    eventSourceRef.current = es;

    const handleEvent = (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data);
        if (payload.data) {
          setVllmStatus(payload.data);
        }
      } catch { /* ignore parse errors */ }
    };

    es.addEventListener('status', handleEvent);
    es.addEventListener('update', handleEvent);

    return () => {
      es.close();
      eventSourceRef.current = null;
    };
  }, [hasAnyVllmAgent]);

  // Helper: extract HF model id from an agent model string like "vllm/Org/Model"
  const getVllmModelId = useCallback((model: string): string | null => {
    if (!model.startsWith('vllm/')) return null;
    return model.slice('vllm/'.length);
  }, []);

  const activeAgent = conversationId 
    ? agents.find((agent) => agent.id === conversation?.agent_id)
    : agents.find((agent) => agent.id === draftAgentId);

  // Compute whether the selected agent's vLLM model is ready
  const selectedAgentVllmModelId = activeAgent ? getVllmModelId(activeAgent.model) : null;
  const isVllmAgent = selectedAgentVllmModelId !== null;
  const loadingStates = ['pulling_image', 'starting_container', 'loading'];
  const isVllmModelReady =
    !isVllmAgent ||
    (vllmStatus?.state === 'ready' && vllmStatus?.model_id === selectedAgentVllmModelId);
  const isVllmModelLoading =
    isVllmAgent &&
    vllmStatus !== null &&
    loadingStates.includes(vllmStatus.state) &&
    vllmStatus.model_id === selectedAgentVllmModelId;

  // Deploys the vLLM model for the currently selected agent
  const handleStartModel = useCallback(async (modelId: string) => {
    try {
      await api.deployVLLMModel({ model_id: modelId });
    } catch (err) {
      console.error('Failed to deploy vLLM model:', err);
    }
  }, []);

  // Helper: get vLLM badge info for a given agent
  const getVllmBadge = useCallback(
    (agent: AgentConfig): { color: 'green' | 'yellow' | 'red'; label: string; spinning: boolean; canStart: boolean } | null => {
      const modelId = getVllmModelId(agent.model);
      if (!modelId) return null; // cloud model — no badge
      if (!vllmStatus) return { color: 'red', label: 'Not Running', spinning: false, canStart: true };

      const isThisModel = vllmStatus.model_id === modelId;

      if (vllmStatus.state === 'ready' && isThisModel) {
        return { color: 'green', label: 'Ready', spinning: false, canStart: false };
      }
      if (loadingStates.includes(vllmStatus.state) && isThisModel) {
        return { color: 'yellow', label: 'Loading...', spinning: true, canStart: false };
      }
      return { color: 'red', label: 'Not Running', spinning: false, canStart: true };
    },
    [vllmStatus, getVllmModelId]
  );


  const handleDraftSend = (event?: React.FormEvent) => {
    if (event) event.preventDefault();
    const text = draftInput.trim();
    if (!text || !isVllmModelReady) return;

    setDraftInput('');
    onCreateConversation({
      agent_id: draftAgentId,
      isolated_container: draftUseContainer,
      initial_message: text,
    });
  };

  return (
    <div className="flex-1 flex flex-col h-screen bg-zinc-50 overflow-hidden">
      {/* Top Header Bar */}
      <div className="h-16 border-b border-zinc-200 px-8 flex items-center justify-between bg-white shrink-0">
        <div className="flex items-center space-x-3 truncate">
          <h2 className="font-bold text-sm text-zinc-900 truncate max-w-md">
            {conversationId ? (conversation?.title || 'Conversation') : 'New Conversation'}
          </h2>

          {conversationId ? (
            conversation?.isolated_container ? (
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-50 text-blue-700 border border-blue-200 shrink-0">
                <span>🐳</span> Docker Sandbox
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-zinc-100 text-zinc-600 border border-zinc-200 shrink-0">
                <Cpu className="w-3.5 h-3.5 text-zinc-500" /> Host Workspace
              </span>
            )
          ) : null}

          {activeAgent && (
            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-zinc-100 text-zinc-800 border border-zinc-200 font-mono shrink-0">
              <Bot className="w-3.5 h-3.5 text-zinc-500" /> {activeAgent.name} ({activeAgent.model})
            </span>
          )}
        </div>
      </div>

      {/* Main Area: Draft Setup Widget vs Active Chat Pane */}
      {!conversationId ? (
        <div className="flex-1 flex flex-col justify-between overflow-hidden">
          {/* Centered Agent Profile Selection Widget */}
          <div className="flex-1 flex flex-col justify-center items-center px-6 py-4 overflow-hidden">
            <div className="w-full max-w-2xl flex flex-col space-y-4 max-h-full">
              {/* Header Title */}
              <div className="text-center space-y-1.5 shrink-0">
                <div className="w-12 h-12 rounded-2xl bg-white border border-zinc-200 flex items-center justify-center text-2xl mx-auto shadow-xs">
                  🛶
                </div>
                <h3 className="text-base font-bold text-zinc-900 tracking-tight">
                  How can Kayak help you today?
                </h3>
                <p className="text-xs text-zinc-500 leading-relaxed">
                  Select an agent profile and execution environment below. Type your initial prompt to begin.
                </p>
              </div>

              {/* Agent Profile Selection Widget Container */}
              <div className="bg-white border border-zinc-200 rounded-2xl p-5 shadow-xs flex flex-col space-y-3 overflow-hidden">
                <label className="block text-xs font-bold uppercase tracking-wider text-zinc-700 flex items-center gap-1.5 shrink-0">
                  <Bot className="w-4 h-4 text-indigo-600" /> Choose Agent Profile
                </label>

                {/* Inner Cards Grid: Scrollable with Clean Scrollbar */}
                <div className="overflow-y-auto max-h-[260px] pr-1.5 space-y-2">
                  <div className="grid grid-cols-2 gap-2.5">
                    {agents.map((agent) => {
                      const isSelected = draftAgentId === agent.id;
                      return (
                        <div
                          key={agent.id}
                          onClick={() => setDraftAgentId(agent.id)}
                          className={`p-3 rounded-xl border cursor-pointer transition-all flex flex-col justify-between ${
                            isSelected
                              ? 'bg-indigo-50/80 border border-indigo-600 text-zinc-950 shadow-xs ring-1 ring-indigo-500/20'
                              : 'bg-white border-zinc-200 text-zinc-700 hover:border-zinc-300 hover:bg-zinc-50/70 shadow-xs'
                          }`}
                        >
                          <div>
                            <div className="flex items-center justify-between mb-1">
                              <span className="font-semibold text-xs text-zinc-900">{agent.name}</span>
                              <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${
                                isSelected 
                                  ? 'bg-indigo-100 text-indigo-800 border-indigo-200' 
                                  : 'bg-zinc-100 text-zinc-600 border-zinc-200'
                              }`}>
                                {agent.model.split('/')[1] || agent.model}
                              </span>
                            </div>
                            <p className="text-[11px] text-zinc-500 line-clamp-2 leading-relaxed">
                              {agent.description}
                            </p>
                          </div>

                          <div className="mt-2.5 pt-2 border-t border-zinc-100 flex items-center justify-between text-[10px]">
                            <span className="text-zinc-400 font-mono">
                              {agent.allowed_tools?.length || 0} tools · {agent.preloaded_skills?.length || 0} skills
                            </span>
                            <div className="flex items-center gap-2">
                              {(() => {
                                const badge = getVllmBadge(agent);
                                if (!badge) return null;
                                const colorClasses = {
                                  green: 'text-emerald-600 bg-emerald-50 border-emerald-200',
                                  yellow: 'text-amber-600 bg-amber-50 border-amber-200',
                                  red: 'text-zinc-500 bg-zinc-50 border-zinc-200',
                                };
                                if (badge.canStart) {
                                  return (
                                    <button
                                      type="button"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        const modelId = getVllmModelId(agent.model);
                                        if (modelId) handleStartModel(modelId);
                                      }}
                                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold border border-indigo-300 bg-indigo-50 text-indigo-700 hover:bg-indigo-100 hover:border-indigo-400 transition-colors"
                                    >
                                      <Play className="w-3 h-3" />
                                      Start
                                    </button>
                                  );
                                }
                                return (
                                  <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-semibold border ${colorClasses[badge.color]}`}>
                                    {badge.spinning ? (
                                      <Loader2 className="w-3 h-3 animate-spin" />
                                    ) : (
                                      <CircleDot className="w-3 h-3" />
                                    )}
                                    {badge.label}
                                  </span>
                                );
                              })()}
                              {isSelected && (
                                <span className="inline-flex items-center gap-1 text-indigo-600 font-bold">
                                  <Check className="w-3.5 h-3.5 stroke-[3]" /> Selected
                                </span>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Execution Sandbox Isolation Toggle */}
                <div className="pt-3 border-t border-zinc-100 flex items-center justify-between shrink-0">
                  <div>
                    <div className="text-xs font-semibold text-zinc-900 flex items-center gap-1.5">
                      <span>🐳 Docker Sandbox Isolation</span>
                    </div>
                    <div className="text-[11px] text-zinc-500">
                      Run shell commands and code in an isolated container instead of host
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setDraftUseContainer(!draftUseContainer)}
                    className={`w-11 h-6 flex items-center rounded-full p-1 cursor-pointer transition-colors duration-200 ease-in-out ${
                      draftUseContainer ? 'bg-indigo-600' : 'bg-zinc-200'
                    }`}
                  >
                    <div
                      className={`bg-white w-4 h-4 rounded-full shadow-md transform transition-transform duration-200 ease-in-out ${
                        draftUseContainer ? 'translate-x-5' : 'translate-x-0'
                      }`}
                    />
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Bottom Docked Message Composer */}
          <div className="p-4 bg-white border-t border-zinc-200 shrink-0">
            <form
              onSubmit={handleDraftSend}
              className="max-w-4xl mx-auto relative bg-zinc-50 border border-zinc-300 rounded-xl overflow-hidden focus-within:bg-white focus-within:border-indigo-600 focus-within:ring-1 focus-within:ring-indigo-600 shadow-xs transition-all"
            >
              <textarea
                value={draftInput}
                onChange={(event) => setDraftInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    handleDraftSend();
                  }
                }}
                rows={2}
                disabled={!isVllmModelReady}
                placeholder={
                  !isVllmModelReady
                    ? 'Waiting for vLLM model to be ready...'
                    : `Message ${activeAgent?.name || 'Kayak Agent'}... (Enter to send, Shift+Enter for new line)`
                }
                className={`w-full bg-transparent px-3.5 py-2.5 text-xs text-zinc-900 placeholder-zinc-400 focus:outline-none resize-none leading-relaxed ${
                  !isVllmModelReady ? 'opacity-50 cursor-not-allowed' : ''
                }`}
              />

              <div className="flex items-center justify-between px-3.5 py-2 bg-zinc-100/50 border-t border-zinc-200">
                <div className="flex items-center space-x-2 text-[11px] text-zinc-500">
                  {!isVllmModelReady ? (
                    isVllmModelLoading ? (
                      <Loader2 className="w-3.5 h-3.5 text-amber-500 animate-spin" />
                    ) : (
                      <AlertCircle className="w-3.5 h-3.5 text-zinc-400" />
                    )
                  ) : (
                    <Sparkles className="w-3.5 h-3.5 text-indigo-600" />
                  )}
                  <span className="font-semibold text-zinc-700">{activeAgent?.name}</span>
                  {activeAgent?.model && (
                    <span className="font-mono text-[10px] text-zinc-400">({activeAgent.model})</span>
                  )}
                  {!isVllmModelReady && isVllmModelLoading && vllmStatus && (
                    <span className="font-mono text-[10px] text-amber-600">
                      — {vllmStatus.message || 'Model is loading...'}
                    </span>
                  )}
                </div>

                {!isVllmModelReady && !isVllmModelLoading && isVllmAgent && selectedAgentVllmModelId ? (
                  <button
                    type="button"
                    onClick={() => handleStartModel(selectedAgentVllmModelId)}
                    className="inline-flex items-center space-x-1.5 px-4 py-1.5 rounded-lg text-xs font-bold bg-emerald-600 hover:bg-emerald-700 text-white transition-colors shadow-xs"
                  >
                    <Play className="w-3.5 h-3.5" />
                    <span>Start Model</span>
                  </button>
                ) : (
                  <button
                    type="submit"
                    disabled={!draftInput.trim() || !isVllmModelReady}
                    className="inline-flex items-center space-x-1 px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 disabled:hover:bg-indigo-600 text-white transition-colors shadow-xs"
                  >
                    <span>{isVllmModelLoading ? 'Model Loading...' : 'Send'}</span>
                    {isVllmModelLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
                  </button>
                )}
              </div>
            </form>
          </div>
        </div>
      ) : (
        <div className="flex-1 flex flex-col overflow-hidden">
          <ChatPane
            conversationId={conversationId}
            showHeader={false}
            agentId={conversation?.agent_id || 'general'}
            agentName={activeAgent?.name || 'Kayak Agent'}
            agentModel={activeAgent?.model}
            placeholder="Ask anything, execute code, run tasks, type LaTeX formulas... (Enter to send)"
            onRefreshConversations={onRefreshConversations}
          />
        </div>
      )}
    </div>
  );
};
