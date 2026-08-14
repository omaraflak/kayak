import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { VLLMDeploymentProgress } from '../types';
import { api } from '../api/client';

/**
 * Single source of vLLM server status for the whole app.
 *
 * Five components previously opened their own EventSource against the same
 * endpoint. Browsers cap concurrent HTTP/1.1 connections at six per origin, so
 * together with the conversation stream that ceiling was reachable during normal
 * use -- at which point new requests simply hang. One connection is shared here and
 * fanned out through context instead.
 */

const LOG_BUFFER_LIMIT = 300;
const LOADING_STATES = ['pulling_image', 'starting_container', 'loading'];

interface VLLMStatusContextValue {
  status: VLLMDeploymentProgress | null;
  logs: string[];
  /** Re-fetches status over REST, for use right after a mutating call. */
  refresh: () => Promise<void>;
  /** True while an image pull, container start, or model load is in progress. */
  isBusy: boolean;
}

const VLLMStatusContext = createContext<VLLMStatusContextValue | null>(null);

export const VLLMStatusProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [status, setStatus] = useState<VLLMDeploymentProgress | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const eventSourceRef = useRef<EventSource | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.getVLLMStatus();
      setStatus(data);
      if (data.logs_tail?.length) {
        setLogs(data.logs_tail);
      }
    } catch (err) {
      console.error('Failed to fetch vLLM status:', err);
    }
  }, []);

  useEffect(() => {
    refresh();

    const eventSource = new EventSource('/api/vllm/events');
    eventSourceRef.current = eventSource;

    const handleStatus = (event: MessageEvent) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.data) setStatus(payload.data);
      } catch {
        /* a malformed frame should not tear down the stream */
      }
    };

    const handleLog = (event: MessageEvent) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.line) {
          setLogs((prev) => [...prev.slice(-LOG_BUFFER_LIMIT), payload.line]);
        }
      } catch {
        /* ignore malformed log frames */
      }
    };

    eventSource.addEventListener('status', handleStatus);
    eventSource.addEventListener('update', handleStatus);
    eventSource.addEventListener('log', handleLog);

    // Re-sync on every (re)connect. The browser quietly reconnects after the server
    // restarts, and any state change the fresh server broadcast before this listener
    // attached is otherwise lost -- leaving the page describing the old process.
    eventSource.onopen = () => {
      refresh();
    };

    return () => {
      eventSource.close();
      eventSourceRef.current = null;
    };
  }, [refresh]);

  const isBusy = Boolean(status && LOADING_STATES.includes(status.state));

  return (
    <VLLMStatusContext.Provider value={{ status, logs, refresh, isBusy }}>
      {children}
    </VLLMStatusContext.Provider>
  );
};

/** Reads shared vLLM status. Must be called within a VLLMStatusProvider. */
export function useVLLMStatus(): VLLMStatusContextValue {
  const context = useContext(VLLMStatusContext);
  if (!context) {
    throw new Error('useVLLMStatus must be used within a VLLMStatusProvider');
  }
  return context;
}

/** States in which the local server is mid-deployment rather than serving. */
export const VLLM_LOADING_STATES = LOADING_STATES;
