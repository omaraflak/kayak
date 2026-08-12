import React, { useState, useEffect, useCallback, useRef } from 'react';
import { 
  Server, 
  Cpu, 
  Square, 
  Terminal, 
  CheckCircle2, 
  AlertCircle, 
  Loader2, 
  RefreshCw, 
  Layers,
  ChevronDown,
  ChevronUp
} from 'lucide-react';
import { api } from '../api/client';
import { VLLMDeploymentProgress } from '../types';
import { useDialog } from '../context/DialogContext';
import { HuggingFaceCatalog } from './HuggingFaceCatalog';

export const ModelsView: React.FC = () => {
  const dialog = useDialog();
  const [status, setStatus] = useState<VLLMDeploymentProgress | null>(null);
  const [isStopping, setIsStopping] = useState(false);
  const [isDeploying, setIsDeploying] = useState(false);
  const [showLogs, setShowLogs] = useState(false);
  const logContainerRef = useRef<HTMLDivElement>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const st = await api.getVLLMStatus();
      setStatus(st);
    } catch (err) {
      console.error('Failed to load vLLM status:', err);
    }
  }, []);

  useEffect(() => {
    fetchStatus();

    // SSE connection for live status and log updates directly in the card
    const eventSource = new EventSource('/api/vllm/events');

    eventSource.addEventListener('status', (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data);
        if (payload.data) {
          setStatus(payload.data);
        }
      } catch {}
    });

    eventSource.addEventListener('update', (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data);
        if (payload.data) {
          setStatus(payload.data);
        }
      } catch {}
    });

    eventSource.addEventListener('log', (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data);
        if (payload.line) {
          setStatus((prev) => {
            if (!prev) return prev;
            const updatedLogs = [...(prev.logs_tail || []), payload.line];
            return {
              ...prev,
              logs_tail: updatedLogs.slice(-50),
            };
          });
        }
      } catch {}
    });

    return () => {
      eventSource.close();
    };
  }, [fetchStatus]);

  useEffect(() => {
    if (showLogs && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [status?.logs_tail, showLogs]);

  const handleStartModel = async (modelId: string) => {
    setIsDeploying(true);
    try {
      const st = await api.deployVLLMModel({ model_id: modelId });
      setStatus(st);
      await fetchStatus();
    } catch (err) {
      console.error('Failed to deploy model:', err);
      dialog.alert({
        title: 'Deployment Failed',
        message: `Could not initiate deployment for ${modelId}: ${err}`,
        variant: 'danger',
      });
    } finally {
      setIsDeploying(false);
    }
  };

  const handleStopServer = async () => {
    const confirmed = await dialog.confirm({
      title: 'Stop vLLM Container?',
      message: 'Stopping the server will terminate the local container and unload models from memory. Active conversations using this local model will be interrupted.',
      confirmText: 'Stop Server',
      cancelText: 'Keep Running',
      variant: 'danger',
    });

    if (confirmed) {
      setIsStopping(true);
      try {
        await api.stopVLLMServer();
        await fetchStatus();
      } catch (err) {
        console.error('Failed to stop vLLM server:', err);
      } finally {
        setIsStopping(false);
      }
    }
  };

  const isReady = status?.state === 'ready';
  const isLoading = ['pulling_image', 'starting_container', 'loading'].includes(status?.state || '') || isDeploying;
  const isError = status?.state === 'error';
  const logs = status?.logs_tail || [];

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 bg-md-surface overflow-hidden font-sans transition-colors">
      {/* Top Header */}
      <div className="h-16 border-b border-md-outline-variant px-8 flex items-center justify-between bg-md-surface-container-low shrink-0 transition-colors">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded-xl bg-md-primary-container text-md-on-primary-container border border-md-outline-variant flex items-center justify-center shadow-2xs">
            <Cpu className="w-4 h-4" />
          </div>
          <div>
            <h2 className="font-bold text-sm text-md-on-surface">Local Models & vLLM Orchestration</h2>
            <p className="text-xs text-md-on-surface-variant">
              Manage locally running inference containers, endpoints, and Hugging Face weights
            </p>
          </div>
        </div>

        <div className="flex items-center space-x-2">
          <button
            type="button"
            onClick={fetchStatus}
            className="p-2 rounded-xl border border-md-outline-variant text-md-on-surface hover:bg-md-surface-container-high transition-colors shadow-2xs cursor-pointer"
            title="Refresh status"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6 bg-md-surface">
        {/* Active Container Status Card with Inline Logs & Telemetry */}
        <div className="bg-md-surface border border-md-outline-variant rounded-2xl p-6 shadow-xs space-y-4 transition-colors">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <div className={`w-10 h-10 rounded-2xl flex items-center justify-center border shadow-xs ${
                isReady 
                  ? 'bg-emerald-100 dark:bg-emerald-950/80 border-emerald-300 dark:border-emerald-800/80 text-emerald-800 dark:text-emerald-200'
                  : isLoading
                  ? 'bg-amber-100 dark:bg-amber-950/80 border-amber-300 dark:border-amber-800/80 text-amber-800 dark:text-amber-200'
                  : isError
                  ? 'bg-rose-100 dark:bg-rose-950/80 border-rose-300 dark:border-rose-800/80 text-rose-800 dark:text-rose-200'
                  : 'bg-md-surface-container-high border-md-outline-variant text-md-on-surface-variant'
              }`}>
                {isLoading ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : isReady ? (
                  <CheckCircle2 className="w-5 h-5 stroke-[2.5]" />
                ) : isError ? (
                  <AlertCircle className="w-5 h-5" />
                ) : (
                  <Server className="w-5 h-5" />
                )}
              </div>

              <div>
                <div className="flex items-center space-x-2">
                  <h3 className="font-bold text-sm text-md-on-surface">
                    {isReady
                      ? `Serving: ${status?.model_id}`
                      : isLoading
                      ? `Provisioning: ${status?.model_id || 'vLLM Container'}`
                      : isError
                      ? 'vLLM Service Error'
                      : 'Local vLLM Server Offline'}
                  </h3>
                  <span className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                    isReady
                      ? 'bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-200 border-emerald-300 dark:border-emerald-800/80'
                      : isLoading
                      ? 'bg-amber-100 dark:bg-amber-950/80 text-amber-800 dark:text-amber-200 border-amber-300 dark:border-amber-800/80'
                      : isError
                      ? 'bg-rose-100 dark:bg-rose-950/80 text-rose-800 dark:text-rose-200 border-rose-300 dark:border-rose-800/80'
                      : 'bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant'
                  }`}>
                    {isReady ? 'ONLINE' : isLoading ? 'STARTING' : isError ? 'ERROR' : 'STOPPED'}
                  </span>
                </div>
                <p className="text-xs text-md-on-surface-variant mt-0.5">
                  {status?.message || 'No container running. Select a model below to launch.'}
                </p>
              </div>
            </div>

            {/* Actions for Active Server */}
            <div className="flex items-center space-x-2">
              {(isReady || isLoading) && (
                <button
                  type="button"
                  onClick={handleStopServer}
                  disabled={isStopping}
                  className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl bg-md-error hover:opacity-90 disabled:opacity-50 text-md-on-error text-xs font-semibold shadow-xs transition-opacity cursor-pointer"
                >
                  {isStopping ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Square className="w-3.5 h-3.5 fill-current" />}
                  <span>Stop Server</span>
                </button>
              )}
            </div>
          </div>

          {/* Details Bar */}
          {isReady && (
            <div className="pt-3 border-t border-md-outline-variant grid grid-cols-3 gap-4 text-xs font-mono">
              <div className="bg-md-surface-container-lowest p-2.5 rounded-xl border border-md-outline-variant">
                <span className="text-[10px] text-md-on-surface-variant uppercase font-sans font-bold block">OpenAI Endpoint</span>
                <span className="text-md-on-surface font-semibold">{status?.endpoint || 'http://localhost:8001/v1'}</span>
              </div>
              <div className="bg-md-surface-container-lowest p-2.5 rounded-xl border border-md-outline-variant">
                <span className="text-[10px] text-md-on-surface-variant uppercase font-sans font-bold block">Host Port</span>
                <span className="text-md-on-surface font-semibold">{status?.port || 8001}</span>
              </div>
              <div className="bg-md-surface-container-lowest p-2.5 rounded-xl border border-md-outline-variant">
                <span className="text-[10px] text-md-on-surface-variant uppercase font-sans font-bold block">Model Tag</span>
                <span className="text-md-on-surface truncate block font-semibold">{status?.model_id}</span>
              </div>
            </div>
          )}

          {/* Embedded Live Server Logs Box inside the Card */}
          {(isReady || isLoading || logs.length > 0) && (
            <div className="pt-2 border-t border-md-outline-variant space-y-2">
              <div>
                <button
                  type="button"
                  onClick={() => setShowLogs(!showLogs)}
                  className="inline-flex items-center gap-2 px-3 py-1.5 rounded-xl border border-md-outline-variant bg-md-surface-container-low hover:bg-md-surface-container-high text-xs font-semibold text-md-on-surface transition-colors cursor-pointer shadow-2xs"
                >
                  <Terminal className="w-3.5 h-3.5 text-md-on-surface-variant" />
                  <span>Container Logs ({logs.length} lines)</span>
                  {showLogs ? <ChevronUp className="w-3.5 h-3.5 text-md-on-surface-variant" /> : <ChevronDown className="w-3.5 h-3.5 text-md-on-surface-variant" />}
                </button>
              </div>

              {showLogs && (
                <div ref={logContainerRef} className="p-3.5 bg-md-surface-container-lowest text-md-on-surface font-mono text-[11px] rounded-xl overflow-y-auto space-y-0.5 leading-relaxed h-80 max-h-96 border border-md-outline-variant">
                  {logs.length === 0 ? (
                    <div className="text-md-on-surface-variant italic">Waiting for container log output...</div>
                  ) : (
                    logs.map((line, idx) => (
                      <div key={idx} className="break-all whitespace-pre-wrap">
                        {line}
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Hugging Face Hub Catalog Browser Section */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h4 className="text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
              <Layers className="w-3.5 h-3.5 text-md-primary" /> Hugging Face Hub Model Catalog
            </h4>
            <span className="text-[11px] text-md-on-surface-variant font-mono">
              Search and launch open-weights models into local vLLM container
            </span>
          </div>

          <HuggingFaceCatalog
            mode="deploy"
            onDeployVLLM={handleStartModel}
            activeVllmModelId={status?.model_id}
            isVllmLoading={isLoading}
          />
        </div>
      </div>
    </div>
  );
};
