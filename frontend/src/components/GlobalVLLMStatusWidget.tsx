import React, { useState, useEffect, useRef } from 'react';
import { useVLLMStatus } from '../context/VLLMStatusContext';
import { VLLMDeploymentModal } from './VLLMDeploymentModal';
import { READY_TOAST_TIMEOUT_MS, shouldShowVllmToast } from './vllmToast';
import { 
  Rocket, 
  Loader2, 
  CheckCircle2, 
  AlertCircle, 
  X,
  Maximize2
} from 'lucide-react';

export const GlobalVLLMStatusWidget: React.FC = () => {
  const { status, isBusy: isDeploymentBusy } = useVLLMStatus();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isDismissed, setIsDismissed] = useState(false);
  const [readyExpired, setReadyExpired] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState<number>(0);
  const startTimeRef = useRef<number | null>(null);
  // Whether this tab watched the deployment happen. A tab that opens onto an
  // already-serving model has nothing to announce.
  const sawDeploymentRef = useRef(false);

  // Re-surface the widget whenever a new deployment starts, even if the user
  // dismissed the previous one.
  useEffect(() => {
    if (isDeploymentBusy) {
      sawDeploymentRef.current = true;
      setIsDismissed(false);
      setReadyExpired(false);
    }
  }, [isDeploymentBusy]);

  const isLoading = status?.state === 'loading';
  const isPulling = status?.state === 'pulling_image';
  const isStarting = status?.state === 'starting_container';
  const isBusy = isLoading || isPulling || isStarting;
  const isReady = status?.state === 'ready';
  const isError = status?.state === 'error';

  // The ready confirmation retires itself instead of living in the corner forever.
  useEffect(() => {
    if (!isReady || !sawDeploymentRef.current) return;
    const timer = setTimeout(() => setReadyExpired(true), READY_TOAST_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [isReady]);

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

  const showFloatingBar = shouldShowVllmToast({
    state: status?.state,
    dismissed: isDismissed,
    sawDeployment: sawDeploymentRef.current,
    readyExpired,
  });
  const modelShortName = status?.model_id ? (status.model_id.split('/').pop() || status.model_id) : 'vLLM Server';

  return (
    <>
      {showFloatingBar && (
        <aside 
          aria-label="vLLM server deployment status"
          className="fixed bottom-5 right-5 z-40 animate-slide-up font-sans"
        >
          <div className="bg-md-inverse-surface text-md-inverse-on-surface border border-md-outline-variant rounded-2xl shadow-2xl p-3.5 backdrop-blur-md flex items-center space-x-3.5 max-w-md min-w-[320px]">
            {/* Status Icon */}
            <div 
              role="button"
              tabIndex={0}
              onClick={() => setIsModalOpen(true)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setIsModalOpen(true); }}
              className="cursor-pointer"
            >
              {isBusy && (
                <div className="w-9 h-9 rounded-xl bg-md-primary/20 border border-md-primary/40 flex items-center justify-center text-md-primary shrink-0">
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

            {/* Status Message */}
            <div 
              role="button"
              tabIndex={0}
              onClick={() => setIsModalOpen(true)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setIsModalOpen(true); }}
              className="flex-1 min-w-0 cursor-pointer space-y-0.5"
            >
              <div className="flex items-center justify-between text-xs">
                <span className="font-bold text-md-inverse-on-surface truncate pr-2">
                  {isBusy ? `Deploying ${modelShortName}` : isReady ? `vLLM: ${modelShortName}` : 'vLLM Error'}
                </span>
                <span className="font-mono text-[11px] text-md-inverse-on-surface/70 font-semibold shrink-0">
                  {isReady ? `Port ${status?.port || 8001}` : ''}
                </span>
              </div>

              <p className="text-[10.5px] text-md-inverse-on-surface/70 truncate">
                {isBusy ? `${status?.message || 'Deploying...'} (${elapsedSeconds}s)` : status?.message}
              </p>
            </div>

            {/* Action Buttons */}
            <div className="flex items-center space-x-1 shrink-0">
              <button
                type="button"
                onClick={() => setIsModalOpen(true)}
                className="p-1.5 rounded-lg text-md-inverse-on-surface/60 hover:text-md-inverse-on-surface hover:bg-white/10 transition-colors"
                title="Open full logs & controls"
              >
                <Maximize2 className="w-4 h-4" />
              </button>

              {isReady && (
                <button
                  type="button"
                  onClick={() => setIsDismissed(true)}
                  className="p-1.5 rounded-lg text-md-inverse-on-surface/60 hover:text-md-inverse-on-surface hover:bg-white/10 transition-colors"
                  title="Dismiss pill"
                >
                  <X className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>
        </aside>
      )}

      {/* Full Deployment and Terminal Logs Modal */}
      <VLLMDeploymentModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        targetModelId={status?.model_id}
      />
    </>
  );
};
