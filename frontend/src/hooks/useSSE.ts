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
   */
  onConnected?: (data: { status: string; isRunning: boolean }) => void;
  /** The stored history changed underneath us -- reload it. */
  onHistoryChanged?: () => void;
}

export function useSSE(conversationId: string | null, callbacks: SSECallbacks) {
  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;

  useEffect(() => {
    if (!conversationId) return;

    const eventSource = new EventSource(`/api/conversations/${conversationId}/events`);

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
            });
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
      // EventSource reconnects on its own; the server replays the in-flight turn
      // buffer on connect, so no recovery is needed here.
    };

    return () => {
      eventSource.close();
    };
  }, [conversationId]);
}
