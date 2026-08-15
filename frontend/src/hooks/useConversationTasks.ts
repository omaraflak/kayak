import { useCallback, useEffect, useRef, useState } from 'react';
import { BackgroundTask } from '../types';
import { api } from '../api/client';
import { TASK_POLL_INTERVAL_MS } from '../features/workspace/conversationTasks';

/**
 * The background tasks running in a conversation's container.
 *
 * Polled rather than streamed: task output already has an SSE channel, but the count
 * has to stay right while the drawer is shut and across a reload, and one small
 * request every few seconds is simpler than reconciling a stream against a list that
 * a page load has to fetch anyway.
 *
 * Repeat polling pauses while the tab is hidden so a forgotten background tab stops
 * asking. The first read is not subject to that: a conversation can be opened in a
 * tab that is not in the foreground -- restored on startup, or opened in the
 * background -- and it must still know what is running when you look at it.
 */
export function useConversationTasks(conversationId: string | null): {
  tasks: BackgroundTask[];
  refresh: () => Promise<void>;
} {
  const [tasks, setTasks] = useState<BackgroundTask[]>([]);
  // A response that arrives after the conversation changed describes the previous
  // container, so it must not be written into state.
  const requestedForRef = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    requestedForRef.current = conversationId;
    if (!conversationId) {
      setTasks([]);
      return;
    }
    try {
      const listing = await api.listTasks(conversationId);
      if (requestedForRef.current === conversationId) setTasks(listing);
    } catch {
      // A failed poll is not worth a dialog; the next one corrects the list.
    }
  }, [conversationId]);

  useEffect(() => {
    if (!conversationId) {
      setTasks([]);
      return;
    }

    refresh();

    const pollIfVisible = () => {
      if (document.visibilityState !== 'hidden') refresh();
    };
    const interval = window.setInterval(pollIfVisible, TASK_POLL_INTERVAL_MS);
    // Returning to the tab should show the truth at once, not one tick later.
    document.addEventListener('visibilitychange', pollIfVisible);

    return () => {
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', pollIfVisible);
    };
  }, [conversationId, refresh]);

  return { tasks, refresh };
}
