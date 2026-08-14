import React, { useState, useEffect, useCallback } from 'react';
import { Conversation, AgentConfig } from '../types';
import { api } from '../api/client';
import { ChatPane } from './ChatPane';
import { AutoGrowTextarea } from './AutoGrowTextarea';
import { useVLLMStatus, VLLM_LOADING_STATES } from '../context/VLLMStatusContext';
import { parseVllmModelId, useVllmModel } from '../hooks/useVllmModel';
import {
  Bot,
  Cpu,
  Check,
  Sparkles,
  Send,
  Loader2,
  CircleDot,
  AlertCircle,
  GitBranch,
  Play
} from 'lucide-react';

interface ChatViewProps {
  conversationId: string | null;
  agents: AgentConfig[];
  /** Agent to preselect in the composer when starting a new conversation. */
  initialDraftAgentId?: string | null;
  onCreateConversation: (data: { title?: string; agent_id: string; isolated_container: boolean; initial_message?: string }) => void;
  onRefreshConversations?: () => void;
  /** Opens an existing conversation, e.g. a branch or the conversation it came from. */
  onOpenConversation?: (conversation: Conversation) => void;
  onSelectConversation?: (id: string) => void;
  /** Reports whether this conversation currently has a turn in flight. */
  onActivityChange?: (isActive: boolean) => void;
}

