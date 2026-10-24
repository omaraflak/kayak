import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { Conversation, Message } from '../../types';
import { ApiError, api, errorMessage } from '../../api/client';
import { useChatAutoScroll } from '../../hooks/useChatAutoScroll';
import { useSSE, ToolApprovalRequest } from '../../hooks/useSSE';
import { useVllmModel } from '../../hooks/useVllmModel';
import { ToolCallCard } from './ToolCallCard';
import { ToolCallsAccordion } from './ToolCallsAccordion';
import { ThinkingAccordion } from './ThinkingAccordion';
import { MarkdownContent } from '../../ui/Markdown';
import { shortModelName } from '../../utils/format';
import { ErrorBoundary } from '../../ui/ErrorBoundary';
import { AutoGrowTextarea } from '../../ui/AutoGrowTextarea';
import { coalesceQueued, QueuedMessage } from './messageQueue';
import { useDictation, useMessageSpeech } from './useSpeech';
import { ProgressRing } from '../../ui/ProgressRing';
import { useVLLMStatus } from '../../context/VLLMStatusContext';
import { MessageAction, MessageActions } from './MessageActions';
import { isSendShortcut } from './sendShortcut';
import { humanizeToolName, toolCallDetail } from './toolCallLabel';
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
  RefreshCw,
} from 'lucide-react';
import { useDialog } from '../../context/DialogContext';

export type { GroupedTurn } from './conversationTurns';
export { groupMessagesIntoTurns } from './conversationTurns';

