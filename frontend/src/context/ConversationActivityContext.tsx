import React, { createContext, useContext, useEffect, useState } from 'react';
import { applyActivityEvent } from '../features/conversations/conversationActivity';

/**
 * Which conversations are working, and whether the server is there at all.
 *
 * The sidebar is visible on every page, so this cannot be derived from the
 * conversation currently open: leaving the chat tab unmounts that pane, and the
 * indicator used to vanish with it while the agent kept working. One shared
 * connection reports every conversation -- including sub-agent runs -- and each
 * stream opens with a snapshot, so a reconnect after a server restart is
 * self-correcting.
 *
 * The same connection is the app's sense of whether the server is reachable. It is
 * open for as long as the page is, so it notices a restart (an update, the Mac
 * waking up) within moments, and its reopening is the signal for everything else
 * to refresh what it showed before the server went away.
 */

interface ConversationActivityValue {
  /** Conversation ids with a turn in flight. */
  runningIds: ReadonlySet<string>;
  /** False while the server cannot be reached. */
  isOnline: boolean;
  /** Increments each time the connection comes back after being lost. */
  reconnections: number;
}

const ConversationActivityContext = createContext<ConversationActivityValue | null>(null);

const EMPTY: ReadonlySet<string> = new Set();

/** How long to wait before reopening a stream the browser gave up on. */
const REOPEN_DELAYS_MS = [1000, 2000, 5000, 10000];

export const ConversationActivityProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const [runningIds, setRunningIds] = useState<ReadonlySet<string>>(EMPTY);
  const [isOnline, setIsOnline] = useState(true);
  const [reconnections, setReconnections] = useState(0);

  useEffect(() => {
    let eventSource: EventSource | null = null;
    let reopenTimer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    let wasLost = false;
    let disposed = false;

    const open = () => {
      eventSource = new EventSource('/api/activity/events');

      eventSource.onopen = () => {
        failures = 0;
        setIsOnline(true);
        if (wasLost) {
          wasLost = false;
          setReconnections((count) => count + 1);
        }
      };

      eventSource.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          setRunningIds((previous) => applyActivityEvent(previous, payload));
        } catch {
          /* a malformed frame should not tear down the stream */
        }
      };

      eventSource.onerror = () => {
        wasLost = true;
        setIsOnline(false);
        // The browser retries a dropped connection by itself, but gives up for
        // good on an error answer -- which a server part-way through restarting
        // can give. Reopening here is what keeps the app from staying offline
        // until someone reloads it.
        if (eventSource?.readyState === EventSource.CLOSED && !disposed) {
          const delay = REOPEN_DELAYS_MS[Math.min(failures, REOPEN_DELAYS_MS.length - 1)];
          failures += 1;
          reopenTimer = setTimeout(open, delay);
        }
      };
    };

    open();

    return () => {
      disposed = true;
      clearTimeout(reopenTimer);
      eventSource?.close();
    };
  }, []);

  return (
    <ConversationActivityContext.Provider value={{ runningIds, isOnline, reconnections }}>
      {children}
    </ConversationActivityContext.Provider>
  );
};

/** Reads the shared set of working conversations and the connection state. */
export function useConversationActivity(): ConversationActivityValue {
  const context = useContext(ConversationActivityContext);
  if (!context) {
    throw new Error(
      'useConversationActivity must be used within a ConversationActivityProvider'
    );
  }
  return context;
}