export const ChatView: React.FC<ChatViewProps> = ({
  conversationId,
  agents,
  initialDraftAgentId,
  onCreateConversation,
  onRefreshConversations,
  onOpenConversation,
  onSelectConversation,
  onActivityChange,
}) => {
  const [conversation, setConversation] = useState<Conversation | null>(null);

  // Draft mode state (when conversationId is null)
  const [draftAgentId, setDraftAgentId] = useState<string>(
    initialDraftAgentId || agents[0]?.id || 'general'
  );
  const [draftUseContainer, setDraftUseContainer] = useState<boolean>(false);
  const [draftInput, setDraftInput] = useState<string>('');
  const [isCreating, setIsCreating] = useState<boolean>(false);
  const { status: vllmStatus } = useVLLMStatus();

  const loadConversationData = useCallback(async () => {
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
  }, [conversationId]);

  useEffect(() => {
    loadConversationData();
  }, [loadConversationData]);

  useEffect(() => {
    if (agents.length > 0 && !agents.some((agent) => agent.id === draftAgentId)) {
      setDraftAgentId(agents[0].id);
    }
  }, [agents]);

  // Arriving from a "chat with this agent" link preselects that agent.
  useEffect(() => {
    if (initialDraftAgentId) setDraftAgentId(initialDraftAgentId);
  }, [initialDraftAgentId]);

  const activeAgent = conversationId
    ? agents.find((agent) => agent.id === conversation?.agent_id)
    : agents.find((agent) => agent.id === draftAgentId);

  // One shared derivation of local-model readiness, so the draft screen and the
  // composer inside ChatPane cannot disagree about the same agent.
  const vllm = useVllmModel(activeAgent?.model);

  // Helper: get vLLM badge info for a given agent
  const getVllmBadge = useCallback(
    (agent: AgentConfig): { color: 'green' | 'yellow' | 'red'; label: string; spinning: boolean; canStart: boolean } | null => {
      const modelId = parseVllmModelId(agent.model);
      if (!modelId) return null; // cloud model — no badge
      if (!vllmStatus) return { color: 'red', label: 'Not Running', spinning: false, canStart: true };

      const isThisModel = vllmStatus.model_id === modelId;

      if (vllmStatus.state === 'ready' && isThisModel) {
        return { color: 'green', label: 'Ready', spinning: false, canStart: false };
      }
      if (VLLM_LOADING_STATES.includes(vllmStatus.state) && isThisModel) {
        return { color: 'yellow', label: 'Loading...', spinning: true, canStart: false };
      }
      return { color: 'red', label: 'Not Running', spinning: false, canStart: true };
    },
    [vllmStatus]
  );

  const startModelFor = useCallback(async (model: string) => {
    const modelId = parseVllmModelId(model);
    if (!modelId) return;
    try {
      await api.deployVLLMModel({ model_id: modelId });
    } catch (err) {
      console.error('Failed to deploy vLLM model:', err);
    }
  }, []);


  const handleDraftSend = async (event?: React.FormEvent) => {
    if (event) event.preventDefault();
    const text = draftInput.trim();
    if (!text || !vllm.isReady || isCreating) return;

    setIsCreating(true);
    try {
      await onCreateConversation({
        agent_id: draftAgentId,
        isolated_container: draftUseContainer,
        initial_message: text,
      });
      setDraftInput('');
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 bg-md-surface overflow-hidden transition-colors">
      {/* Top Header Bar */}
      <div className="h-16 border-b border-md-outline-variant px-8 flex items-center justify-between bg-md-surface-container-low shrink-0 transition-colors">
        <div className="flex items-center space-x-3 truncate">
          <h2 className="font-bold text-sm text-md-on-surface truncate max-w-md">
            {conversationId ? (conversation?.title || 'Conversation') : 'New Conversation'}
          </h2>

          {conversationId ? (
            conversation?.isolated_container ? (
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-100 dark:bg-blue-950/80 text-blue-800 dark:text-blue-200 border border-blue-300 dark:border-blue-800/80 shrink-0">
                <span>🐳</span> Docker Sandbox
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-md-surface-container-high text-md-on-surface border border-md-outline-variant shrink-0">
                <Cpu className="w-3.5 h-3.5 text-md-on-surface-variant" /> Host Workspace
              </span>
            )
          ) : null}

          {activeAgent && (
            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-md-surface-container-high text-md-on-surface border border-md-outline-variant font-mono shrink-0">
              <Bot className="w-3.5 h-3.5 text-md-primary" /> {activeAgent.name} ({activeAgent.model})
            </span>
          )}

          {/* Provenance for a branch. The source is an ordinary conversation that
              deleting this one leaves untouched, and vice versa. */}
          {conversation?.branched_from_conversation_id && (
            <button
              type="button"
              onClick={() =>
                onSelectConversation?.(conversation.branched_from_conversation_id as string)
              }
              className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-md-tertiary-container text-md-on-tertiary-container border border-md-outline-variant shrink-0 hover:opacity-90 transition-opacity cursor-pointer"
              title="Open the conversation this was branched from"
            >
              <GitBranch className="w-3.5 h-3.5" /> Branch
            </button>
          )}
        </div>
      </div>

      {/* Main Area: Draft Setup Widget vs Active Chat Pane */}
      {!conversationId ? (
        <div className="flex-1 flex flex-col justify-between overflow-hidden bg-md-surface">
          {/* Centered Agent Profile Selection Widget */}
          <div className="flex-1 flex flex-col justify-center items-center px-6 py-4 overflow-hidden">
            <div className="w-full max-w-2xl flex flex-col space-y-4 max-h-full">
              {/* Header Title */}
              <div className="text-center space-y-1.5 shrink-0">
                <div className="w-12 h-12 rounded-2xl bg-md-surface-container border border-md-outline-variant flex items-center justify-center text-2xl mx-auto shadow-xs">
                  🛶
                </div>
                <h3 className="text-base font-bold text-md-on-surface tracking-tight">
                  How can Kayak help you today?
                </h3>
                <p className="text-xs text-md-on-surface-variant leading-relaxed">
                  Select an agent profile and execution environment below. Type your initial prompt to begin.
                </p>
              </div>

              {/* Agent Profile Selection Widget Container */}
              <div className="bg-md-surface border border-md-outline-variant rounded-2xl p-5 shadow-xs flex flex-col space-y-3 overflow-hidden transition-colors">
                <label className="block text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5 shrink-0">
                  <Bot className="w-4 h-4 text-md-primary" /> Choose Agent Profile
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
                              ? 'bg-md-primary-container border-2 border-md-primary text-md-on-primary-container shadow-xs ring-1 ring-md-primary/40 font-medium'
                              : 'bg-md-surface border-md-outline-variant text-md-on-surface hover:border-md-outline hover:bg-md-surface-container shadow-xs'
                          }`}
                        >
                          <div>
                            <div className="flex items-center justify-between mb-1">
                              <span className="font-semibold text-xs text-md-on-surface">{agent.name}</span>
                              <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${
                                isSelected 
                                  ? 'bg-md-primary text-md-on-primary border-md-primary' 
                                  : 'bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant'
                              }`}>
                                {agent.model.split('/')[1] || agent.model}
                              </span>
                            </div>
                            <p className="text-[11px] text-md-on-surface-variant line-clamp-2 leading-relaxed">
                              {agent.description}
                            </p>
                          </div>

                          <div className="mt-2.5 pt-2 border-t border-md-outline-variant flex items-center justify-between text-[10px]">
                            <span className="text-md-on-surface-variant font-mono">
                              {agent.allowed_tools?.length || 0} tools · {agent.preloaded_skills?.length || 0} skills
                            </span>
                            <div className="flex items-center gap-2">
                              {(() => {
                                const badge = getVllmBadge(agent);
                                if (!badge) return null;
                                const colorClasses = {
                                  green: 'text-emerald-800 dark:text-emerald-200 bg-emerald-100 dark:bg-emerald-950/80 border-emerald-300 dark:border-emerald-800/80',
                                  yellow: 'text-amber-800 dark:text-amber-200 bg-amber-100 dark:bg-amber-950/80 border-amber-300 dark:border-amber-800/80',
                                  red: 'text-md-on-surface-variant bg-md-surface-container-high border-md-outline-variant',
                                };
                                if (badge.canStart) {
                                  return (
                                    <button
                                      type="button"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        startModelFor(agent.model);
                                      }}
                                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold border border-md-outline-variant bg-md-primary-container text-md-on-primary-container hover:opacity-90 transition-opacity"
                                    >
                                      <Play className="w-3 h-3 fill-current" />
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
                                <span className="inline-flex items-center gap-1 text-md-primary font-bold">
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
                <div className="pt-3 border-t border-md-outline-variant flex items-center justify-between shrink-0">
                  <div>
                    <div className="text-xs font-semibold text-md-on-surface flex items-center gap-1.5">
                      <span>🐳 Docker Sandbox Isolation</span>
                    </div>
                    <div className="text-[11px] text-md-on-surface-variant">
                      Run shell commands and code in an isolated container instead of host
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setDraftUseContainer(!draftUseContainer)}
                    className={`w-11 h-6 flex items-center rounded-full p-1 cursor-pointer transition-colors duration-200 ease-in-out ${
                      draftUseContainer ? 'bg-md-primary' : 'bg-md-surface-container-high'
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
          <div className="p-4 bg-md-surface-container-low border-t border-md-outline-variant shrink-0 transition-colors">
            <form
              onSubmit={handleDraftSend}
              className="max-w-4xl mx-auto relative bg-md-surface-container-lowest border border-md-outline-variant rounded-2xl overflow-hidden focus-within:border-md-primary focus-within:ring-2 focus-within:ring-md-primary/20 shadow-xs transition-all"
            >
              <AutoGrowTextarea
                value={draftInput}
                onChange={setDraftInput}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    handleDraftSend();
                  }
                }}
                disabled={!vllm.isReady}
                placeholder={
                  !vllm.isReady
                    ? 'Waiting for the local model to be ready...'
                    : `Message ${activeAgent?.name || 'Kayak Agent'}... (Enter to send, Shift+Enter for new line)`
                }
                className={!vllm.isReady ? 'opacity-60' : ''}
              />

              <div className="flex items-center justify-between px-3.5 py-2 bg-md-surface-container-high border-t border-md-outline-variant">
                <div className="flex items-center space-x-2 text-[11px] text-md-on-surface-variant">
                  {!vllm.isReady ? (
                    vllm.isLoading ? (
                      <Loader2 className="w-3.5 h-3.5 text-amber-500 animate-spin" />
                    ) : (
                      <AlertCircle className="w-3.5 h-3.5 text-md-on-surface-variant" />
                    )
                  ) : (
                    <Sparkles className="w-3.5 h-3.5 text-md-primary" />
                  )}
                  <span className="font-semibold text-md-on-surface">{activeAgent?.name}</span>
                  {activeAgent?.model && (
                    <span className="font-mono text-[10px] text-md-on-surface-variant">({activeAgent.model})</span>
                  )}
                  {vllm.isLoading && vllm.statusMessage && (
                    <span className="font-mono text-[10px] text-amber-800 dark:text-amber-200">
                      — {vllm.statusMessage}
                    </span>
                  )}
                </div>

                {vllm.isOffline ? (
                  <button
                    type="button"
                    onClick={() => vllm.start()}
                    className="inline-flex items-center space-x-1.5 px-4 py-1.5 rounded-lg text-xs font-bold bg-md-primary text-md-on-primary hover:opacity-90 transition-opacity shadow-xs cursor-pointer"
                  >
                    <Play className="w-3.5 h-3.5 fill-current" />
                    <span>Start Model</span>
                  </button>
                ) : (
                  <button
                    type="submit"
                    disabled={!draftInput.trim() || !vllm.isReady || isCreating}
                    className="inline-flex items-center space-x-1 px-4 py-1.5 rounded-lg text-xs font-semibold bg-md-primary text-md-on-primary hover:opacity-90 disabled:opacity-40 disabled:hover:opacity-40 transition-opacity shadow-xs cursor-pointer"
                  >
                    <span>{isCreating ? 'Starting...' : vllm.isLoading ? 'Model Loading...' : 'Send'}</span>
                    {isCreating || vllm.isLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
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
            onConversationUpdated={loadConversationData}
            onOpenConversation={onOpenConversation}
            onActivityChange={onActivityChange}
          />
        </div>
      )}
    </div>
  );
};
