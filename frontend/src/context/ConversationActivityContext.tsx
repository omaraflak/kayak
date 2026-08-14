import React, { createContext, useContext, useEffect, useState } from 'react';
import { applyActivityEvent } from '../components/conversationActivity';

/**
 * Which conversations are working, for the whole app.
 *
 * The sidebar is visible on every page, so this cannot be derived from the
 * conversation currently open: leaving the chat tab unmounts that pane, and the
 * indicator used to vanish with it while the agent kept working. One shared
 * connection reports every conversation -- including sub-agent runs -- and each
 * stream opens with a snapshot, so a reconnect after a server restart is
 * self-correcting.
 */

interface ConversationActivityValue {
  /** Conversation ids with a turn in flight. */
  runningIds: ReadonlySet<string>;
}

const ConversationActivityContext = createContext<ConversationActivityValue | null>(null);

const EMPTY: ReadonlySet<string> = new Set();

export const ConversationActivityProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const [runningIds, setRunningIds] = useState<ReadonlySet<string>>(EMPTY);

  useEffect(() => {
    const eventSource = new EventSource('/api/activity/events');

    eventSource.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        setRunningIds((previous) => applyActivityEvent(previous, payload));
      } catch {
        /* a malformed frame should not tear down the stream */
      }
    };

    eventSource.onerror = () => {
      // The browser reconnects on its own, and the snapshot that opens the new
      // stream restores the correct state.
    };

    return () => {
      eventSource.close();
    };
  }, []);

  return (
    <ConversationActivityContext.Provider value={{ runningIds }}>
      {children}
    </ConversationActivityContext.Provider>
  );
};

/** Reads the shared set of working conversations. */
export function useConversationActivity(): ConversationActivityValue {
  const context = useContext(ConversationActivityContext);
  if (!context) {
    throw new Error(
      'useConversationActivity must be used within a ConversationActivityProvider'
    );
  }
  return context;
}
