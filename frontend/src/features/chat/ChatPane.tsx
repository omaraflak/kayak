import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { Conversation, Message } from '../../types';
import { api, errorMessage } from '../../api/client';
import { useChatAutoScroll } from '../../hooks/useChatAutoScroll';
import { useSSE, ToolApprovalRequest } from '../../hooks/useSSE';
import { useVllmModel } from '../../hooks/useVllmModel';
import { ToolCallCard } from './ToolCallCard';
import { ToolCallsAccordion } from './ToolCallsAccordion';
import { ThinkingAccordion } from './ThinkingAccordion';
import { MarkdownContent } from '../../ui/Markdown';
import { AutoGrowTextarea } from '../../ui/AutoGrowTextarea';
import { coalesceQueued, QueuedMessage } from './messageQueue';
import { useDictation, useMessageSpeech } from './useSpeech';
import { ProgressRing } from '../../ui/ProgressRing';
import { useVLLMStatus } from '../../context/VLLMStatusContext';
import { MessageAction, MessageActions } from './MessageActions';
import {
  GroupedTurn,
  countMessagesFrom,
  findPrecedingUserMessageId,
  groupMessagesIntoTurns,
  isPersistedTurn,
  lastAssistantTurnIndex,
} from './conversationTurns';
import { extractWrittenFiles, workspaceRelativePath } from '../workspace/workspaceFiles';
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
  Clock,
  Mic,
  Volume2,
  VolumeX,
} from 'lucide-react';
import { useDialog } from '../../context/DialogContext';

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
  /**
   * Disables the composer. Set for a transcript nobody may add to -- a sub-agent
   * session, which takes its instructions from the agent that started it.
   */
  readOnly?: boolean;
  /** Shown in the disabled composer, explaining why it cannot be typed into. */
  readOnlyMessage?: string;
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
  readOnly = false,
  readOnlyMessage,
}) => {
  const dialog = useDialog();
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputContent, setInputContent] = useState('');
  // Typed while the agent was still answering. Held here rather than sent, so
  // each does not start its own turn and get answered out of order.
  const [queued, setQueued] = useState<QueuedMessage[]>([]);
  const [speakReplies, setSpeakReplies] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [streamingTokenText, setStreamingTokenText] = useState('');
  const [streamingThinkingText, setStreamingThinkingText] = useState('');
  const [activeToolExecutions, setActiveToolExecutions] = useState<ActiveToolCalls>({});

  const [pendingApprovals, setPendingApprovals] = useState<ToolApprovalRequest[]>([]);
  const [streamWarning, setStreamWarning] = useState<string | null>(null);
  const [pendingTurnAction, setPendingTurnAction] = useState<
    { turnId: string; action: MessageAction } | null
  >(null);
  const vllm = useVllmModel(agentModel);

  const composerRef = useRef<HTMLTextAreaElement>(null);

  // Reading replies aloud and dictating them both need their model running, so
  // the controls appear only when the machine can actually do it.
  const { statuses: audioServers } = useVLLMStatus();
  const canSpeak = audioServers.speech?.state === 'ready';
  const canListen = audioServers.transcription?.state === 'ready';
  const spoken = useMessageSpeech(canSpeak);
  const dictation = useDictation((text) =>
    // Appended rather than replacing: dictation is often a second thought added
    // to something already typed.
    setInputContent((current) => (current.trim() ? `${current.trim()} ${text}` : text))
  );

  const transcript = useChatAutoScroll(
    [messages, streamingTokenText, streamingThinkingText, activeToolExecutions],
    conversationId
  );

  /** Reloads the transcript, and hands back what it loaded.

      Returned rather than only stored: a caller that needs to act on the fresh
      messages cannot read them from state, which has not re-rendered yet. */
  const loadConversationData = useCallback(async (): Promise<Message[]> => {
    if (!conversationId) {
      setMessages([]);
      setIsSending(false);
      return [];
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
      return data.messages || [];
    } catch (err) {
      console.error('Failed to load conversation messages:', err);
      return [];
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
  }, [conversationId, loadConversationData]);

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
      // Spoken after the reload rather than from the streamed text: speech is
      // kept against the message, and the message only has an id once it has
      // been persisted. The cost is a moment's delay before the voice starts.
      loadConversationData().then((loaded) => {
        if (speakRepliesRef.current) speakLatestRef.current(loaded);
      });
      onRefreshConversations?.();
      flushRef.current();
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

  /** Sends one message and starts a turn. The queue and the composer share it. */
  const deliver = async (text: string) => {
    if (!text.trim()) return;
    setIsSending(true);
    transcript.followOutput();

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

  const handleSend = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    // Guarded here as well as on the controls: Enter reaches this without them.
    if (readOnly) return;
    if (!inputContent.trim() || !vllm.isReady) return;

    const text = inputContent.trim();
    setInputContent('');

    // Mid-turn this joins the queue rather than being refused, and goes when the
    // agent finishes. Sending it now would start a second turn that the agent
    // answers before it has seen the first one's reply.
    if (isSending) {
      setQueued((previous) => [
        ...previous,
        { id: `queued_${Date.now()}_${previous.length}`, text },
      ]);
      return;
    }

    // Anything queued from a cancelled turn goes along with it, in order.
    const pending = coalesceQueued([...queued, { id: 'now', text }]);
    setQueued([]);
    await deliver(pending);
  };

  // The stream handlers are built before this function exists, and are not
  // rebuilt as it changes, so they reach it through a ref rather than closing
  // over a version of it from an earlier render.
  const deliverRef = useRef(deliver);
  deliverRef.current = deliver;
  const queuedRef = useRef(queued);
  queuedRef.current = queued;

  /**
   * Sends whatever was typed while the agent was working.
   *
   * Only after a turn that finished on its own. Stopping a turn is a decision to
   * stop, and firing the queue into a new one immediately afterwards would
   * override it.
   */
  const flushQueue = () => {
    const pending = queuedRef.current;
    if (pending.length === 0) return;
    const text = coalesceQueued(pending);
    setQueued([]);
    if (text) deliverRef.current(text);
  };
  const flushRef = useRef(flushQueue);
  flushRef.current = flushQueue;
  /** Reads the newest agent message aloud, once it has been persisted. */
  const speakLatest = (loaded: Message[]) => {
    const latest = [...loaded]
      .reverse()
      .find((message) => message.role === 'assistant' && message.content?.trim());
    if (latest?.id && latest.content) spoken.speak(latest.id, latest.content.trim());
  };
  const speakLatestRef = useRef(speakLatest);
  speakLatestRef.current = speakLatest;

  // Whenever the transcript changes, ask what speech exists for what is on it.
  // This is what brings a chip back after a reload, and what lets an older
  // message offer to play immediately when its audio is still cached.
  useEffect(() => {
    const ids = messages
      .filter((message) => message.role === 'assistant' && message.id)
      .map((message) => message.id!);
    spoken.sync(ids);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, canSpeak]);
  const speakRepliesRef = useRef(speakReplies);
  speakRepliesRef.current = speakReplies;
  // The text of the reply currently streaming, for reading it aloud once it ends.
  const streamedTextRef = useRef('');
  streamedTextRef.current = streamingTokenText;

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
        message: errorMessage(err),
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
        transcript.followOutput();
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
        message: errorMessage(err),
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
          ref={transcript.containerRef}
          onScroll={transcript.onScroll}
          className="h-full overflow-y-auto px-4 sm:px-6 py-6"
        >
          <div ref={transcript.contentRef} className="max-w-3xl mx-auto w-full space-y-6">
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

                  {/* Being synthesised right now. Read from the server rather
                      than from this page's memory, so it is still here after a
                      reload or a trip to another conversation. */}
                  {(() => {
                    const speech = turn.id ? spoken.states[turn.id] : undefined;
                    if (!speech) return null;

                    // A synthesis that died -- the speech container running out
                    // of memory, most often -- used to leave nothing on screen at
                    // all, so the reply simply never spoke and never said why.
                    if (speech.state === 'failed') {
                      return (
                        <div className="pt-0.5">
                          <span
                            className="inline-flex items-center gap-1.5 text-[10px] font-bold px-2 py-0.5 rounded-full border bg-rose-100 dark:bg-rose-950/80 text-rose-800 dark:text-rose-200 border-rose-300 dark:border-rose-800/80"
                            title={speech.error || undefined}
                          >
                            <AlertCircle className="w-2.5 h-2.5" />
                            COULD NOT SPEAK
                          </span>
                        </div>
                      );
                    }

                    if (speech.state !== 'pending') return null;
                    return (
                      <div className="pt-0.5">
                        <span className="inline-flex items-center gap-1.5 text-[10px] font-bold px-2 py-0.5 rounded-full border bg-md-primary-container text-md-on-primary-container border-md-primary/40">
                          <ProgressRing
                            fraction={
                              speech.chunks_total > 0
                                ? speech.chunks_done / speech.chunks_total
                                : undefined
                            }
                          />
                          {speech.chunks_total > 0
                            ? `${speech.chunks_done}/${speech.chunks_total}`
                            : 'SPEAKING'}
                        </span>
                      </div>
                    );
                  })()}

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
                      // Reading an older reply aloud does not depend on the
                      // toggle: that decides what happens automatically, this is
                      // an explicit ask for one message.
                      onSpeak={
                        canSpeak && turn.content && turn.id
                          ? () =>
                              spoken.playingId === turn.id
                                ? spoken.stop()
                                : spoken.speak(turn.id!, turn.content!.trim())
                          : undefined
                      }
                      // Playing rather than merely being synthesised: the button
                      // stops the audio, and there is nothing to stop until there
                      // is sound.
                      isPlaying={spoken.playingId === turn.id}
                      isPreparing={turn.id ? spoken.states[turn.id]?.state === 'pending' : false}
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
        {!transcript.isFollowing && (
          <button
            type="button"
            onClick={() => {
              transcript.followOutput();
              // Instant rather than smooth: this can span the whole transcript, and
              // animating that far is slow enough to feel broken.
              transcript.scrollToBottom(false);
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

      {/* A refused microphone, or a transcription that failed. Without this the
          button simply does nothing and there is no way to tell why. */}
      {dictation.error && (
        <div
          className={`px-4 pt-3 ${fullWidthInput ? 'w-full' : 'max-w-3xl mx-auto w-full'}`}
        >
          <div className="flex items-start gap-2 px-3 py-2 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-300 dark:border-rose-800/80 text-rose-900 dark:text-rose-100 text-[11px] leading-relaxed">
            <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <p className="flex-1">{dictation.error}</p>
            <button
              type="button"
              onClick={dictation.clearError}
              className="font-bold underline cursor-pointer shrink-0"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Waiting to be sent. Above the composer rather than in the transcript:
          they are not part of the conversation yet, and showing them as messages
          would claim the agent had seen them. */}
      {queued.length > 0 && (
        <div
          className={`px-4 pt-3 ${fullWidthInput ? 'w-full' : 'max-w-3xl mx-auto w-full'} space-y-1.5`}
        >
          <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-md-on-surface-variant">
            <Clock className="w-3 h-3" />
            <span>
              {queued.length === 1
                ? 'Queued · sends when the agent finishes'
                : `Queued · ${queued.length} messages send together when the agent finishes`}
            </span>
          </div>
          {queued.map((message) => (
            <div
              key={message.id}
              className="flex items-start justify-between gap-2 px-3 py-2 rounded-xl border border-dashed border-md-outline-variant bg-md-surface-container-low"
            >
              <p className="text-[11px] text-md-on-surface-variant leading-relaxed whitespace-pre-wrap flex-1 min-w-0">
                {message.text}
              </p>
              <button
                type="button"
                onClick={() =>
                  setQueued((previous) => previous.filter((entry) => entry.id !== message.id))
                }
                title="Remove from the queue"
                className="p-1 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high transition-colors cursor-pointer shrink-0"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
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
            disabled={readOnly || vllm.isOffline || vllm.isLoading}
            placeholder={
              readOnly
                ? readOnlyMessage || 'This conversation is read-only.'
                : vllm.isOffline
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
              {canSpeak && (
                <button
                  type="button"
                  onClick={() => {
                    // Switching it off stops what is being read now, rather
                    // than only affecting the next reply.
                    if (speakReplies) spoken.stop();
                    setSpeakReplies(!speakReplies);
                  }}
                  title={
                    speakReplies
                      ? 'Stop reading replies aloud'
                      : 'Read replies aloud as they arrive'
                  }
                  className={`p-1.5 rounded-lg transition-colors cursor-pointer ${
                    speakReplies
                      ? 'bg-md-primary-container text-md-on-primary-container'
                      : 'text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high'
                  }`}
                >
                  {speakReplies ? (
                    <Volume2 className={`w-3.5 h-3.5 ${spoken.playingId ? 'animate-pulse' : ''}`} />
                  ) : (
                    <VolumeX className="w-3.5 h-3.5" />
                  )}
                </button>
              )}

              {canListen && !readOnly && (
                <button
                  type="button"
                  onClick={() => (dictation.isRecording ? dictation.stop() : dictation.start())}
                  disabled={dictation.isTranscribing}
                  title={
                    dictation.isRecording
                      ? 'Stop recording and transcribe'
                      : 'Dictate a message'
                  }
                  className={`p-1.5 rounded-lg transition-colors cursor-pointer disabled:opacity-50 ${
                    dictation.isRecording
                      ? 'bg-md-error text-md-on-error'
                      : 'text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high'
                  }`}
                >
                  {dictation.isTranscribing ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Mic className={`w-3.5 h-3.5 ${dictation.isRecording ? 'animate-pulse' : ''}`} />
                  )}
                </button>
              )}

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
                  disabled={readOnly || !inputContent.trim() || vllm.isLoading}
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
