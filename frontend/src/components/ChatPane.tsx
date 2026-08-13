import React, { useState, useEffect, useRef } from 'react';
import { Message } from '../types';
import { api } from '../api/client';
import { useSSE, ToolApprovalRequest } from '../hooks/useSSE';
import { useVLLMStatus, VLLM_LOADING_STATES } from '../context/VLLMStatusContext';
import { ToolCallCard } from './ToolCallCard';
import { ToolCallsAccordion } from './ToolCallsAccordion';
import { ThinkingAccordion } from './ThinkingAccordion';
import { MarkdownContent } from './Markdown';
import { 
  Send, 
  Bot, 
  Loader2, 
  Sparkles,
  Square,
  Play,
  CheckCircle2,
  AlertCircle,
  ShieldQuestion,
  Check,
  X
} from 'lucide-react';
import { useDialog } from '../context/DialogContext';

export interface ChatPaneProps {
  conversationId: string | null;
  agentId?: string;
  agentName?: string;
  agentModel?: string;
  placeholder?: string;
  showHeader?: boolean;
  headerTitle?: string;
  headerBadge?: string;
  headerSubtitle?: string;
  fullWidthInput?: boolean;
  onSendMessage?: (content: string) => Promise<string | void>;
  onConversationCreated?: (newId: string) => void;
  onRefreshConversations?: () => void;
}

interface GroupedTurn {
  id: string;
  role: 'user' | 'assistant';
  content?: string;
  thinking?: string;
  toolCalls: {
    id: string;
    name: string;
    argumentsStr: string;
    output?: string;
    isError?: boolean;
  }[];
}

export function groupMessagesIntoTurns(messages: Message[]): GroupedTurn[] {
  const turns: GroupedTurn[] = [];
  let currentAssistantTurn: GroupedTurn | null = null;
  const toolOutputsMap: Record<string, { output: string; name?: string; isError?: boolean }> = {};

  // First pass: collect tool outputs by tool_call_id
  for (const msg of messages) {
    if (msg.role === 'tool' && msg.tool_call_id) {
      toolOutputsMap[msg.tool_call_id] = {
        output: msg.content || '',
        name: msg.name || undefined,
        isError: msg.content?.startsWith('Error:') || msg.content?.startsWith('✗'),
      };
    }
  }

  for (let index = 0; index < messages.length; index++) {
    const msg = messages[index];
    if (msg.role === 'user') {
      if (currentAssistantTurn) {
        turns.push(currentAssistantTurn);
        currentAssistantTurn = null;
      }
      turns.push({
        id: msg.id || `user_${index}`,
        role: 'user',
        content: msg.content || '',
        toolCalls: [],
      });
    } else if (msg.role === 'assistant') {
      if (!currentAssistantTurn) {
        currentAssistantTurn = {
          id: msg.id || `assistant_${index}`,
          role: 'assistant',
          content: msg.content || '',
          thinking: msg.thinking || undefined,
          toolCalls: [],
        };
      } else {
        if (msg.content) {
          currentAssistantTurn.content = currentAssistantTurn.content 
            ? `${currentAssistantTurn.content}\n\n${msg.content}`
            : msg.content;
        }
        if (msg.thinking) {
          currentAssistantTurn.thinking = currentAssistantTurn.thinking
            ? `${currentAssistantTurn.thinking}\n\n${msg.thinking}`
            : msg.thinking;
        }
      }

      if (msg.tool_calls) {
        for (const tc of msg.tool_calls) {
          const matched = toolOutputsMap[tc.id];
          currentAssistantTurn.toolCalls.push({
            id: tc.id,
            name: tc.function.name,
            argumentsStr: tc.function.arguments,
            output: matched?.output,
            isError: matched?.isError,
          });
        }
      }
    }
  }

  if (currentAssistantTurn) {
    turns.push(currentAssistantTurn);
  }

  return turns;
}

