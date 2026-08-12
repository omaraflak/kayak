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
    <div className="flex-1 flex flex-col h-full min-h-0 bg-zinc-50 dark:bg-zinc-950 overflow-hidden font-sans transition-colors">
      {/* Top Header */}
      <div className="h-16 border-b border-zinc-200 dark:border-zinc-800 px-8 flex items-center justify-between bg-white dark:bg-zinc-900 shrink-0 transition-colors">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded-xl bg-indigo-50 dark:bg-indigo-950/60 border border-indigo-200 dark:border-indigo-800/60 flex items-center justify-center text-indigo-600 dark:text-indigo-400 shadow-2xs">
            <Cpu className="w-4 h-4" />
          </div>
          <div>
            <h2 className="font-bold text-sm text-zinc-900 dark:text-zinc-100">Local Models & vLLM Orchestration</h2>
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              Manage locally running inference containers, endpoints, and Hugging Face weights
            </p>
          </div>
        </div>

        <div className="flex items-center space-x-2">
          <button
            type="button"
            onClick={fetchStatus}
            className="p-2 rounded-xl border border-zinc-200 dark:border-zinc-800 text-zinc-600 dark:text-zinc-300 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-50 dark:hover:bg-zinc-800 transition-colors shadow-2xs cursor-pointer"
            title="Refresh status"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6">
        {/* Active Container Status Card with Inline Logs & Telemetry */}
        <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded-2xl p-6 shadow-xs space-y-4 transition-colors">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <div className={`w-10 h-10 rounded-2xl flex items-center justify-center border shadow-xs ${
                isReady 
                  ? 'bg-emerald-50 dark:bg-emerald-950/80 border-emerald-200 dark:border-emerald-800/80 text-emerald-600 dark:text-emerald-400'
                  : isLoading
                  ? 'bg-amber-50 dark:bg-amber-950/80 border-amber-200 dark:border-amber-800/80 text-amber-600 dark:text-amber-400'
                  : isError
                  ? 'bg-rose-50 dark:bg-rose-950/80 border-rose-200 dark:border-rose-800/80 text-rose-600 dark:text-rose-400'
                  : 'bg-zinc-100 dark:bg-zinc-800 border-zinc-200 dark:border-zinc-700 text-zinc-500 dark:text-zinc-400'
              }`}>
                {isLoading ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : isReady ? (
                  <CheckCircle2 className="w-5 h-5" />
                ) : isError ? (
                  <AlertCircle className="w-5 h-5" />
                ) : (
                  <Server className="w-5 h-5" />
                )}
              </div>

              <div>
                <div className="flex items-center space-x-2">
                  <h3 className="font-bold text-sm text-zinc-900 dark:text-zinc-100">
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
                      ? 'bg-emerald-50 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-200 border-emerald-200 dark:border-emerald-800/80'
                      : isLoading
                      ? 'bg-amber-50 dark:bg-amber-950/80 text-amber-800 dark:text-amber-200 border-amber-200 dark:border-amber-800/80'
                      : isError
                      ? 'bg-rose-50 dark:bg-rose-950/80 text-rose-800 dark:text-rose-200 border-rose-200 dark:border-rose-800/80'
                      : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 border-zinc-200 dark:border-zinc-700'
                  }`}>
                    {isReady ? 'ONLINE' : isLoading ? 'STARTING' : isError ? 'ERROR' : 'STOPPED'}
                  </span>
                </div>
                <p className="text-xs text-zinc-600 dark:text-zinc-300 mt-0.5">
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
                  className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl bg-rose-600 hover:bg-rose-700 disabled:opacity-50 text-white text-xs font-semibold shadow-xs transition-colors cursor-pointer"
                >
                  {isStopping ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Square className="w-3.5 h-3.5 fill-white" />}
                  <span>Stop Server</span>
                </button>
              )}
            </div>
          </div>

          {/* Details Bar */}
          {isReady && (
            <div className="pt-3 border-t border-zinc-200 dark:border-zinc-800 grid grid-cols-3 gap-4 text-xs font-mono">
              <div className="bg-zinc-50 dark:bg-zinc-800/80 p-2.5 rounded-xl border border-zinc-200 dark:border-zinc-700">
                <span className="text-[10px] text-zinc-500 dark:text-zinc-400 uppercase font-sans font-bold block">OpenAI Endpoint</span>
                <span className="text-zinc-900 dark:text-zinc-100 font-semibold">{status?.endpoint || 'http://localhost:8001/v1'}</span>
              </div>
              <div className="bg-zinc-50 dark:bg-zinc-800/80 p-2.5 rounded-xl border border-zinc-200 dark:border-zinc-700">
                <span className="text-[10px] text-zinc-500 dark:text-zinc-400 uppercase font-sans font-bold block">Host Port</span>
                <span className="text-zinc-900 dark:text-zinc-100 font-semibold">{status?.port || 8001}</span>
              </div>
              <div className="bg-zinc-50 dark:bg-zinc-800/80 p-2.5 rounded-xl border border-zinc-200 dark:border-zinc-700">
                <span className="text-[10px] text-zinc-500 dark:text-zinc-400 uppercase font-sans font-bold block">Model Tag</span>
                <span className="text-zinc-900 dark:text-zinc-100 truncate block font-semibold">{status?.model_id}</span>
              </div>
            </div>
          )}

          {/* Embedded Live Server Logs Box inside the Card */}
          {(isReady || isLoading || logs.length > 0) && (
            <div className="pt-2 border-t border-zinc-100 dark:border-zinc-800 space-y-2">
              <div>
                <button
                  type="button"
                  onClick={() => setShowLogs(!showLogs)}
                  className="inline-flex items-center gap-2 px-3 py-1.5 rounded-xl border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800 hover:bg-zinc-100 dark:hover:bg-zinc-750 text-xs font-semibold text-zinc-700 dark:text-zinc-300 transition-colors cursor-pointer shadow-2xs"
                >
                  <Terminal className="w-3.5 h-3.5 text-zinc-500 dark:text-zinc-400" />
                  <span>Container Logs ({logs.length} lines)</span>
                  {showLogs ? <ChevronUp className="w-3.5 h-3.5 text-zinc-400" /> : <ChevronDown className="w-3.5 h-3.5 text-zinc-400" />}
                </button>
              </div>

              {showLogs && (
                <div ref={logContainerRef} className="p-3.5 bg-zinc-950 text-zinc-200 font-mono text-[11px] rounded-xl overflow-y-auto space-y-0.5 leading-relaxed selection:bg-indigo-600 h-80 max-h-96 border border-zinc-800">
                  {logs.length === 0 ? (
                    <div className="text-zinc-500 italic">Waiting for container log output...</div>
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
            <h4 className="text-xs font-bold uppercase tracking-wider text-zinc-700 dark:text-zinc-300 flex items-center gap-1.5">
              <Layers className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" /> Hugging Face Hub Model Catalog
            </h4>
            <span className="text-[11px] text-zinc-400 dark:text-zinc-500 font-mono">
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