export interface ChatPaneProps {
  conversationId: string | null;
  agentName?: string;
  agentModel?: string;
  placeholder?: string;
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

/**
 * What was typed but not sent, per conversation.
 *
 * The pane is rebuilt for each conversation, so that nothing -- a queued message,
 * a turn's spinner, the previous transcript -- can carry over into the next one.
 * A half-written message is the one thing worth carrying back when you return.
 */
const drafts = new Map<string, string>();

export const ChatPane: React.FC<ChatPaneProps> = ({
  conversationId,
  agentName = 'Kayak Agent',
  agentModel,
  placeholder,
  onRefreshConversations,
  onConversationUpdated,
  onOpenConversation,
  onOpenFile,
  readOnly = false,
  readOnlyMessage,
}) => {
  const dialog = useDialog();
  const [messages, setMessages] = useState<Message[]>([]);
  // Distinguishes "could not load" from "nothing to show": a failed load used to
  // leave the previous conversation's messages on screen under this one's title.
  const [loadError, setLoadError] = useState(false);
  const [inputContent, setInputContent] = useState(
    () => (conversationId && drafts.get(conversationId)) || ''
  );
  // Typed while the agent was still answering. Held here rather than sent, so
  // each does not start its own turn and get answered out of order.
  const [queued, setQueued] = useState<QueuedMessage[]>([]);
  const [speakReplies, setSpeakReplies] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [streamingTokenText, setStreamingTokenText] = useState('');
  const [streamingThinkingText, setStreamingThinkingText] = useState('');
  const [activeToolExecutions, setActiveToolExecutions] = useState<ActiveToolCalls>({});

  const [pendingApprovals, setPendingApprovals] = useState<ToolApprovalRequest[]>([]);
  // Approvals whose answer is on its way, and one whose answer did not arrive.
  const [answeringApprovals, setAnsweringApprovals] = useState<ReadonlySet<string>>(new Set());
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [streamWarning, setStreamWarning] = useState<string | null>(null);
  // Why the turn just failed, until the reloaded transcript shows it in place.
  const [turnError, setTurnError] = useState<string | null>(null);
  // While a turn runs, the last message stored before it began. Everything after
  // it arrives in the replay a (re)connecting stream is sent, so the transcript
  // stops there until the turn ends; showing both put every step on screen twice.
  const [turnAnchor, setTurnAnchor] = useState<string | null>(null);
  // Set when a turn did not finish on its own -- it failed, or was stopped -- so its
  // closing event does not send the queue: not into a model that has just refused
  // to answer, and not over a decision to stop.
  const turnInterruptedRef = useRef(false);
  const [pendingTurnAction, setPendingTurnAction] = useState<
    { turnId: string; action: MessageAction } | null
  >(null);
  const vllm = useVllmModel(agentModel);

  const composerRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!conversationId) return;
    if (inputContent) drafts.set(conversationId, inputContent);
    else drafts.delete(conversationId);
  }, [conversationId, inputContent]);

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
  // Only the newest load may write: one started mid-turn and answered after the turn
  // ended would otherwise put the finished turn's spinner back, for good.
  const loadSequence = useRef(0);

  const loadConversationData = useCallback(async (): Promise<Message[]> => {
    const sequence = ++loadSequence.current;
    if (!conversationId) {
      setMessages([]);
      setIsSending(false);
      return [];
    }
    try {
      const data = await api.getConversation(conversationId);
      if (sequence !== loadSequence.current) return data.messages || [];
      // The database is authoritative here: a reload only happens after the server
      // has persisted the turn, so any optimistic placeholder is already represented.
      // Matching placeholders by content instead would drop a genuine repeat message.
      const loaded = data.messages || [];
      setMessages(loaded);
      setLoadError(false);
      // Once the stored explanation is in the transcript, it speaks for itself.
      if (loaded[loaded.length - 1]?.is_error) setTurnError(null);

      if (data.conversation?.status === 'running') {
        setIsSending(true);
      }
      return loaded;
    } catch (err) {
      console.error('Failed to load conversation messages:', err);
      if (sequence === loadSequence.current) setLoadError(true);
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
    setTurnAnchor(null);
  }, []);

  useSSE(conversationId, {
    onConnected: ({ isRunning, isReconnect, turnAnchor: anchor }) => {
      // Everything streamed so far is about to be replayed, so it is cleared rather
      // than shown twice -- and a turn that ended while the stream was away is
      // cleared for good. Without this a tab backgrounded mid-turn came back with
      // the answer doubled, or with a spinner for a turn long finished.
      setStreamingTokenText('');
      setStreamingThinkingText('');
      setActiveToolExecutions({});
      setPendingApprovals([]);
      setIsSending(isRunning);
      setTurnAnchor(isRunning ? anchor : null);
      // What finished meanwhile is only in the database.
      loadConversationData();
      if (isReconnect) onRefreshConversations?.();
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
      const interrupted = turnInterruptedRef.current;
      turnInterruptedRef.current = false;
      // Spoken after the reload rather than from the streamed text: speech is
      // kept against the message, and the message only has an id once it has
      // been persisted. The cost is a moment's delay before the voice starts.
      loadConversationData().then((loaded) => {
        if (speakRepliesRef.current && !interrupted) speakLatestRef.current(loaded);
      });
      onRefreshConversations?.();
      if (!interrupted) flushRef.current();
    },
    onCancelled: () => {
      // Followed by the closing event, which must not send the queue now.
      turnInterruptedRef.current = true;
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
      // The server stores why the turn failed, so the reload shows it in place,
      // under whatever the agent managed to say first, with a way to try again. The
      // event's own text covers the moment before that, and a failure that could
      // not be stored.
      turnInterruptedRef.current = true;
      setTurnError(errorMessage(err, 'The agent stopped because of an unexpected problem. Try again.'));
      resetStreamState();
      loadConversationData();
    },
  });

  /** Sends one message and starts a turn. The queue and the composer share it. */
  const deliver = async (text: string) => {
    if (!text.trim() || !conversationId) return;
    setIsSending(true);
    setTurnError(null);
    turnInterruptedRef.current = false;
    transcript.followOutput();

    // Optimistically insert user message immediately for instant UI feedback
    const placeholderId = `optimistic_${Date.now()}`;
    const tempUserMsg: Message = {
      id: placeholderId,
      conversation_id: conversationId,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, tempUserMsg]);

    try {
      await api.sendMessage(conversationId, text);
      onRefreshConversations?.();
    } catch (err) {
      // The message never reached the server, so leaving the placeholder in place
      // would show it as sent until the next reload. Take it back and say why.
      setIsSending(false);
      setMessages((prev) => prev.filter((message) => message.id !== placeholderId));
      setInputContent(text);
      dialog.alert({
        title: 'Message not sent',
        message: `${errorMessage(err, "Couldn't send your message.")} Your message is back in the box below.`,
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

  // Leaving the conversation keeps what was waiting to be sent: it goes back into
  // the draft, ahead of anything typed since, rather than vanishing with the pane.
  useEffect(
    () => () => {
      const waiting = coalesceQueued(queuedRef.current);
      if (!conversationId || !waiting) return;
      const draft = drafts.get(conversationId);
      drafts.set(conversationId, draft ? `${waiting}\n\n${draft}` : waiting);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

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
        title: "Couldn't stop the agent",
        message: errorMessage(err, "Couldn't stop the agent. Try again."),
        variant: 'danger',
      });
    }
  };

  const handleResolveApproval = async (callId: string, approved: boolean) => {
    if (!conversationId || answeringApprovals.has(callId)) return;
    // The card stays until the answer has arrived. Removing it first meant a
    // request that failed left the agent waiting on a question nobody could see.
    setAnsweringApprovals((current) => new Set(current).add(callId));
    setApprovalError(null);
    try {
      await api.resolveToolApproval(conversationId, callId, approved);
      setPendingApprovals((prev) => prev.filter((item) => item.id !== callId));
    } catch (err) {
      if (err instanceof ApiError && (err.status === 404 || err.status === 410)) {
        // The question is no longer being asked; the explanation says so.
        setPendingApprovals((prev) => prev.filter((item) => item.id !== callId));
      }
      setApprovalError(errorMessage(err, "Couldn't send your answer. Try again."));
    } finally {
      setAnsweringApprovals((current) => {
        const next = new Set(current);
        next.delete(callId);
        return next;
      });
    }
  };

  const handleTurnAction = async (turn: GroupedTurn, action: MessageAction) => {
    if (!conversationId || pendingTurnAction) return;

    // Retrying a turn that only holds why it failed loses nothing worth asking about.
    const onlyFailure = !turn.content && turn.toolCalls.length === 0 && Boolean(turn.error);

    // Revert and retry delete stored messages with no undo, so they state the cost.
    if ((action === 'revert' || action === 'retry') && !(action === 'retry' && onlyFailure)) {
      const anchorId =
        action === 'revert'
          ? findPrecedingUserMessageId(messages, turn.id) ?? turn.id
          : turn.id;
      const count = countMessagesFrom(messages, anchorId);
      const confirmed = await dialog.confirm({
        title: action === 'revert' ? 'Rewind to here?' : 'Try this reply again?',
        message:
          action === 'revert'
            ? `This removes ${count} message${count === 1 ? '' : 's'} from this conversation and puts your message back in the box below, so you can change it.`
            : `This removes ${count} message${count === 1 ? '' : 's'} and asks the agent to answer again.`,
        confirmText: action === 'revert' ? 'Rewind' : 'Try again',
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
        setTurnError(null);
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
      const what =
        action === 'revert'
          ? 'rewind the conversation'
          : action === 'retry'
          ? 'try again'
          : 'start a new chat from here';
      dialog.alert({
        title: `Couldn't ${what}`,
        message: errorMessage(err, `Couldn't ${what}. Try again.`),
        variant: 'danger',
      });
    } finally {
      setPendingTurnAction(null);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (isSendShortcut(e)) {
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
  const visibleMessages = useMemo(() => {
    if (!turnAnchor) return messages;
    const index = messages.findIndex((message) => message.id === turnAnchor);
    return index === -1 ? messages : messages.slice(0, index + 1);
  }, [messages, turnAnchor]);
  const groupedTurns = useMemo(() => groupMessagesIntoTurns(visibleMessages), [visibleMessages]);
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
      {/* Messages Scroll Area - Constrained to max-w-3xl for optimal line length and reading ergonomics */}
      <div className="flex-1 min-h-0 relative">
        <div
          ref={transcript.containerRef}
          onScroll={transcript.onScroll}
          className="h-full overflow-y-auto px-4 sm:px-6 py-6"
        >
          <div ref={transcript.contentRef} className="max-w-3xl mx-auto w-full space-y-6">
            {loadError && (
              <div className="flex items-start gap-2 rounded-xl border border-rose-300 dark:border-rose-800/80 bg-rose-50 dark:bg-rose-950/40 px-3.5 py-2.5 text-xs text-rose-900 dark:text-rose-100">
                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                <p className="flex-1 leading-relaxed">Couldn't open this conversation.</p>
                <button
                  type="button"
                  onClick={() => loadConversationData()}
                  className="font-bold underline cursor-pointer shrink-0"
                >
                  Try again
                </button>
              </div>
            )}

            {groupedTurns.length === 0 && !isStreamingTurn && !loadError && (
              <div className="text-center py-20 space-y-3">
                <div className="w-12 h-12 rounded-2xl bg-md-primary-container text-md-on-primary-container border border-md-outline-variant flex items-center justify-center mx-auto shadow-xs">
                  <Sparkles className="w-6 h-6" />
                </div>
                <h3 className="text-sm font-bold text-md-on-surface">{`Chat with ${agentName}`}</h3>
                <p className="text-xs text-md-on-surface-variant max-w-sm mx-auto leading-relaxed">
                  {`Ask ${agentName} a question, or give it a task to carry out.`}
                </p>
              </div>
            )}

            {groupedTurns.map((turn, index) => {
              if (turn.role === 'user') {
                return (
                  <div key={turn.id} className="flex justify-end pt-2">
                    <div className="max-w-2xl bg-md-primary text-md-on-primary rounded-2xl rounded-tr-xs px-5 py-3 text-[15px] shadow-xs leading-relaxed">
                      <ErrorBoundary fallback={<PlainText text={turn.content || ''} />}>
                        <MarkdownContent variant="on-primary">
                          {turn.content || ''}
                        </MarkdownContent>
                      </ErrorBoundary>
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
                    // One reply that cannot be rendered falls back to its text,
                    // rather than taking the rest of the conversation with it.
                    <ErrorBoundary fallback={<PlainText text={turn.content} />}>
                      <MarkdownContent
                        size="comfortable"
                        className="text-md-on-surface leading-relaxed"
                        resolveUrl={resolveFileUrl}
                      >
                        {turn.content}
                      </MarkdownContent>
                    </ErrorBoundary>
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
                            Couldn't read aloud
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
                            ? `Preparing audio ${speech.chunks_done}/${speech.chunks_total}`
                            : 'Preparing audio'}
                        </span>
                      </div>
                    );
                  })()}

                  {/* Collapsible Dropdown Button for Tool Calls */}
                  {turn.toolCalls.length > 0 && (
                    <ErrorBoundary fallback={null}>
                      <ToolCallsAccordion toolCalls={turn.toolCalls} />
                    </ErrorBoundary>
                  )}

                  {/* Why the turn stopped, kept with it so it is still here after a
                      reload, with the obvious next step one click away. */}
                  {turn.error && (
                    <FailureNotice
                      message={turn.error}
                      onRetry={
                        index === lastAssistantIndex && isPersistedTurn(turn)
                          ? () => handleTurnAction(turn, 'retry')
                          : undefined
                      }
                      disabled={!canEditHistory}
                      isRetrying={
                        pendingTurnAction?.turnId === turn.id &&
                        pendingTurnAction.action === 'retry'
                      }
                    />
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

                  {/* Revealed on hover: rewrite history from this point, or fork it.
                      A turn that is only an explanation of a failure offers its
                      own Try again, and has nothing to copy, read or branch. */}
                  {isPersistedTurn(turn) &&
                    !isStreamingTurn &&
                    (turn.content || turn.toolCalls.length > 0) && (
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
            {pendingApprovals.map((approval) => {
              const detail = toolCallDetail(approval.arguments);
              const answering = answeringApprovals.has(approval.id);
              return (
                <div
                  key={approval.id}
                  className="rounded-xl border border-amber-300 dark:border-amber-800/80 bg-amber-50 dark:bg-amber-950/40 p-3.5 space-y-2.5"
                >
                  <div className="flex items-start gap-2 text-xs text-amber-900 dark:text-amber-100">
                    <ShieldQuestion className="w-4 h-4 shrink-0 mt-px" />
                    <p className="leading-relaxed">
                      <span className="font-bold">
                        {agentName} wants to {humanizeToolName(approval.name).toLowerCase()}
                      </span>
                      {detail && (
                        <>
                          {': '}
                          <code className="font-mono px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-900/60 border border-amber-300 dark:border-amber-800 break-all">
                            {detail}
                          </code>
                        </>
                      )}
                      . Allow it?
                    </p>
                  </div>
                  {approval.arguments && (
                    <details className="text-[11px] text-amber-900 dark:text-amber-100/90">
                      <summary className="cursor-pointer select-none font-semibold">Details</summary>
                      <pre className="mt-1.5 font-mono bg-amber-100/70 dark:bg-amber-900/40 rounded-lg p-2.5 overflow-x-auto max-h-40">
                        {approval.arguments}
                      </pre>
                    </details>
                  )}
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      disabled={answering}
                      onClick={() => handleResolveApproval(approval.id, true)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold bg-md-primary text-md-on-primary hover:opacity-90 disabled:opacity-50 transition-opacity cursor-pointer"
                    >
                      {answering ? (
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <Check className="w-3.5 h-3.5 stroke-[3]" />
                      )}{' '}
                      Allow
                    </button>
                    <button
                      type="button"
                      disabled={answering}
                      onClick={() => handleResolveApproval(approval.id, false)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold bg-md-surface-container-high text-md-on-surface border border-md-outline-variant hover:opacity-90 disabled:opacity-50 transition-opacity cursor-pointer"
                    >
                      <X className="w-3.5 h-3.5 stroke-[3]" /> Don't allow
                    </button>
                  </div>
                </div>
              );
            })}

            {approvalError && (
              <div className="flex items-start gap-2 rounded-xl border border-rose-300 dark:border-rose-800/80 bg-rose-50 dark:bg-rose-950/40 px-3.5 py-2.5 text-xs text-rose-900 dark:text-rose-100">
                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                <p className="flex-1 leading-relaxed">{approvalError}</p>
                <button
                  type="button"
                  onClick={() => setApprovalError(null)}
                  className="font-bold underline cursor-pointer shrink-0"
                >
                  Dismiss
                </button>
              </div>
            )}

            {/* A failure the transcript does not show yet, or could not store. */}
            {turnError && !isStreamingTurn && !groupedTurns[groupedTurns.length - 1]?.error && (
              <FailureNotice message={turnError} />
            )}

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
                      <span className="text-xs font-medium text-md-on-surface-variant">Thinking…</span>
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
                  <ErrorBoundary fallback={<PlainText text={streamingTokenText} />}>
                    <MarkdownContent
                      size="comfortable"
                      className="text-md-on-surface leading-relaxed"
                      resolveUrl={resolveFileUrl}
                    >
                      {streamingTokenText}
                    </MarkdownContent>
                  </ErrorBoundary>
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
                        // An empty result is still a result.
                        isExecuting={t.output === undefined}
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
          <div className={`max-w-3xl mx-auto flex items-start gap-2 rounded-xl border border-amber-300 dark:border-amber-800/80 bg-amber-50 dark:bg-amber-950/40 px-3.5 py-2.5`}>
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

      {/* Reading a reply aloud failed. It used to fail silently: the button
          simply did nothing. */}
      {spoken.error && (
        <div className={`px-4 pt-3 max-w-3xl mx-auto w-full`}>
          <div className="flex items-start gap-2 px-3 py-2 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-300 dark:border-rose-800/80 text-rose-900 dark:text-rose-100 text-[11px] leading-relaxed">
            <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <p className="flex-1">{spoken.error}</p>
            <button
              type="button"
              onClick={spoken.clearError}
              className="font-bold underline cursor-pointer shrink-0"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* A refused microphone, or a transcription that failed. Without this the
          button simply does nothing and there is no way to tell why. */}
      {dictation.error && (
        <div
          className={`px-4 pt-3 max-w-3xl mx-auto w-full`}
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
          className={`px-4 pt-3 max-w-3xl mx-auto w-full space-y-1.5`}
        >
          <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-md-on-surface-variant">
            <Clock className="w-3 h-3" />
            <span>
              {isSending
                ? queued.length === 1
                  ? 'Waiting · sends when the agent finishes'
                  : `Waiting · ${queued.length} messages send together when the agent finishes`
                : 'Not sent yet · goes with your next message'}
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
          className={`max-w-3xl mx-auto relative bg-md-surface-container-lowest border border-md-outline-variant rounded-2xl overflow-hidden focus-within:border-md-primary focus-within:ring-2 focus-within:ring-md-primary/20 shadow-xs transition-all`}
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
                : vllm.isError
                ? `${shortModelName(vllm.modelId || '')} couldn't start. Open Local Models to see why, or click Try again.`
                : vllm.isOffline
                ? `${shortModelName(vllm.modelId || '')} isn't running. Click Start model to use it.`
                : vllm.isLoading
                ? `${shortModelName(vllm.modelId || '')} is starting…`
                : placeholder || `Message ${agentName}… (Enter to send, Shift+Enter for a new line)`
            }
            className="disabled:opacity-60"
          />

          <div className="flex items-center justify-between px-3.5 py-2 bg-md-surface-container-low border-t border-md-outline-variant">
            <div className="flex items-center space-x-2 text-[11px] text-md-on-surface-variant">
              <Sparkles className="w-3.5 h-3.5 text-md-primary" />
              <span className="font-semibold text-md-on-surface">{agentName}</span>
              {agentModel && (
                <span className="text-[10px] text-md-on-surface-variant">
                  ({shortModelName(agentModel)})
                </span>
              )}
              {vllm.isLocal && (
                vllm.isReady ? (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold text-emerald-800 dark:text-emerald-200 bg-emerald-100 dark:bg-emerald-950/80 border border-emerald-300 dark:border-emerald-800/80 px-2 py-0.5 rounded-full">
                    <CheckCircle2 className="w-2.5 h-2.5 stroke-[2.5]" /> Running
                  </span>
                ) : vllm.isLoading ? (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold text-amber-800 dark:text-amber-200 bg-amber-100 dark:bg-amber-950/80 border border-amber-300 dark:border-amber-800/80 px-2 py-0.5 rounded-full">
                    <Loader2 className="w-2.5 h-2.5 animate-spin" /> Starting
                  </span>
                ) : vllm.isError ? (
                  <span
                    className="inline-flex items-center gap-1 text-[10px] font-bold text-rose-800 dark:text-rose-200 bg-rose-100 dark:bg-rose-950/80 border border-rose-300 dark:border-rose-800/80 px-2 py-0.5 rounded-full"
                    title={vllm.errorMessage || undefined}
                  >
                    <AlertCircle className="w-2.5 h-2.5" /> Couldn't start
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold text-md-on-surface-variant bg-md-surface-container-highest border border-md-outline-variant px-2 py-0.5 rounded-full">
                    Not running
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
                        title: "Couldn't start the model",
                        message: errorMessage(err, "Couldn't start the model. Try again."),
                        variant: 'danger',
                      })
                    );
                  }}
                  className="inline-flex items-center space-x-1.5 px-3.5 py-1.5 rounded-lg text-xs font-bold bg-md-primary text-md-on-primary hover:opacity-90 disabled:opacity-50 transition-opacity shadow-xs cursor-pointer"
                >
                  <Play className="w-3.5 h-3.5 fill-current" />
                  <span>{vllm.isError ? 'Try again' : 'Start model'}</span>
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={readOnly || !inputContent.trim() || vllm.isLoading}
                  className="inline-flex items-center space-x-1 px-4 py-1.5 rounded-lg text-xs font-semibold bg-md-primary text-md-on-primary hover:opacity-90 disabled:opacity-40 disabled:hover:opacity-40 transition-opacity shadow-xs cursor-pointer"
                >
                  <span>{vllm.isLoading ? 'Model starting…' : 'Send'}</span>
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

/** A message shown as plain text, for content that could not be rendered. */
const PlainText: React.FC<{ text: string }> = ({ text }) => (
  <p className="whitespace-pre-wrap text-[15px] leading-relaxed">{text}</p>
);

/** Why a turn stopped, with the way to try again when there is one. */
const FailureNotice: React.FC<{
  message: string;
  onRetry?: () => void;
  disabled?: boolean;
  isRetrying?: boolean;
}> = ({ message, onRetry, disabled, isRetrying }) => (
  <div className="flex items-start gap-2.5 rounded-xl border border-rose-300 dark:border-rose-800/80 bg-rose-50 dark:bg-rose-950/40 px-3.5 py-2.5">
    <AlertCircle className="w-4 h-4 text-rose-700 dark:text-rose-300 shrink-0 mt-0.5" />
    <p className="flex-1 text-xs text-rose-900 dark:text-rose-100 leading-relaxed">{message}</p>
    {onRetry && (
      <button
        type="button"
        onClick={onRetry}
        disabled={disabled}
        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-bold bg-md-surface text-md-on-surface border border-md-outline-variant hover:opacity-90 disabled:opacity-40 transition-opacity cursor-pointer shrink-0"
      >
        {isRetrying ? (
          <Loader2 className="w-3 h-3 animate-spin" />
        ) : (
          <RefreshCw className="w-3 h-3" />
        )}
        Try again
      </button>
    )}
  </div>
);