export const ChatPane: React.FC<ChatPaneProps> = ({
  conversationId,
  agentId = 'general',
  agentName = 'Kayak Agent',
  agentModel,
  placeholder,
  showHeader = true,
  headerTitle,
  headerBadge,
  headerSubtitle,
  fullWidthInput = false,
  onSendMessage,
  onConversationCreated,
  onRefreshConversations,
}) => {
  const dialog = useDialog();
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputContent, setInputContent] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [streamingTokenText, setStreamingTokenText] = useState('');
  const [streamingThinkingText, setStreamingThinkingText] = useState('');
  const [activeToolExecutions, setActiveToolExecutions] = useState<
    Record<string, { name: string; args: string; output?: string; isError?: boolean }>
  >({});

  const [pendingApprovals, setPendingApprovals] = useState<ToolApprovalRequest[]>([]);
  const [streamWarning, setStreamWarning] = useState<string | null>(null);

  const isVllmAgent = Boolean(agentModel?.startsWith('vllm/'));
  const vllmModelId = isVllmAgent && agentModel ? agentModel.slice('vllm/'.length) : null;
  const { status: vllmStatus, refresh: refreshVllmStatus } = useVLLMStatus();
  const [isStartingVllm, setIsStartingVllm] = useState(false);

  const loadingStates = VLLM_LOADING_STATES;
  const isVllmModelReady = !isVllmAgent || (vllmStatus?.state === 'ready' && vllmStatus?.model_id === vllmModelId);
  const isVllmModelLoading = isVllmAgent && (isStartingVllm || (vllmStatus !== null && loadingStates.includes(vllmStatus.state) && (vllmStatus.model_id === vllmModelId || !vllmStatus.model_id)));
  const isVllmModelOffline = isVllmAgent && !isVllmModelReady && !isVllmModelLoading;

  const handleStartVllmModel = async () => {
    if (!vllmModelId) return;
    setIsStartingVllm(true);
    try {
      await api.deployVLLMModel({ model_id: vllmModelId });
      await refreshVllmStatus();
    } catch (err) {
      console.error('Failed to start vLLM model:', err);
      dialog.alert({
        title: 'Model Startup Failed',
        message: `Could not launch ${vllmModelId}: ${err}`,
        variant: 'danger',
      });
    } finally {
      setIsStartingVllm(false);
    }
  };

  const messagesContainerRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = (smooth = true) => {
    if (messagesContainerRef.current) {
      messagesContainerRef.current.scrollTo({
        top: messagesContainerRef.current.scrollHeight,
        behavior: smooth ? 'smooth' : 'auto',
      });
    }
  };

  const loadConversationData = async () => {
    if (!conversationId) {
      setMessages([]);
      setIsSending(false);
      return;
    }
    try {
      const data = await api.getConversation(conversationId);
      // The database is authoritative here: a reload only happens after the server
      // has persisted the turn, so any optimistic placeholder is already represented.
      // Matching placeholders by content instead would drop a genuine repeat message.
      setMessages(data.messages || []);

      if (data.conversation?.status === 'running') {
        setIsSending(true);
      }

    } catch (err) {
      console.error('Failed to load conversation messages:', err);
    }
  };

  useEffect(() => {
    loadConversationData();
    setStreamingTokenText('');
    setStreamingThinkingText('');
    setActiveToolExecutions({});
    setPendingApprovals([]);
    setStreamWarning(null);
  }, [conversationId]);

  useEffect(() => {
    scrollToBottom();
  }, [messages, streamingTokenText, streamingThinkingText, activeToolExecutions]);

  useSSE(conversationId, {
    onThinking: (chunk) => {
      setIsSending(true);
      setStreamingThinkingText((prev) => prev + chunk);
    },
    onToken: (token) => {
      setIsSending(true);
      setStreamingTokenText((prev) => prev + token);
    },
    onToolCallDelta: (delta) => {
      setIsSending(true);
      setActiveToolExecutions((prev) => {
        const existing = prev[delta.id] || { name: delta.name || '', args: '' };
        const combinedArgs = existing.args + (delta.arguments || '');
        const updatedName = delta.name || existing.name;

        return {
          ...prev,
          [delta.id]: {
            ...existing,
            name: updatedName,
            args: combinedArgs,
          },
        };
      });
    },
    onToolCallExecuting: (data) => {
      setIsSending(true);
      setActiveToolExecutions((prev) => ({
        ...prev,
        [data.id]: {
          name: data.name,
          args: data.arguments,
        },
      }));
    },
    onToolApprovalRequired: (request) => {
      setIsSending(true);
      setPendingApprovals((prev) =>
        prev.some((item) => item.id === request.id) ? prev : [...prev, request]
      );
    },
    onWarning: (warning) => {
      setStreamWarning(warning);
    },
    onMaxIterations: () => {
      setStreamWarning(
        'The agent hit the maximum number of tool-use steps for one turn and stopped early.'
      );
    },
    onTitleUpdated: () => {
      onRefreshConversations?.();
    },
    onToolCallResult: (data) => {
      setPendingApprovals((prev) => prev.filter((item) => item.id !== data.id));
      setActiveToolExecutions((prev) => {
        const existing = prev[data.id];
        return {
          ...prev,
          [data.id]: {
            ...existing,
            name: data.name,
            output: data.output,
            isError: data.is_error,
          },
        };
      });
    },
    onDone: () => {
      setIsSending(false);
      setStreamingTokenText('');
      setStreamingThinkingText('');
      setActiveToolExecutions({});
      setPendingApprovals([]);
      loadConversationData();
      onRefreshConversations?.();
    },
    onCancelled: () => {
      setIsSending(false);
      setStreamingTokenText('');
      setStreamingThinkingText('');
      setActiveToolExecutions({});
      setPendingApprovals([]);
      loadConversationData();
      onRefreshConversations?.();
    },
    onUserMessage: (msg) => {
      setMessages((prev) => {
        if (prev.some((m) => m.id === msg.id)) return prev;
        // Placeholders are confirmed in the order they were sent, so the oldest
        // outstanding one belongs to this echo regardless of its text.
        const optIndex = prev.findIndex((m) => m.id?.startsWith('optimistic_'));
        if (optIndex !== -1) {
          const next = [...prev];
          next[optIndex] = msg;
          return next;
        }
        return [...prev, msg];
      });
    },
    onError: (err) => {
      setIsSending(false);
      dialog.alert({
        title: 'Execution Error',
        message: `Agent encountered an error: ${err}`,
        variant: 'danger',
      });
    },
  });

  const handleSend = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!inputContent.trim() || isSending || (isVllmAgent && !isVllmModelReady)) return;

    const text = inputContent.trim();
    setInputContent('');
    setIsSending(true);

    // Optimistically insert user message immediately for instant UI feedback
    const tempUserMsg: Message = {
      id: `optimistic_${Date.now()}`,
      conversation_id: conversationId || '',
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, tempUserMsg]);

    try {
      if (!conversationId) {
        if (onSendMessage) {
          const newCid = await onSendMessage(text);
          if (newCid) {
            onConversationCreated?.(newCid);
            onRefreshConversations?.();
          }
        } else {
          const conv = await api.createConversation({
            agent_id: agentId,
            initial_message: text,
          });
          onConversationCreated?.(conv.id);
          onRefreshConversations?.();
        }
      } else {
        await api.sendMessage(conversationId, text);
        onRefreshConversations?.();
      }
    } catch (err) {
      setIsSending(false);
      console.error('Failed to send message:', err);
    }
  };

  const handleCancelGeneration = async () => {
    if (!conversationId) return;
    try {
      await api.cancelConversation(conversationId);
      setIsSending(false);
      setStreamingTokenText('');
      setStreamingThinkingText('');
      setActiveToolExecutions({});
      loadConversationData();
      onRefreshConversations?.();
    } catch (err) {
      console.error('Failed to cancel conversation:', err);
    }
  };

  const handleResolveApproval = async (callId: string, approved: boolean) => {
    if (!conversationId) return;
    setPendingApprovals((prev) => prev.filter((item) => item.id !== callId));
    try {
      await api.resolveToolApproval(conversationId, callId, approved);
    } catch (err) {
      console.error('Failed to submit tool approval:', err);
      dialog.alert({
        title: 'Approval Failed',
        message: `Could not record your decision: ${err}`,
        variant: 'danger',
      });
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const groupedTurns = groupMessagesIntoTurns(messages);

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 bg-md-surface overflow-hidden transition-colors">
      {/* Optional Sub-Header */}
      {showHeader && (
        <div className="p-3 border-b border-md-outline-variant bg-md-surface-container-low flex items-center justify-between text-xs font-bold uppercase tracking-wider text-md-on-surface shrink-0 transition-colors">
          <div className="flex items-center space-x-2">
            <Bot className="w-4 h-4 text-md-primary" />
            <span>{headerTitle || agentName}</span>
          </div>
          {headerBadge && (
            <span className="text-[10px] font-mono text-md-on-surface-variant font-normal">{headerBadge}</span>
          )}
        </div>
      )}

      {/* Messages Scroll Area - Constrained to max-w-3xl for optimal line length and reading ergonomics */}
      <div ref={messagesContainerRef} className="flex-1 min-h-0 overflow-y-auto px-4 sm:px-6 py-6">
        <div className="max-w-3xl mx-auto w-full space-y-6">
          {groupedTurns.length === 0 && !streamingTokenText && !streamingThinkingText && Object.keys(activeToolExecutions).length === 0 && (
            <div className="text-center py-20 space-y-3">
              <div className="w-12 h-12 rounded-2xl bg-md-primary-container text-md-on-primary-container border border-md-outline-variant flex items-center justify-center mx-auto shadow-xs">
                <Sparkles className="w-6 h-6" />
              </div>
              <h3 className="text-sm font-bold text-md-on-surface">{headerTitle || `Chat with ${agentName}`}</h3>
              <p className="text-xs text-md-on-surface-variant max-w-sm mx-auto leading-relaxed">
                {headerSubtitle || 'Type your instructions below to begin interactive agent synthesis and refinement.'}
              </p>
            </div>
          )}

          {groupedTurns.map((turn) => {
            if (turn.role === 'user') {
              return (
                <div key={turn.id} className="flex justify-end pt-2">
                  <div className="max-w-2xl bg-md-primary text-md-on-primary rounded-2xl rounded-tr-xs px-5 py-3 text-[15px] shadow-xs leading-relaxed">
                    <MarkdownContent variant="on-primary">
                      {turn.content || ''}
                    </MarkdownContent>
                  </div>
                </div>
              );
            }

            if (turn.role === 'assistant') {
              return (
                <div key={turn.id} className="w-full space-y-2 pt-2">
                  {/* Collapsible Accordion for Thinking / Reasoning Tokens */}
                  {turn.thinking && (
                    <ThinkingAccordion content={turn.thinking} />
                  )}

                  {/* Direct Response Text on Background without enclosing white card or icons */}
                  {turn.content && (
                    <MarkdownContent size="comfortable" className="text-md-on-surface leading-relaxed">
                      {turn.content}
                    </MarkdownContent>
                  )}

                  {/* Collapsible Dropdown Button for Tool Calls */}
                  {turn.toolCalls && turn.toolCalls.length > 0 && (
                    <ToolCallsAccordion
                      toolCalls={turn.toolCalls.map((tc) => ({
                        id: tc.id,
                        name: tc.name,
                        argumentsStr: tc.argumentsStr,
                        output: tc.output,
                        isError: tc.isError,
                      }))}
                    />
                  )}
                </div>
              );
            }

            return null;
          })}

          {/* Live Streaming Active Turn */}
          {(isSending || streamingTokenText || streamingThinkingText || Object.keys(activeToolExecutions).length > 0) && (
            <div className="w-full space-y-2 pt-2 animate-fade-in">
              {/* Agent Preparing / Thinking Loader before first token arrives */}
              {isSending && !streamingTokenText && !streamingThinkingText && Object.keys(activeToolExecutions).length === 0 && (
                <div className="flex items-center space-x-2.5 py-3 text-md-on-surface-variant animate-pulse">
                  <div className="w-7 h-7 rounded-xl bg-md-primary-container border border-md-outline-variant flex items-center justify-center text-md-on-primary-container shrink-0">
                    <Loader2 className="w-4 h-4 animate-spin" />
                  </div>
                  <span className="text-xs font-medium text-md-on-surface-variant">Reaching model...</span>
                </div>
              )}

              {/* Live Streaming Thinking Process */}
              {streamingThinkingText && (
                <ThinkingAccordion
                  content={streamingThinkingText}
                  isThinking={!streamingTokenText}
                  defaultExpanded={true}
                />
              )}

              {/* Streaming token text directly on background */}
              {streamingTokenText && (
                <MarkdownContent size="comfortable" className="text-md-on-surface leading-relaxed">
                  {streamingTokenText}
                </MarkdownContent>
              )}

              {/* Tool calls held for explicit user approval */}
              {pendingApprovals.map((approval) => (
                <div
                  key={approval.id}
                  className="rounded-xl border border-amber-300 dark:border-amber-800/80 bg-amber-50 dark:bg-amber-950/40 p-3.5 space-y-2.5"
                >
                  <div className="flex items-center gap-2 text-xs font-bold text-amber-900 dark:text-amber-100">
                    <ShieldQuestion className="w-4 h-4 shrink-0" />
                    <span>Approval required to run</span>
                    <code className="font-mono px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-900/60 border border-amber-300 dark:border-amber-800">
                      {approval.name}
                    </code>
                  </div>
                  {approval.arguments && (
                    <pre className="text-[11px] font-mono text-amber-900 dark:text-amber-100/90 bg-amber-100/70 dark:bg-amber-900/40 rounded-lg p-2.5 overflow-x-auto max-h-40">
                      {approval.arguments}
                    </pre>
                  )}
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => handleResolveApproval(approval.id, true)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold bg-md-primary text-md-on-primary hover:opacity-90 transition-opacity cursor-pointer"
                    >
                      <Check className="w-3.5 h-3.5 stroke-[3]" /> Approve
                    </button>
                    <button
                      type="button"
                      onClick={() => handleResolveApproval(approval.id, false)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold bg-md-surface-container-high text-md-on-surface border border-md-outline-variant hover:opacity-90 transition-opacity cursor-pointer"
                    >
                      <X className="w-3.5 h-3.5 stroke-[3]" /> Deny
                    </button>
                  </div>
                </div>
              ))}

              {/* Active Executing Tool Calls Live */}
              {Object.keys(activeToolExecutions).length > 0 && (
                <div className="space-y-2 pt-1">
                  {Object.entries(activeToolExecutions).map(([id, t]) => (
                    <ToolCallCard
                      key={id}
                      name={t.name}
                      argumentsStr={t.args}
                      output={t.output}
                      isExecuting={!t.output}
                      isError={t.isError}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Degraded-run notice: a tool-less retry or an iteration ceiling changes what
          the agent was actually able to do, so it is surfaced rather than logged. */}
      {streamWarning && (
        <div className="px-4 pt-3 shrink-0">
          <div className={`${fullWidthInput ? 'w-full' : 'max-w-3xl mx-auto'} flex items-start gap-2 rounded-xl border border-amber-300 dark:border-amber-800/80 bg-amber-50 dark:bg-amber-950/40 px-3.5 py-2.5`}>
            <AlertCircle className="w-4 h-4 text-amber-700 dark:text-amber-300 shrink-0 mt-0.5" />
            <p className="text-xs text-amber-900 dark:text-amber-100 leading-relaxed flex-1">{streamWarning}</p>
            <button
              type="button"
              onClick={() => setStreamWarning(null)}
              className="text-amber-700 dark:text-amber-300 hover:opacity-70 transition-opacity cursor-pointer"
              title="Dismiss"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}

      {/* Input Composer - Constrained to max-w-3xl for standard chat, full width in studio mode */}
      <div className="p-4 border-t border-md-outline-variant bg-md-surface shrink-0 transition-colors">
        <form
          onSubmit={handleSend}
          className={`${fullWidthInput ? 'w-full' : 'max-w-3xl mx-auto'} relative bg-md-surface-container-lowest border border-md-outline-variant rounded-2xl overflow-hidden focus-within:border-md-primary focus-within:ring-2 focus-within:ring-md-primary/20 shadow-xs transition-all`}
        >
          <textarea
            value={inputContent}
            onChange={(e) => setInputContent(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={2}
            disabled={isVllmModelOffline || isVllmModelLoading}
            placeholder={
              isVllmModelOffline
                ? `Local model ${vllmModelId} is offline. Click 'Start Model' to begin...`
                : isVllmModelLoading
                ? `Local model ${vllmModelId} is initializing...`
                : placeholder || `Message ${agentName}... (Enter to send, Shift+Enter for new line)`
            }
            className="w-full bg-transparent px-4 py-3 text-xs text-md-on-surface placeholder:text-md-on-surface-variant/70 focus:outline-none resize-none leading-relaxed disabled:opacity-60 disabled:cursor-not-allowed"
          />

          <div className="flex items-center justify-between px-3.5 py-2 bg-md-surface-container-low border-t border-md-outline-variant">
            <div className="flex items-center space-x-2 text-[11px] text-md-on-surface-variant">
              <Sparkles className="w-3.5 h-3.5 text-md-primary" />
              <span className="font-semibold text-md-on-surface">{agentName}</span>
              {agentModel && (
                <span className="font-mono text-[10px] text-md-on-surface-variant">
                  ({agentModel.split('/')[1] || agentModel})
                </span>
              )}
              {isVllmAgent && (
                isVllmModelReady ? (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold text-emerald-800 dark:text-emerald-200 bg-emerald-100 dark:bg-emerald-950/80 border border-emerald-300 dark:border-emerald-800/80 px-2 py-0.5 rounded-full">
                    <CheckCircle2 className="w-2.5 h-2.5 stroke-[2.5]" /> Serving
                  </span>
                ) : isVllmModelLoading ? (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold text-amber-800 dark:text-amber-200 bg-amber-100 dark:bg-amber-950/80 border border-amber-300 dark:border-amber-800/80 px-2 py-0.5 rounded-full">
                    <Loader2 className="w-2.5 h-2.5 animate-spin" /> Loading
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold text-md-on-surface-variant bg-md-surface-container-highest border border-md-outline-variant px-2 py-0.5 rounded-full">
                    Offline
                  </span>
                )
              )}
            </div>

            <div className="flex items-center space-x-2">
              {isSending ? (
                <button
                  type="button"
                  onClick={handleCancelGeneration}
                  className="inline-flex items-center space-x-1 px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-md-error text-md-on-error hover:opacity-90 transition-opacity shadow-xs cursor-pointer"
                >
                  <Square className="w-3 h-3 fill-current" />
                  <span>Stop</span>
                </button>
              ) : isVllmModelOffline ? (
                <button
                  type="button"
                  onClick={handleStartVllmModel}
                  disabled={isStartingVllm}
                  className="inline-flex items-center space-x-1.5 px-3.5 py-1.5 rounded-lg text-xs font-bold bg-md-primary text-md-on-primary hover:opacity-90 disabled:opacity-50 transition-opacity shadow-xs cursor-pointer"
                >
                  {isStartingVllm ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5 fill-current" />}
                  <span>Start Model</span>
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!inputContent.trim() || isVllmModelLoading}
                  className="inline-flex items-center space-x-1 px-4 py-1.5 rounded-lg text-xs font-semibold bg-md-primary text-md-on-primary hover:opacity-90 disabled:opacity-40 disabled:hover:opacity-40 transition-opacity shadow-xs cursor-pointer"
                >
                  <span>{isVllmModelLoading ? 'Model Loading...' : 'Send'}</span>
                  {isVllmModelLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
                </button>
              )}
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
