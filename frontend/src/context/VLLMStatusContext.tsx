import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { Modality, VLLMDeploymentProgress } from '../types';
import { api } from '../api/client';
import { shouldResetLogs } from '../features/models/serverStatus';

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

/**
 * How long the stream may be down before what it last said stops being trusted.
 *
 * Long enough to ride out the browser's own quick reconnect; short enough that a
 * model is not shown as running for long after Kayak itself stopped answering.
 */
const STALE_AFTER_MS = 10_000;

/** How long to wait before reopening a stream the browser gave up on. */
const RECONNECT_DELAY_MS = 3_000;

/** States in which a server is coming up rather than serving. */
const LOADING_STATES: readonly string[] = ['pulling_image', 'starting_container', 'loading'];

/** Whether a server in this state is mid-deployment. */
export function isLoadingState(state: string | null | undefined): boolean {
  return !!state && LOADING_STATES.includes(state);
}

/**
 * The model a server is actually holding -- serving or coming up -- or null.
 *
 * After a stop or a failure the status still names the last model, and treating
 * that as active marked a dead server's model as running.
 */
export function activeModelOf(progress: VLLMDeploymentProgress | null | undefined): string | null {
  if (!progress) return null;
  return progress.state === 'ready' || isLoadingState(progress.state)
    ? progress.model_id ?? null
    : null;
}

/** The frame shape the server sends; `modality` is stamped on every event. */
interface ServerEvent {
  modality?: Modality;
  data?: VLLMDeploymentProgress;
  line?: string;
}

interface VLLMStatusContextValue {
  /** The text server, i.e. the one that answers chat. */
  status: VLLMDeploymentProgress | null;
  /** Every local server's state, keyed by modality. */
  statuses: Partial<Record<Modality, VLLMDeploymentProgress>>;
  /** Log lines per modality, so each server's startup reads on its own. */
  logsByModality: Partial<Record<Modality, string[]>>;
  /**
   * True while the live connection to Kayak has been down for a while, so the
   * statuses above are what was last heard rather than what is true now. Surfaces
   * show them as unconfirmed ("Checking…") instead of claiming a model is online.
   */
  isStale: boolean;
  /** Re-fetches status over REST, for use right after a mutating call. */
  refresh: () => Promise<void>;
}

const VLLMStatusContext = createContext<VLLMStatusContextValue | null>(null);

export const VLLMStatusProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [statuses, setStatuses] = useState<Partial<Record<Modality, VLLMDeploymentProgress>>>({});
  const [logsByModality, setLogsByModality] = useState<Partial<Record<Modality, string[]>>>({});
  const [isStale, setIsStale] = useState(false);
  // The latest status per modality, read synchronously when the next one arrives:
  // whether a new start began depends on what the server was doing before it.
  const statusesRef = useRef<Partial<Record<Modality, VLLMDeploymentProgress>>>({});

  /** Takes one server's new status, starting its log afresh when a new start begins. */
  const applyStatus = useCallback((modality: Modality, next: VLLMDeploymentProgress) => {
    const previous = statusesRef.current[modality];
    statusesRef.current = { ...statusesRef.current, [modality]: next };
    setStatuses((current) => ({ ...current, [modality]: next }));
    if (shouldResetLogs(previous, next)) {
      setLogsByModality((current) => ({ ...current, [modality]: next.logs_tail ?? [] }));
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const all = await api.getInferenceStatus();
      for (const [modality, progress] of Object.entries(all)) {
        applyStatus(modality as Modality, progress);
      }
      // The stream fills a log as lines arrive; a tab opened mid-start has only
      // the server's recent tail to begin from.
      setLogsByModality((previous) => {
        const next = { ...previous };
        for (const [modality, progress] of Object.entries(all)) {
          const key = modality as Modality;
          if (!next[key]?.length && progress.logs_tail?.length) next[key] = progress.logs_tail;
        }
        return next;
      });
    } catch (err) {
      console.error('Failed to fetch local server status:', err);
    }
  }, [applyStatus]);

  useEffect(() => {
    refresh();

    let eventSource: EventSource | null = null;
    let staleTimer: number | undefined;
    let reconnectTimer: number | undefined;
    let closed = false;

    // An unlabelled frame is treated as the text server: that is what every frame
    // was before modalities existed, and mislabelling one as speech would hide it.
    const modalityOf = (payload: ServerEvent): Modality =>
      payload.modality ?? payload.data?.modality ?? 'text';

    const handleStatus = (event: MessageEvent) => {
      try {
        const payload: ServerEvent = JSON.parse(event.data);
        if (payload.data) applyStatus(modalityOf(payload), payload.data);
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

    const connect = () => {
      if (closed) return;
      const source = new EventSource('/api/inference/events');
      eventSource = source;
      source.addEventListener('status', handleStatus);
      source.addEventListener('update', handleStatus);
      source.addEventListener('log', handleLog);

      // Re-sync on every (re)connect. The browser quietly reconnects after the
      // server restarts, and any state change the fresh server broadcast before
      // this listener attached is otherwise lost -- leaving the page describing
      // the old process.
      source.onopen = () => {
        window.clearTimeout(staleTimer);
        staleTimer = undefined;
        setIsStale(false);
        refresh();
      };

      // The browser retries a dropped stream on its own, but nothing said so: the
      // page kept claiming a model was online while Kayak could not be reached.
      // After a grace period the statuses are marked unconfirmed, and a stream the
      // browser has given up on entirely is reopened by hand.
      source.onerror = () => {
        if (staleTimer === undefined) {
          staleTimer = window.setTimeout(() => setIsStale(true), STALE_AFTER_MS);
        }
        if (source.readyState === EventSource.CLOSED) {
          source.close();
          window.clearTimeout(reconnectTimer);
          reconnectTimer = window.setTimeout(connect, RECONNECT_DELAY_MS);
        }
      };
    };

    connect();

    return () => {
      closed = true;
      window.clearTimeout(staleTimer);
      window.clearTimeout(reconnectTimer);
      eventSource?.close();
    };
  }, [refresh, applyStatus]);

  const status = statuses.text ?? null;

  return (
    <VLLMStatusContext.Provider value={{ status, statuses, logsByModality, isStale, refresh }}>
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
