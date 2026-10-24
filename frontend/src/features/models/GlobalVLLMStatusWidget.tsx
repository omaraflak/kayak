import React, { useState, useEffect, useRef } from 'react';
import { isLoadingState, useVLLMStatus } from '../../context/VLLMStatusContext';
import { READY_TOAST_TIMEOUT_MS, shouldShowVllmToast } from './vllmToast';
import { failureTitle, formatElapsed, shortModelName } from './serverStatus';
import { Loader2, CheckCircle2, AlertCircle, X, ArrowUpRight } from 'lucide-react';

/**
 * Narrates a text model deployment from any page.
 *
 * Its details -- logs, the error, the stop control -- live on the model's row in
 * Local Models, so the toast opens that page rather than repeating them.
 */
export const GlobalVLLMStatusWidget: React.FC<{ onOpenModels: () => void }> = ({
  onOpenModels,
}) => {
  const { status, isStale } = useVLLMStatus();
  const [isDismissed, setIsDismissed] = useState(false);
  const [readyExpired, setReadyExpired] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState<number>(0);
  // Whether this tab watched the deployment happen. A tab that opens onto an
  // already-serving model has nothing to announce.
  const sawDeploymentRef = useRef(false);

  const isBusy = isLoadingState(status?.state);
  const isReady = status?.state === 'ready';
  const isError = status?.state === 'error';

  // Re-surface the widget whenever a new deployment starts, even if the user
  // dismissed the previous one, and time it from the start.
  useEffect(() => {
    if (!isBusy) {
      setElapsedSeconds(0);
      return;
    }
    sawDeploymentRef.current = true;
    setIsDismissed(false);
    setReadyExpired(false);
    const startedAt = Date.now();
    const interval = setInterval(
      () => setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000)),
      1000
    );
    return () => clearInterval(interval);
  }, [isBusy]);

  // The ready confirmation retires itself instead of living in the corner forever.
  useEffect(() => {
    if (!isReady || !sawDeploymentRef.current) return;
    const timer = setTimeout(() => setReadyExpired(true), READY_TOAST_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [isReady]);

  const showFloatingBar = shouldShowVllmToast({
    state: status?.state,
    dismissed: isDismissed,
    sawDeployment: sawDeploymentRef.current,
    readyExpired,
  });
  if (!showFloatingBar) return null;

  const modelShortName = status?.model_id ? shortModelName(status.model_id) : 'Local model';

  return (
    <aside
      aria-label="Local model status"
      className="fixed bottom-5 right-5 z-40 animate-slide-up font-sans"
    >
      <div className="bg-md-inverse-surface text-md-inverse-on-surface border border-md-outline-variant rounded-2xl shadow-2xl p-3.5 backdrop-blur-md flex items-center space-x-3.5 max-w-md min-w-[320px]">
        <button
          type="button"
          onClick={onOpenModels}
          title="Open Local Models"
          className="flex items-center gap-3.5 flex-1 min-w-0 text-left cursor-pointer"
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

          <div className="flex-1 min-w-0 space-y-0.5">
            <div className="flex items-center justify-between text-xs">
              <span className="font-bold truncate pr-2">
                {isBusy
                  ? `Starting ${modelShortName}`
                  : isReady
                  ? `${modelShortName} is ready`
                  : failureTitle(status)}
              </span>
              <ArrowUpRight className="w-3.5 h-3.5 shrink-0 opacity-60" />
            </div>
            <p className="text-[10.5px] text-md-inverse-on-surface/70 truncate">
              {isStale
                ? 'Checking…'
                : isBusy
                ? `${status?.message || 'Starting…'} · ${formatElapsed(elapsedSeconds)}`
                : isError
                ? status?.error || status?.message
                : 'Agents that use it can answer now.'}
            </p>
          </div>
        </button>

        {!isBusy && (
          <button
            type="button"
            onClick={() => setIsDismissed(true)}
            className="p-1.5 rounded-lg text-md-inverse-on-surface/60 hover:text-md-inverse-on-surface hover:bg-white/10 transition-colors shrink-0 cursor-pointer"
            title="Dismiss"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>
    </aside>
  );
};
