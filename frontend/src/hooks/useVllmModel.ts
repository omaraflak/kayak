import { useCallback, useState } from 'react';
import { api } from '../api/client';
import { isLoadingState, useVLLMStatus } from '../context/VLLMStatusContext';
import { VLLMDeploymentProgress } from '../types';

/**
 * Whether the local model an agent needs is actually serving.
 *
 * The draft screen and the composer both gate sending on this, and each used to derive
 * it separately. The copies had already drifted -- only one of them counted a
 * just-issued start, or a loading server that had not yet reported which model it was
 * loading, as "loading" -- so the two surfaces could disagree about whether the same
 * agent was ready.
 */

export interface VllmModelState {
  /** The Hugging Face repository this agent needs, or null for a cloud model. */
  modelId: string | null;
  /** True when the agent runs on a locally served model. */
  isLocal: boolean;
  /** True for a cloud agent, or when the local server is serving this model. */
  isReady: boolean;
  isLoading: boolean;
  /**
   * True when this model is not running and not starting -- including when its
   * last start failed, so a Start button offered for "offline" is offered then too.
   */
  isOffline: boolean;
  /** True when this model's last start failed, or it stopped unexpectedly. */
  isError: boolean;
  /**
   * The server's one-line headline for that failure, such as "Qwen3-4B couldn't
   * start." or "Qwen3-4B stopped unexpectedly.". Null unless `isError`.
   */
  errorMessage: string | null;
  /**
   * True while the live connection to Kayak is down, so the state above is what
   * was last heard rather than confirmed now.
   */
  isStale: boolean;
  /** Message from the server while it provisions, for surfacing progress. */
  statusMessage: string | null;
  start: () => Promise<void>;
}

/** Extracts the repository id from an agent model string like `vllm/Org/Model`. */
export function parseVllmModelId(model: string | undefined | null): string | null {
  if (!model || !model.startsWith('vllm/')) return null;
  const id = model.slice('vllm/'.length);
  return id || null;
}

/**
 * Where the text server stands for one local model.
 *
 * A server provisioning before it has reported which model it holds counts as
 * loading this one -- otherwise the UI offers a "Start Model" button for a start
 * that is already under way. A failure counts only when it names this model: a
 * different model failing says nothing about this one.
 */
export function localModelState(
  status: VLLMDeploymentProgress | null | undefined,
  modelId: string
): 'ready' | 'loading' | 'error' | 'offline' {
  if (status?.state === 'ready' && status.model_id === modelId) return 'ready';
  if (isLoadingState(status?.state) && (!status?.model_id || status.model_id === modelId)) {
    return 'loading';
  }
  if (status?.state === 'error' && status.model_id === modelId) return 'error';
  return 'offline';
}

export function useVllmModel(agentModel: string | undefined | null): VllmModelState {
  const { status, isStale, refresh } = useVLLMStatus();
  const [isStarting, setIsStarting] = useState(false);

  const modelId = parseVllmModelId(agentModel);
  const isLocal = modelId !== null;
  const state = modelId ? localModelState(status, modelId) : 'ready';

  const isReady = state === 'ready';
  // A start this tab has just asked for counts as loading before the server says so.
  const isLoading = isLocal && !isReady && (isStarting || state === 'loading');
  const isError = isLocal && !isLoading && state === 'error';

  const start = useCallback(async () => {
    if (!modelId) return;
    setIsStarting(true);
    try {
      await api.deployVLLMModel({ model_id: modelId });
      await refresh();
    } finally {
      setIsStarting(false);
    }
  }, [modelId, refresh]);

  return {
    modelId,
    isLocal,
    isReady,
    isLoading,
    isOffline: isLocal && !isReady && !isLoading,
    isError,
    errorMessage: isError ? status?.message ?? null : null,
    isStale: isLocal && isStale,
    statusMessage: isLoading ? status?.message ?? null : null,
    start,
  };
}
