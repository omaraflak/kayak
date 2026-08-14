import React, { useState, useEffect, useLayoutEffect, useMemo, useRef, useCallback } from 'react';
import { Conversation, Message } from '../types';
import { api } from '../api/client';
import { useSSE, ToolApprovalRequest } from '../hooks/useSSE';
import { useVllmModel } from '../hooks/useVllmModel';
import { ToolCallCard } from './ToolCallCard';
import { ToolCallsAccordion } from './ToolCallsAccordion';
import { ThinkingAccordion } from './ThinkingAccordion';
import { MarkdownContent } from './Markdown';
import { AutoGrowTextarea } from './AutoGrowTextarea';
import { MessageAction, MessageActions } from './MessageActions';
import {
  GroupedTurn,
  countMessagesFrom,
  findPrecedingUserMessageId,
  groupMessagesIntoTurns,
  isPersistedTurn,
  lastAssistantTurnIndex,
} from './conversationTurns';
import { isNearBottom } from './chatScroll';
import { extractWrittenFiles, workspaceRelativePath } from './workspaceFiles';
import {
  ActiveToolCalls,
  applyToolDelta,
  applyToolExecuting,
  applyToolResult,
} from './toolCallStream';
import {
  Send,
  Bot,
  FileText,
  Loader2,
  Sparkles,
  Square,
  Play,
  CheckCircle2,
  AlertCircle,
  ArrowDown,
  ShieldQuestion,
  Check,
  X,
} from 'lucide-react';
import { useDialog } from '../context/DialogContext';

