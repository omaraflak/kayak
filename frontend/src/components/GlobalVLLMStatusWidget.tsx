import React, { useState, useEffect } from 'react';
import { VLLMDeploymentProgress } from '../types';
import { api } from '../api/client';
import { VLLMDeploymentModal } from './VLLMDeploymentModal';
import { 
  Rocket, 
  Loader2, 
  CheckCircle2, 
  AlertCircle, 
  ChevronRight, 
  X,
  Maximize2
} from 'lucide-react';

export const GlobalVLLMStatusWidget: React.FC = () => {
  const [status, setStatus] = useState<VLLMDeploymentProgress | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isDismissed, setIsDismissed] = useState(false);

  const fetchStatus = async () => {
    try {
      const data = await api.getVLLMStatus();
      setStatus(data);
    } catch (err) {
      console.error('Failed to fetch global vLLM status:', err);
    }
  };

  useEffect(() => {
    fetchStatus();

    // SSE connection for persistent real-time status updates across the entire app
    const eventSource = new EventSource('/api/vllm/events');

    eventSource.addEventListener('status', (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data);
        if (payload.data) {
          setStatus(payload.data);
          // If a new deployment starts, ensure it is not dismissed
          if (['pulling_image', 'downloading_model', 'initializing_weights', 'starting_container'].includes(payload.data.state)) {
            setIsDismissed(false);
          }
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
  }, []);

  if (!status || isDismissed) return (
    <VLLMDeploymentModal
      isOpen={isModalOpen}
      onClose={() => {
        setIsModalOpen(false);
        fetchStatus();
      }}
      targetModelId={status?.model_id}
    />
  );

  const isDownloading = status.state === 'downloading_model';
  const isInitializing = status.state === 'initializing_weights';
  const isPulling = status.state === 'pulling_image';
  const isStarting = status.state === 'starting_container';
  const isBusy = isDownloading || isInitializing || isPulling || isStarting;
  const isReady = status.state === 'ready';
  const isError = status.state === 'error';

  // Do not show floating bar if idle or stopped
  if (status.state === 'idle' || status.state === 'stopped') {
    return (
      <VLLMDeploymentModal
        isOpen={isModalOpen}
        onClose={() => {
          setIsModalOpen(false);
          fetchStatus();
        }}
        targetModelId={status?.model_id}
      />
    );
  }

  const modelShortName = status.model_id ? (status.model_id.split('/').pop() || status.model_id) : 'vLLM Server';
  const progressPercent = status.progress_percent ?? (isReady ? 100 : isInitializing ? 90 : 25);

  return (
    <>
      <aside 
        aria-label="vLLM server deployment status"
        className="fixed bottom-5 right-5 z-40 animate-slide-up font-sans"
      >
        <div className="bg-zinc-950/95 text-white border border-zinc-700/80 rounded-2xl shadow-2xl p-3.5 backdrop-blur-md flex items-center space-x-3.5 max-w-md min-w-[320px]">
          {/* Status Icon */}
          <div 
            role="button"
            tabIndex={0}
            onClick={() => setIsModalOpen(true)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setIsModalOpen(true); }}
            className="cursor-pointer"
          >
            {isBusy && (
              <div className="w-9 h-9 rounded-xl bg-indigo-600/30 border border-indigo-500/40 flex items-center justify-center text-indigo-400 shrink-0">
                <Loader2 className="w-5 h-5 animate-spin" />
              </div>
            )}
            {isReady && (
              <div className="w-9 h-9 rounded-xl bg-emerald-600/30 border border-emerald-500/40 flex items-center justify-center text-emerald-400 shrink-0">
                <CheckCircle2 className="w-5 h-5" />
              </div>
            )}
            {isError && (
              <div className="w-9 h-9 rounded-xl bg-rose-600/30 border border-rose-500/40 flex items-center justify-center text-rose-400 shrink-0">
                <AlertCircle className="w-5 h-5" />
              </div>
            )}
          </div>

          {/* Progress & Status Message */}
          <div 
            role="button"
            tabIndex={0}
            onClick={() => setIsModalOpen(true)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setIsModalOpen(true); }}
            className="flex-1 min-w-0 cursor-pointer space-y-1"
          >
            <div className="flex items-center justify-between text-xs">
              <span className="font-bold text-zinc-100 truncate pr-2">
                {isBusy ? `Deploying ${modelShortName}` : isReady ? `vLLM: ${modelShortName}` : 'vLLM Error'}
              </span>
              <span className="font-mono text-[11px] text-zinc-400 font-semibold shrink-0">
                {isReady ? 'Port 8000' : `${progressPercent.toFixed(0)}%`}
              </span>
            </div>

            {/* Mini Progress Bar */}
            <div className="w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden">
              <div
                className={`h-full transition-all duration-300 ${
                  isError
                    ? 'bg-rose-500'
                    : isReady
                    ? 'bg-emerald-500'
                    : 'bg-indigo-500'
                }`}
                style={{ width: `${Math.max(5, Math.min(100, progressPercent))}%` }}
              />
            </div>

            <p className="text-[10.5px] text-zinc-400 truncate">
              {status.message}
            </p>
          </div>

          {/* Action Buttons */}
          <div className="flex items-center space-x-1 shrink-0">
            <button
              type="button"
              onClick={() => setIsModalOpen(true)}
              className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
              title="Open full logs & controls"
            >
              <Maximize2 className="w-4 h-4" />
            </button>

            {isReady && (
              <button
                type="button"
                onClick={() => setIsDismissed(true)}
                className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-zinc-800 transition-colors"
                title="Dismiss pill"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>
      </aside>

      {/* Full Deployment and Terminal Logs Modal */}
      <VLLMDeploymentModal
        isOpen={isModalOpen}
        onClose={() => {
          setIsModalOpen(false);
          fetchStatus();
        }}
        targetModelId={status.model_id}
      />
    </>
  );
};
