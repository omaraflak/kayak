import { useEffect, useRef } from 'react';

export interface SSECallbacks {
  onToken?: (token: string) => void;
  onToolCallDelta?: (delta: { id: string; name?: string; arguments?: string }) => void;
  onToolCallExecuting?: (data: { id: string; name: string; arguments: string }) => void;
  onToolCallResult?: (data: { id: string; name: string; output: string; is_error: boolean }) => void;
  onTaskEvent?: (event: any) => void;
  onDone?: () => void;
  onCancelled?: () => void;
  onError?: (error: string) => void;
  onUserMessage?: (message: any) => void;
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

    eventSource.onerror = (err) => {
      // EventSource automatically attempts reconnection
    };

    return () => {
      eventSource.close();
    };
  }, [conversationId]);
}
