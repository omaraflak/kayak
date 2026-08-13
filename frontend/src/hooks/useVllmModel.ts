import { useCallback, useState } from 'react';
import { api } from '../api/client';
import { useVLLMStatus, VLLM_LOADING_STATES } from '../context/VLLMStatusContext';

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
  isOffline: boolean;
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

export function useVllmModel(agentModel: string | undefined | null): VllmModelState {
  const { status, refresh } = useVLLMStatus();
  const [isStarting, setIsStarting] = useState(false);

  const modelId = parseVllmModelId(agentModel);
  const isLocal = modelId !== null;

  const isReady = !isLocal || (status?.state === 'ready' && status?.model_id === modelId);

  // A server that has just been asked to start, or that is provisioning before it has
  // reported which model it holds, counts as loading for this agent -- otherwise the
  // UI offers a "Start Model" button for a start that is already under way.
  const isLoading =
    isLocal &&
    !isReady &&
    (isStarting ||
      (status !== null &&
        VLLM_LOADING_STATES.includes(status.state) &&
        (status.model_id === modelId || !status.model_id)));

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
    statusMessage: isLoading ? status?.message ?? null : null,
    start,
  };
}
