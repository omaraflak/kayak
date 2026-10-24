import { useEffect, useRef } from 'react';
import { Message, TaskStatus } from '../types';

export interface ToolApprovalRequest {
  id: string;
  name: string;
  arguments: string;
}

/** A background task or sub-agent reporting progress on this conversation's stream. */
export type TaskStreamEvent =
  | { type: 'task_started'; task_id: string; name: string; pid?: number }
  | { type: 'task_output'; task_id: string; stream: 'stdout' | 'stderr'; text: string }
  | {
      type: 'task_finished';
      task_id: string;
      name: string;
      status: TaskStatus;
      exit_code?: number | null;
      error?: string;
    }
  | {
      type: 'subagent_started';
      task_id: string;
      subagent_conversation_id: string;
      agent_id: string;
    }
  | {
      type: 'subagent_finished';
      task_id: string;
      subagent_conversation_id: string;
      status: string;
      result?: string;
      error?: string;
    };

export interface SSECallbacks {
  onToken?: (token: string) => void;
  onThinking?: (token: string) => void;
  onToolCallDelta?: (delta: { id: string; name?: string; arguments?: string }) => void;
  onToolCallExecuting?: (data: { id: string; name: string; arguments: string }) => void;
  /** `arguments` is only present on replayed results, for clients that missed the executing event. */
  onToolCallResult?: (data: { id: string; name: string; arguments?: string; output: string; is_error: boolean }) => void;
  onToolApprovalRequired?: (data: ToolApprovalRequest) => void;
  onTaskEvent?: (event: TaskStreamEvent) => void;
  onTitleUpdated?: (title: string) => void;
  onWarning?: (warning: string) => void;
  onMaxIterations?: (data: { limit: number; content: string }) => void;
  onDone?: () => void;
  onCancelled?: () => void;
  onError?: (error: string) => void;
  onUserMessage?: (message: Message) => void;
  /**
   * Fired on every (re)connect with whether a turn is actually in flight. A tab that
   * was backgrounded mid-turn has no other way to recover its composer state.
   *
   * The server follows it with a replay of the turn so far, so anything streamed
   * before a reconnect is about to arrive again.
   */
  onConnected?: (data: {
    status: string;
    isRunning: boolean;
    isReconnect: boolean;
    /** While a turn runs, the last message stored before it began. */
    turnAnchor: string | null;
  }) => void;
  /** The stored history changed underneath us -- reload it. */
  onHistoryChanged?: () => void;
}

export function useSSE(conversationId: string | null, callbacks: SSECallbacks) {
  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;

  useEffect(() => {
    if (!conversationId) return;

    let eventSource: EventSource | null = null;
    let reopenTimer: ReturnType<typeof setTimeout> | undefined;
    let hasConnected = false;
    let disposed = false;
    // Grows while the server keeps refusing -- a conversation deleted in another
    // window answers "not found" for good -- and resets once a stream connects.
    let reopenDelay = 2000;

    const open = () => {
      eventSource = new EventSource(`/api/conversations/${conversationId}/events`);

      eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);

          switch (data.type) {
            case 'thinking':
              callbacksRef.current.onThinking?.(data.content);
              break;
            case 'token':
              callbacksRef.current.onToken?.(data.content);
              break;
            case 'tool_call_delta':
              callbacksRef.current.onToolCallDelta?.({
                id: data.id,
                name: data.name,
                arguments: data.arguments,
              });
              break;
            case 'tool_call_executing':
              callbacksRef.current.onToolCallExecuting?.(data);
              break;
            case 'tool_call_result':
              callbacksRef.current.onToolCallResult?.(data);
              break;
            case 'tool_approval_required':
              callbacksRef.current.onToolApprovalRequired?.({
                id: data.id,
                name: data.name,
                arguments: data.arguments,
              });
              break;
            case 'task_started':
            case 'task_output':
            case 'task_finished':
            case 'subagent_started':
            case 'subagent_finished':
              callbacksRef.current.onTaskEvent?.(data);
              break;
            case 'user_message':
              callbacksRef.current.onUserMessage?.(data.message);
              break;
            case 'title_updated':
              callbacksRef.current.onTitleUpdated?.(data.title);
              break;
            case 'warning':
              callbacksRef.current.onWarning?.(data.warning);
              break;
            case 'max_iterations':
              callbacksRef.current.onMaxIterations?.({
                limit: data.limit,
                content: data.content,
              });
              break;
            case 'connected':
              callbacksRef.current.onConnected?.({
                status: data.status,
                isRunning: Boolean(data.is_running),
                isReconnect: hasConnected,
                turnAnchor: typeof data.turn_anchor === 'string' ? data.turn_anchor : null,
              });
              hasConnected = true;
              reopenDelay = 2000;
              break;
            case 'history_changed':
              callbacksRef.current.onHistoryChanged?.();
              break;
            case 'done':
              callbacksRef.current.onDone?.();
              break;
            case 'cancelled':
              callbacksRef.current.onCancelled?.();
              callbacksRef.current.onDone?.();
              break;
            case 'error':
              callbacksRef.current.onError?.(data.error);
              break;
            default:
              break;
          }
        } catch (err) {
          console.error('Error parsing SSE event:', err);
        }
      };

      eventSource.onerror = () => {
        // EventSource reconnects on its own after a dropped connection, and the
        // server replays the in-flight turn on connect. It gives up for good on an
        // error answer, though, which a restarting server can give, and the chat
        // would then never hear about its turn again.
        if (eventSource?.readyState === EventSource.CLOSED && !disposed) {
          reopenTimer = setTimeout(open, reopenDelay);
          reopenDelay = Math.min(reopenDelay * 2, 60000);
        }
      };
    };

    open();

    return () => {
      disposed = true;
      clearTimeout(reopenTimer);
      eventSource?.close();
    };
  }, [conversationId]);
}
