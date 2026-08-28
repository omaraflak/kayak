import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { Modality, VLLMDeploymentProgress } from '../types';
import { api } from '../api/client';

/**
 * Single source of local server status for the whole app.
 *
 * Five components previously opened their own EventSource against the same
 * endpoint. Browsers cap concurrent HTTP/1.1 connections at six per origin, so
 * together with the conversation stream that ceiling was reachable during normal
 * use -- at which point new requests simply hang. One connection is shared here and
 * fanned out through context instead.
 *
 * That one connection now carries every modality, so each frame is routed by the
 * modality it names. Without that routing a voice model's startup logs would appear
 * under the text model and its "ready" would mark the wrong server online.
 */

const LOG_BUFFER_LIMIT = 300;
const LOADING_STATES = ['pulling_image', 'starting_container', 'loading'];

/** The frame shape the server sends; `modality` is stamped on every event. */
interface ServerEvent {
  modality?: Modality;
  data?: VLLMDeploymentProgress;
  line?: string;
}

interface VLLMStatusContextValue {
  /** The text server, i.e. the one that answers chat. */
  status: VLLMDeploymentProgress | null;
  logs: string[];
  /** Every local server's state, keyed by modality. */
  statuses: Partial<Record<Modality, VLLMDeploymentProgress>>;
  /** Log lines per modality, so each server's startup reads on its own. */
  logsByModality: Partial<Record<Modality, string[]>>;
  /** Re-fetches status over REST, for use right after a mutating call. */
  refresh: () => Promise<void>;
  /** True while the text server is pulling, starting, or loading. */
  isBusy: boolean;
}

const VLLMStatusContext = createContext<VLLMStatusContextValue | null>(null);

export const VLLMStatusProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [statuses, setStatuses] = useState<Partial<Record<Modality, VLLMDeploymentProgress>>>({});
  const [logsByModality, setLogsByModality] = useState<Partial<Record<Modality, string[]>>>({});
  const eventSourceRef = useRef<EventSource | null>(null);

  const refresh = useCallback(async () => {
    try {
      const all = await api.getInferenceStatus();
      setStatuses(all);
      setLogsByModality((prev) => {
        const next = { ...prev };
        for (const [modality, progress] of Object.entries(all)) {
          if (progress.logs_tail?.length) {
            next[modality as Modality] = progress.logs_tail;
          }
        }
        return next;
      });
    } catch (err) {
      console.error('Failed to fetch local server status:', err);
    }
  }, []);

  useEffect(() => {
    refresh();

    const eventSource = new EventSource('/api/inference/events');
    eventSourceRef.current = eventSource;

    // An unlabelled frame is treated as the text server: that is what every frame
    // was before modalities existed, and mislabelling one as speech would hide it.
    const modalityOf = (payload: ServerEvent): Modality =>
      payload.modality ?? payload.data?.modality ?? 'text';

    const handleStatus = (event: MessageEvent) => {
      try {
        const payload: ServerEvent = JSON.parse(event.data);
        if (payload.data) {
          const modality = modalityOf(payload);
          setStatuses((prev) => ({ ...prev, [modality]: payload.data }));
        }
      } catch {
        /* a malformed frame should not tear down the stream */
      }
    };

    const handleLog = (event: MessageEvent) => {
      try {
        const payload: ServerEvent = JSON.parse(event.data);
        if (payload.line) {
          const modality = modalityOf(payload);
          setLogsByModality((prev) => ({
            ...prev,
            [modality]: [...(prev[modality] ?? []).slice(-LOG_BUFFER_LIMIT), payload.line!],
          }));
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

  const status = statuses.text ?? null;
  const logs = logsByModality.text ?? [];
  const isBusy = Boolean(status && LOADING_STATES.includes(status.state));

  return (
    <VLLMStatusContext.Provider
      value={{ status, logs, statuses, logsByModality, refresh, isBusy }}
    >
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