export type { GroupedTurn } from './conversationTurns';
export { groupMessagesIntoTurns } from './conversationTurns';

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
  /**
   * The conversation record itself changed (its title, say), as opposed to its
   * messages. The owner holds that record, so it has to re-read it.
   */
  onConversationUpdated?: () => void;
  /** Opens a conversation created from this one, e.g. by branching. */
  onOpenConversation?: (conversation: Conversation) => void;
  /** Opens a workspace file in the side-panel preview (from a chip in the chat). */
  onOpenFile?: (path: string) => void;
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
  onConversationUpdated,
  onOpenConversation,
  onOpenFile,
}) => {
  const dialog = useDialog();
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputContent, setInputContent] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [streamingTokenText, setStreamingTokenText] = useState('');
  const [streamingThinkingText, setStreamingThinkingText] = useState('');
  const [activeToolExecutions, setActiveToolExecutions] = useState<ActiveToolCalls>({});

  const [pendingApprovals, setPendingApprovals] = useState<ToolApprovalRequest[]>([]);
  const [streamWarning, setStreamWarning] = useState<string | null>(null);
  const [pendingTurnAction, setPendingTurnAction] = useState<
    { turnId: string; action: MessageAction } | null
  >(null);
  const [isFollowingOutput, setIsFollowingOutput] = useState(true);

  const vllm = useVllmModel(agentModel);

  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const messagesContentRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  // Mirrors isFollowingOutput for callbacks that are registered once and would
  // otherwise capture a stale value (the resize observer below).
  const isFollowingRef = useRef(true);
  isFollowingRef.current = isFollowingOutput;

  const scrollToBottom = (smooth = false) => {
    if (messagesContainerRef.current) {
      messagesContainerRef.current.scrollTo({
        top: messagesContainerRef.current.scrollHeight,
        behavior: smooth ? 'smooth' : 'auto',
      });
    }
  };

  const loadConversationData = useCallback(async () => {
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
  }, [conversationId]);

  useEffect(() => {
    loadConversationData();
    setStreamingTokenText('');
    setStreamingThinkingText('');
    setActiveToolExecutions({});
    setPendingApprovals([]);
    setStreamWarning(null);
    setPendingTurnAction(null);
    // Opening a conversation always starts at the newest message.
    setIsFollowingOutput(true);
  }, [conversationId, loadConversationData]);

  // Positioned before the browser paints, and without animation: opening a
  // conversation should simply *be* at the newest message, rather than showing the
  // top of the history and then scrolling down through it. The same instant pin
  // keeps the view at the end as output arrives; a smooth animation there only
  // chases the content it is trying to follow.
  useLayoutEffect(() => {
    if (isFollowingOutput) scrollToBottom();
  }, [messages, streamingTokenText, streamingThinkingText, activeToolExecutions, isFollowingOutput]);

  // Content that finishes loading after paint -- images, KaTeX, highlighted code --
  // grows the transcript underneath the viewport, which would silently leave the
  // reader above the end. Re-pin whenever the height changes while following.
  useEffect(() => {
    const content = messagesContentRef.current;
    if (!content) return;

    const observer = new ResizeObserver(() => {
      if (isFollowingRef.current) scrollToBottom();
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [conversationId]);

  const handleScroll = () => {
    const element = messagesContainerRef.current;
    if (!element) return;
    setIsFollowingOutput(isNearBottom(element));
  };

  const resetStreamState = useCallback(() => {
    setIsSending(false);
    setStreamingTokenText('');
    setStreamingThinkingText('');
    setActiveToolExecutions({});
    setPendingApprovals([]);
  }, []);

  useSSE(conversationId, {
    onConnected: ({ isRunning }) => {
      // Recovers the composer after a dropped stream: without this a tab that was
      // backgrounded mid-turn comes back showing a Send button for a turn that is
      // still running, or a Stop button for one that has finished.
      setIsSending(isRunning);
      if (!isRunning) {
        setStreamingTokenText('');
        setStreamingThinkingText('');
      }
    },
    onHistoryChanged: () => {
      loadConversationData();
    },
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
      setActiveToolExecutions((prev) => applyToolDelta(prev, delta));
    },
    onToolCallExecuting: (data) => {
      setIsSending(true);
      setActiveToolExecutions((prev) => applyToolExecuting(prev, data));
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
      onConversationUpdated?.();
    },
    onToolCallResult: (data) => {
      setPendingApprovals((prev) => prev.filter((item) => item.id !== data.id));
      setActiveToolExecutions((prev) => applyToolResult(prev, data));
    },
    onDone: () => {
      resetStreamState();
      loadConversationData();
      onRefreshConversations?.();
    },
    onCancelled: () => {
      resetStreamState();
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
      // Partial output from the failed turn is on screen but was never persisted.
      // Reloading replaces it with what the database actually holds, rather than
      // leaving text that silently disappears at the next refresh.
      resetStreamState();
      loadConversationData();
      dialog.alert({
        title: 'Execution Error',
        message: `Agent encountered an error: ${err}`,
        variant: 'danger',
      });
    },
  });

  const handleSend = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!inputContent.trim() || isSending || !vllm.isReady) return;

    const text = inputContent.trim();
    setInputContent('');
    setIsSending(true);
    setIsFollowingOutput(true);

    // Optimistically insert user message immediately for instant UI feedback
    const placeholderId = `optimistic_${Date.now()}`;
    const tempUserMsg: Message = {
      id: placeholderId,
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
      // The message never reached the server, so leaving the placeholder in place
      // would show it as sent until the next reload. Take it back and say why.
      setIsSending(false);
      setMessages((prev) => prev.filter((message) => message.id !== placeholderId));
      setInputContent(text);
      dialog.alert({
        title: 'Message not sent',
        message: `${err}. Your message has been put back in the composer.`,
        variant: 'danger',
      });
    }
  };

  const handleCancelGeneration = async () => {
    if (!conversationId) return;
    try {
      await api.cancelConversation(conversationId);
      resetStreamState();
      loadConversationData();
      onRefreshConversations?.();
    } catch (err) {
      dialog.alert({
        title: 'Could not stop the turn',
        message: String(err),
        variant: 'danger',
      });
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

  const handleTurnAction = async (turn: GroupedTurn, action: MessageAction) => {
    if (!conversationId || pendingTurnAction) return;

    // Revert and retry delete stored messages with no undo, so they state the cost.
    if (action === 'revert' || action === 'retry') {
      const anchorId =
        action === 'revert'
          ? findPrecedingUserMessageId(messages, turn.id) ?? turn.id
          : turn.id;
      const count = countMessagesFrom(messages, anchorId);
      const confirmed = await dialog.confirm({
        title: action === 'revert' ? 'Revert to this point?' : 'Retry this response?',
        message:
          action === 'revert'
            ? `This permanently removes ${count} message${count === 1 ? '' : 's'} from this conversation and puts the prompt back in the composer for editing.`
            : `This permanently removes ${count} message${count === 1 ? '' : 's'} and generates the response again from the same history.`,
        confirmText: action === 'revert' ? 'Revert' : 'Retry',
        cancelText: 'Cancel',
        variant: 'danger',
      });
      if (!confirmed) return;
    }

    setPendingTurnAction({ turnId: turn.id, action });
    try {
      if (action === 'revert') {
        const result = await api.revertToMessage(conversationId, turn.id);
        await loadConversationData();
        if (result.prompt) {
          setInputContent(result.prompt);
          composerRef.current?.focus();
        }
      } else if (action === 'retry') {
        await api.retryFromMessage(conversationId, turn.id);
        setIsSending(true);
        setIsFollowingOutput(true);
        await loadConversationData();
      } else {
        // Branching copies through the turn's *last* message, so the new history ends
        // on a complete turn rather than a tool call with no result.
        const branch = await api.branchFromMessage(conversationId, turn.lastMessageId);
        onOpenConversation?.(branch);
      }
      onRefreshConversations?.();
    } catch (err) {
      dialog.alert({
        title:
          action === 'revert'
            ? 'Could not revert'
            : action === 'retry'
            ? 'Could not retry'
            : 'Could not branch',
        message: String(err),
        variant: 'danger',
      });
    } finally {
      setPendingTurnAction(null);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Workspace-relative paths in the transcript (plot.png, /workspace/plot.png)
  // only render if they are resolved to the endpoint that actually serves them.
  const resolveFileUrl = useCallback(
    (url: string) => {
      if (!conversationId) return url;
      const relative = workspaceRelativePath(url);
      return relative ? api.workspaceFileUrl(conversationId, relative) : url;
    },
    [conversationId]
  );

  // Regrouping walks the whole history; without memoization it ran on every streamed
  // token as well as every render.
  const groupedTurns = useMemo(() => groupMessagesIntoTurns(messages), [messages]);
  const lastAssistantIndex = useMemo(
    () => lastAssistantTurnIndex(groupedTurns),
    [groupedTurns]
  );

  const isStreamingTurn =
    isSending ||
    Boolean(streamingTokenText) ||
    Boolean(streamingThinkingText) ||
    Object.keys(activeToolExecutions).length > 0;

  const canEditHistory = Boolean(conversationId) && !isSending && !pendingTurnAction;

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
      <div className="flex-1 min-h-0 relative">
        <div
          ref={messagesContainerRef}
          onScroll={handleScroll}
          className="h-full overflow-y-auto px-4 sm:px-6 py-6"
        >
          <div ref={messagesContentRef} className="max-w-3xl mx-auto w-full space-y-6">
            {groupedTurns.length === 0 && !isStreamingTurn && (
              <div className="text-center py-20 space-y-3">
                <div className="w-12 h-12 rounded-2xl bg-md-primary-container text-md-on-primary-container border border-md-outline-variant flex items-center justify-center mx-auto shadow-xs">
                  <Sparkles className="w-6 h-6" />
                </div>
                <h3 className="text-sm font-bold text-md-on-surface">{headerTitle || `Chat with ${agentName}`}</h3>
                <p className="text-xs text-md-on-surface-variant max-w-sm mx-auto leading-relaxed">
                  {headerSubtitle || `Ask ${agentName} a question, or give it a task to carry out.`}
                </p>
              </div>
            )}

            {groupedTurns.map((turn, index) => {
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

              return (
                <div key={turn.id} className="w-full space-y-2 pt-2 group">
                  {/* Collapsible Accordion for Thinking / Reasoning Tokens */}
                  {turn.thinking && <ThinkingAccordion content={turn.thinking} />}

                  {/* Direct Response Text on Background without enclosing white card or icons */}
                  {turn.content && (
                    <MarkdownContent
                      size="comfortable"
                      className="text-md-on-surface leading-relaxed"
                      resolveUrl={resolveFileUrl}
                    >
                      {turn.content}
                    </MarkdownContent>
                  )}

                  {/* Collapsible Dropdown Button for Tool Calls */}
                  {turn.toolCalls.length > 0 && (
                    <ToolCallsAccordion toolCalls={turn.toolCalls} />
                  )}

                  {/* Files this turn created or modified, opened in the side panel. */}
                  {onOpenFile &&
                    (() => {
                      const writtenFiles = extractWrittenFiles(turn.toolCalls);
                      if (writtenFiles.length === 0) return null;
                      return (
                        <div className="flex flex-wrap gap-1.5 pt-1">
                          {writtenFiles.map((path) => (
                            <button
                              key={path}
                              type="button"
                              onClick={() => onOpenFile(path)}
                              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-mono bg-md-surface-container-high text-md-on-surface border border-md-outline-variant hover:border-md-primary hover:text-md-primary transition-colors cursor-pointer"
                              title={`Open ${path}`}
                            >
                              <FileText className="w-3 h-3 shrink-0" />
                              {path.split('/').pop()}
                            </button>
                          ))}
                        </div>
                      );
                    })()}

                  {/* Revealed on hover: rewrite history from this point, or fork it. */}
                  {isPersistedTurn(turn) && !isStreamingTurn && (
                    <MessageActions
                      canRetry={index === lastAssistantIndex}
                      disabled={!canEditHistory}
                      pendingAction={
                        pendingTurnAction?.turnId === turn.id ? pendingTurnAction.action : null
                      }
                      content={turn.content || ''}
                      onAction={(action) => handleTurnAction(turn, action)}
                    />
                  )}
                </div>
              );
            })}

            {/* Tool calls held for explicit user approval. Rendered outside the
                streaming block: a reconnecting client is re-sent its pending
                approvals before any token arrives, and the turn stays blocked until
                one of these buttons is pressed. */}
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

            {/* Live Streaming Active Turn */}
            {isStreamingTurn && (
              <div className="w-full space-y-2 pt-2 animate-fade-in">
                {/* Agent Preparing / Thinking Loader before first token arrives */}
                {isSending &&
                  !streamingTokenText &&
                  !streamingThinkingText &&
                  Object.keys(activeToolExecutions).length === 0 &&
                  pendingApprovals.length === 0 && (
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
                  <MarkdownContent
                    size="comfortable"
                    className="text-md-on-surface leading-relaxed"
                    resolveUrl={resolveFileUrl}
                  >
                    {streamingTokenText}
                  </MarkdownContent>
                )}

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

        {/* Offered only once the reader has scrolled away from the newest output. */}
        {!isFollowingOutput && (
          <button
            type="button"
            onClick={() => {
              setIsFollowingOutput(true);
              // Instant rather than smooth: this can span the whole transcript, and
              // animating that far is slow enough to feel broken.
              scrollToBottom(false);
            }}
            className="absolute bottom-4 left-1/2 -translate-x-1/2 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-md-inverse-surface text-md-inverse-on-surface text-[11px] font-semibold shadow-lg hover:opacity-90 transition-opacity cursor-pointer"
          >
            <ArrowDown className="w-3.5 h-3.5" />
            {isStreamingTurn ? 'Jump to live output' : 'Jump to latest'}
          </button>
        )}
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
          {/* Renders at the size the transcript does, so what you type looks like
              what you get back. */}
          <AutoGrowTextarea
            textareaRef={composerRef}
            value={inputContent}
            onChange={setInputContent}
            onKeyDown={handleKeyDown}
            disabled={vllm.isOffline || vllm.isLoading}
            placeholder={
              vllm.isOffline
                ? `Local model ${vllm.modelId} is offline. Click 'Start Model' to begin...`
                : vllm.isLoading
                ? `Local model ${vllm.modelId} is initializing...`
                : placeholder || `Message ${agentName}... (Enter to send, Shift+Enter for new line)`
            }
            className="disabled:opacity-60"
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
              {vllm.isLocal && (
                vllm.isReady ? (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold text-emerald-800 dark:text-emerald-200 bg-emerald-100 dark:bg-emerald-950/80 border border-emerald-300 dark:border-emerald-800/80 px-2 py-0.5 rounded-full">
                    <CheckCircle2 className="w-2.5 h-2.5 stroke-[2.5]" /> Serving
                  </span>
                ) : vllm.isLoading ? (
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
              ) : vllm.isOffline ? (
                <button
                  type="button"
                  onClick={() => {
                    vllm.start().catch((err) =>
                      dialog.alert({
                        title: 'Model Startup Failed',
                        message: `Could not launch ${vllm.modelId}: ${err}`,
                        variant: 'danger',
                      })
                    );
                  }}
                  className="inline-flex items-center space-x-1.5 px-3.5 py-1.5 rounded-lg text-xs font-bold bg-md-primary text-md-on-primary hover:opacity-90 disabled:opacity-50 transition-opacity shadow-xs cursor-pointer"
                >
                  <Play className="w-3.5 h-3.5 fill-current" />
                  <span>Start Model</span>
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!inputContent.trim() || vllm.isLoading}
                  className="inline-flex items-center space-x-1 px-4 py-1.5 rounded-lg text-xs font-semibold bg-md-primary text-md-on-primary hover:opacity-90 disabled:opacity-40 disabled:hover:opacity-40 transition-opacity shadow-xs cursor-pointer"
                >
                  <span>{vllm.isLoading ? 'Model Loading...' : 'Send'}</span>
                  {vllm.isLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
                </button>
              )}
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
