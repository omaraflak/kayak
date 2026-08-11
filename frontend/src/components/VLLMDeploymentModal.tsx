import React, { useState, useEffect, useRef } from 'react';
import { VLLMDeploymentProgress } from '../types';
import { api } from '../api/client';
import { 
  Rocket, 
  CheckCircle2, 
  AlertCircle, 
  Loader2, 
  Terminal, 
  Square, 
  X, 
  ChevronDown, 
  ChevronUp,
  Cpu,
  Layers
} from 'lucide-react';

interface VLLMDeploymentModalProps {
  isOpen: boolean;
  onClose: () => void;
  targetModelId?: string | null;
  onModelReady?: (modelString: string) => void;
  autoDeploy?: boolean;
}

export const VLLMDeploymentModal: React.FC<VLLMDeploymentModalProps> = ({
  isOpen,
  onClose,
  targetModelId,
  onModelReady,
  autoDeploy = false,
}) => {
  const [status, setStatus] = useState<VLLMDeploymentProgress | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [showLogs, setShowLogs] = useState<boolean>(true);
  const [isDeploying, setIsDeploying] = useState<boolean>(false);
  const logsEndRef = useRef<HTMLDivElement>(null);

  const fetchStatus = async () => {
    try {
      const data = await api.getVLLMStatus();
      setStatus(data);
      if (data.logs_tail) {
        setLogs(data.logs_tail);
      }
    } catch (err) {
      console.error('Failed to fetch vLLM status:', err);
    }
  };

  useEffect(() => {
    if (!isOpen) return;

    fetchStatus();

    // Connect to live SSE stream for real-time logs and download progress
    const eventSource = new EventSource('/api/vllm/events');

    eventSource.addEventListener('status', (e: MessageEvent) => {
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
          setLogs((prev) => [...prev.slice(-300), payload.line]);
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

    return () => {
      eventSource.close();
    };
  }, [isOpen]);

  useEffect(() => {
    if (showLogs) {
      logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs, showLogs]);

  // Auto-deploy only when explicitly requested (e.g. initiating new model deployment)
  useEffect(() => {
    if (isOpen && autoDeploy && targetModelId) {
      handleDeploy(targetModelId);
    }
  }, [isOpen, autoDeploy, targetModelId]);

  const handleDeploy = async (modelId: string) => {
    setIsDeploying(true);
    try {
      const data = await api.deployVLLMModel({
        model_id: modelId,
        gpu_memory_utilization: 0.90,
        trust_remote_code: true,
      });
      setStatus(data);
    } catch (err) {
      console.error('Deployment request failed:', err);
    } finally {
      setIsDeploying(false);
    }
  };

  const handleStop = async () => {
    try {
      await api.stopVLLMServer();
      await fetchStatus();
    } catch (err) {
      console.error('Failed to stop vLLM server:', err);
    }
  };

  const [elapsedSeconds, setElapsedSeconds] = useState<number>(0);
  const startTimeRef = useRef<number | null>(null);

  const isReady = status?.state === 'ready';
  const isError = status?.state === 'error';
  const isLoading = status?.state === 'loading';
  const isPulling = status?.state === 'pulling_image';
  const isStarting = status?.state === 'starting_container';
  const isBusy = isLoading || isPulling || isStarting || isDeploying;

  // Continuous client-side stopwatch for provisioning duration
  useEffect(() => {
    if (isBusy) {
      if (!startTimeRef.current) {
        startTimeRef.current = Date.now();
      }
      const interval = setInterval(() => {
        if (startTimeRef.current) {
          setElapsedSeconds(Math.floor((Date.now() - startTimeRef.current) / 1000));
        }
      }, 1000);
      return () => clearInterval(interval);
    } else {
      startTimeRef.current = null;
      setElapsedSeconds(0);
    }
  }, [isBusy]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-zinc-950/60 backdrop-blur-xs animate-fade-in font-sans">
      <div className="bg-white rounded-2xl border border-zinc-200 shadow-2xl w-full max-w-2xl overflow-hidden flex flex-col max-h-[85vh]">
        {/* Header */}
        <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between bg-zinc-50/70 shrink-0">
          <div className="flex items-center space-x-2.5">
            <div className="w-8 h-8 rounded-xl bg-indigo-50 border border-indigo-200 flex items-center justify-center text-indigo-600 shadow-2xs">
              <Rocket className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-zinc-900 flex items-center gap-2">
                <span>Local vLLM Container Orchestration</span>
                {isReady && (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded-full">
                    <CheckCircle2 className="w-3 h-3" /> Live & Serving
                  </span>
                )}
                {isBusy && (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold text-indigo-700 bg-indigo-50 border border-indigo-200 px-2 py-0.5 rounded-full">
                    <Loader2 className="w-3 h-3 animate-spin" /> Provisioning
                  </span>
                )}
                {isError && (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold text-rose-700 bg-rose-50 border border-rose-200 px-2 py-0.5 rounded-full">
                    <AlertCircle className="w-3 h-3" /> Error
                  </span>
                )}
              </h2>
              <p className="text-[11px] text-zinc-500">
                Docker Sandbox · High-Throughput PagedAttention · OpenAI-Compatible Endpoint
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-700 hover:bg-zinc-200/60 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Content Area */}
        <div className="p-6 space-y-5 overflow-y-auto flex-1">
          {/* Target Model Card */}
          <div className="bg-zinc-50 border border-zinc-200 rounded-xl p-4 flex items-center justify-between">
            <div className="space-y-1">
              <div className="text-xs font-bold text-zinc-900 flex items-center gap-1.5">
                <Layers className="w-4 h-4 text-indigo-600" />
                <span className="font-mono text-zinc-900">{status?.model_id || targetModelId || 'Select a model to deploy'}</span>
              </div>
              <div className="text-[11px] text-zinc-500 flex items-center gap-2">
                <span>Port: <code className="font-mono text-zinc-700 font-semibold">{status?.port || 8000}</code></span>
                <span>·</span>
                <span>Endpoint: <code className="font-mono text-zinc-700">{status?.endpoint || 'http://localhost:8000/v1'}</code></span>
              </div>
            </div>
          </div>

          {/* Status Message */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="font-medium text-zinc-800 flex items-center gap-1.5">
                {isBusy && <Loader2 className="w-3.5 h-3.5 animate-spin text-indigo-600" />}
                {isReady
                  ? (status?.message || 'vLLM server is healthy and ready!')
                  : isError
                  ? (status?.message || 'vLLM deployment encountered an error')
                  : isBusy
                  ? `${status?.message || 'Provisioning vLLM container...'} (${elapsedSeconds}s)`
                  : 'vLLM server is idle'}
              </span>
            </div>

            {status?.error && (
              <div className="p-3 rounded-xl bg-rose-50 border border-rose-200 text-rose-800 text-xs font-medium">
                {status.error}
              </div>
            )}
          </div>

          {/* Live Streaming Logs Viewer */}
          <div className="border border-zinc-200 rounded-xl overflow-hidden shadow-2xs">
            <button
              type="button"
              onClick={() => setShowLogs(!showLogs)}
              className="w-full px-3.5 py-2 bg-zinc-100/80 border-b border-zinc-200 flex items-center justify-between text-xs font-semibold text-zinc-700 hover:bg-zinc-100 transition-colors"
            >
              <div className="flex items-center gap-1.5">
                <Terminal className="w-3.5 h-3.5 text-zinc-600" />
                <span>vLLM Server Logs ({logs.length} lines)</span>
              </div>
              {showLogs ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            </button>

            {showLogs && (
              <div className="p-3 bg-zinc-950 text-zinc-200 font-mono text-[11px] h-48 overflow-y-auto space-y-0.5 leading-relaxed selection:bg-indigo-600">
                {logs.length === 0 ? (
                  <div className="text-zinc-500 italic">Waiting for container log output...</div>
                ) : (
                  logs.map((line, idx) => (
                    <div key={idx} className="break-all whitespace-pre-wrap">
                      {line}
                    </div>
                  ))
                )}
                <div ref={logsEndRef} />
              </div>
            )}
          </div>
        </div>

        {/* Footer Actions */}
        <div className="px-6 py-3.5 border-t border-zinc-200 bg-zinc-50 flex items-center justify-between shrink-0">
          <div className="text-xs text-zinc-500">
            Model cache: <code className="font-mono text-zinc-700">data/huggingface_cache/</code>
          </div>

          <div className="flex items-center space-x-2">
            {isBusy && (
              <button
                type="button"
                onClick={handleStop}
                className="px-3.5 py-1.5 rounded-lg border border-rose-300 text-rose-700 hover:bg-rose-50 text-xs font-semibold flex items-center gap-1.5 transition-colors"
              >
                <Square className="w-3 h-3 fill-rose-700" />
                <span>Cancel</span>
              </button>
            )}

            {isReady ? (
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold shadow-xs transition-colors"
              >
                Done
              </button>
            ) : (
              !isBusy && (
                <button
                  type="button"
                  onClick={onClose}
                  className="px-4 py-1.5 rounded-lg bg-zinc-200 hover:bg-zinc-300 text-zinc-800 text-xs font-semibold transition-colors"
                >
                  Close
                </button>
              )
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
