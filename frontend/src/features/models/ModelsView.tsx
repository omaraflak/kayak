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
  ChevronUp,
  HardDrive,
  Trash2,
  Play,
  Copy,
  Check,
  Zap,
  Package,
} from 'lucide-react';
import { api, errorMessage } from '../../api/client';
import { useDialog } from '../../context/DialogContext';
import { useVLLMStatus } from '../../context/VLLMStatusContext';
import { HostCapability, MetalStatus, ModelCacheInfo, VLLMDeployRequest } from '../../types';
import { HuggingFaceCatalog } from './HuggingFaceCatalog';
import { isMlxModel } from './metalModels';
import { VLLMLaunchDialog } from './VLLMLaunchDialog';
import { formatBytes } from './modelSizing';

/**
 * Local model serving.
 *
 * The page answers three questions in the order they get asked: what is running right
 * now and how do I point an agent at it, what does this machine hold, and how do I get
 * something new. Previously it opened on a Hugging Face search box seeded with someone
 * else's default query, and never mentioned the disk it was filling or the model string
 * an agent needs.
 */

export const ModelsView: React.FC = () => {
  const dialog = useDialog();
  const { status, logs, refresh: fetchStatus } = useVLLMStatus();
  const [capability, setCapability] = useState<HostCapability | null>(null);
  const [metal, setMetal] = useState<MetalStatus | null>(null);
  const [cache, setCache] = useState<ModelCacheInfo | null>(null);
  const [isStopping, setIsStopping] = useState(false);
  const [isDeploying, setIsDeploying] = useState(false);
  const [pendingLaunch, setPendingLaunch] = useState<string | null>(null);
  const [showLogs, setShowLogs] = useState(false);
  const [copied, setCopied] = useState(false);
  const logContainerRef = useRef<HTMLDivElement>(null);

  const isReady = status?.state === 'ready';
  const isLoading =
    ['pulling_image', 'starting_container', 'loading'].includes(status?.state || '') || isDeploying;
  const isError = status?.state === 'error';

  const refreshMachine = useCallback(async () => {
    const [capabilityResult, cacheResult, metalResult] = await Promise.allSettled([
      api.getHostCapability(),
      api.getModelCache(),
      api.getMetalStatus(),
    ]);
    if (capabilityResult.status === 'fulfilled') setCapability(capabilityResult.value);
    if (metalResult.status === 'fulfilled') setMetal(metalResult.value);
    if (cacheResult.status === 'fulfilled') setCache(cacheResult.value);
  }, []);

  useEffect(() => {
    refreshMachine();
  }, [refreshMachine]);

  // A deployment downloads weights, so the inventory is stale the moment one finishes.
  useEffect(() => {
    if (status?.state === 'ready' || status?.state === 'error') refreshMachine();
  }, [status?.state, refreshMachine]);

  useEffect(() => {
    if (showLogs && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [logs, showLogs]);

  // While provisioning or after a failure the logs are the only thing worth reading,
  // so they open themselves rather than hiding behind a click.
  useEffect(() => {
    if (isLoading || isError) setShowLogs(true);
  }, [isLoading, isError]);

  const cachedIds = new Set((cache?.models || []).map((model) => model.repo_id));

  /** The GPU server's model, while it is coming up or serving. */
  const metalModelId =
    metal && (metal.state === 'ready' || metal.state === 'starting') ? metal.model : null;
  /** True while the GPU server is installing or loading. */
  const metalBusy = metal?.state === 'installing' || metal?.state === 'starting';

  const requestLaunch = (modelId: string) => {
    if (capability && !capability.docker_available) {
      dialog.alert({
        title: 'Docker is not available',
        message:
          'Local serving runs vLLM in a Docker container. Start Docker and refresh this page.',
        variant: 'danger',
      });
      return;
    }
    setPendingLaunch(modelId);
  };

  // Metal runs on the host via the launcher rather than in a container, so its
  // progress arrives by polling the control file rather than the vLLM event
  // stream. Polled only while something is actually in flight.
  useEffect(() => {
    if (!metal || (metal.state !== 'installing' && metal.state !== 'starting')) return;
    const timer = setInterval(async () => {
      try {
        setMetal(await api.getMetalStatus());
      } catch {
        // The launcher rewrites the file continuously; a failed read is transient.
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [metal]);

  /**
   * Sends a model to whichever backend can actually run it.
   *
   * MLX weights only run on the Apple GPU, and the container dialog's options
   * -- GPU memory fraction, KV cache size, CUDA graphs -- mean nothing there,
   * so those models skip it and start directly.
   */
  const handleLaunchRequest = useCallback(
    async (modelId: string) => {
      if (metal?.supported && isMlxModel(modelId)) {
        try {
          setMetal(await api.startMetal(modelId));
          await fetchStatus();
        } catch (err) {
          // A refused start (launcher quit, non-MLX repository) used to be an
          // unhandled rejection: the click just did nothing.
          dialog.alert({
            title: 'Could not start the model',
            message: errorMessage(err),
            variant: 'danger',
          });
        }
        return;
      }
      requestLaunch(modelId);
    },
    [metal?.supported, requestLaunch, fetchStatus, dialog]
  );


  const handleLaunch = async (request: VLLMDeployRequest) => {
    setPendingLaunch(null);
    setIsDeploying(true);
    setShowLogs(true);
    try {
      await api.deployVLLMModel(request);
      await fetchStatus();
    } catch (err) {
      dialog.alert({
        title: 'Deployment Failed',
        message: `Could not start ${request.model_id}: ${err}`,
        variant: 'danger',
      });
    } finally {
      setIsDeploying(false);
    }
  };

  const handleStopServer = async () => {
    const confirmed = await dialog.confirm({
      title: 'Stop vLLM Container?',
      message:
        'Stopping the server will terminate the local container and unload models from memory. Active conversations using this local model will be interrupted.',
      confirmText: 'Stop Server',
      cancelText: 'Keep Running',
      variant: 'danger',
    });
    if (!confirmed) return;

    setIsStopping(true);
    try {
      // Whichever backend is up. Stopping the one that is not running is a
      // no-op on both sides, so this needs no branch.
      await api.stopVLLMServer();
      if (metal?.supported) setMetal(await api.stopMetal());
      await fetchStatus();
    } catch (err) {
      dialog.alert({
        title: 'Could not stop the server',
        message: errorMessage(err),
        variant: 'danger',
      });
    } finally {
      setIsStopping(false);
    }
  };

  const handleDeleteWeights = async (repoId: string, sizeBytes: number) => {
    const confirmed = await dialog.confirm({
      title: 'Delete downloaded weights?',
      message: `This frees ${formatBytes(sizeBytes)} by removing ${repoId} from this machine. Starting it again re-downloads the whole model.`,
      confirmText: 'Delete',
      cancelText: 'Keep',
      variant: 'danger',
    });
    if (!confirmed) return;

    try {
      await api.deleteCachedModel(repoId);
      await refreshMachine();
    } catch (err) {
      dialog.alert({
        title: 'Could not delete weights',
        message: errorMessage(err),
        variant: 'danger',
      });
    }
  };

  const agentModelString = status?.model_id ? `vllm/${status.model_id}` : null;

  const handleCopyModelString = async () => {
    if (!agentModelString) return;
    try {
      await navigator.clipboard.writeText(agentModelString);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard access can be denied; the string is on screen either way */
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 bg-md-surface overflow-hidden font-sans transition-colors">
      <div className="h-16 border-b border-md-outline-variant px-8 flex items-center justify-between bg-md-surface-container-low shrink-0 transition-colors">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded-xl bg-md-primary-container text-md-on-primary-container border border-md-outline-variant flex items-center justify-center shadow-2xs">
            <Cpu className="w-4 h-4" />
          </div>
          <div>
            <h2 className="font-bold text-sm text-md-on-surface">Local Models</h2>
            <p className="text-xs text-md-on-surface-variant">
              Serve open-weights models on this machine through vLLM
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={() => {
            fetchStatus();
            refreshMachine();
          }}
          className="p-2 rounded-xl border border-md-outline-variant text-md-on-surface hover:bg-md-surface-container-high transition-colors shadow-2xs cursor-pointer"
          title="Refresh status"
        >
          <RefreshCw className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-8 bg-md-surface">
        {/* ---------------------------------------------------------------- Runtime */}
        <section className="space-y-3">
          <SectionHeading
            icon={<Server className="w-3.5 h-3.5 text-md-primary" />}
            title="Server"
            note={isReady ? 'Reachable by any agent using the model string below' : undefined}
          />

          <div className="bg-md-surface border border-md-outline-variant rounded-2xl p-6 shadow-xs space-y-4 transition-colors">
            <div className="flex items-start justify-between gap-4">
              <div className="flex items-center space-x-3 min-w-0">
                <div
                  className={`w-10 h-10 rounded-2xl flex items-center justify-center border shadow-xs shrink-0 ${
                    isReady
                      ? 'bg-emerald-100 dark:bg-emerald-950/80 border-emerald-300 dark:border-emerald-800/80 text-emerald-800 dark:text-emerald-200'
                      : isLoading
                      ? 'bg-amber-100 dark:bg-amber-950/80 border-amber-300 dark:border-amber-800/80 text-amber-800 dark:text-amber-200'
                      : isError
                      ? 'bg-rose-100 dark:bg-rose-950/80 border-rose-300 dark:border-rose-800/80 text-rose-800 dark:text-rose-200'
                      : 'bg-md-surface-container-high border-md-outline-variant text-md-on-surface-variant'
                  }`}
                >
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

                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="font-bold text-sm text-md-on-surface">
                      {isReady
                        ? `Serving ${status?.model_id}`
                        : isLoading
                        ? `Starting ${status?.model_id || 'vLLM'}`
                        : isError
                        ? 'Deployment failed'
                        : 'No model running'}
                    </h3>
                    <StatusPill isReady={isReady} isLoading={isLoading} isError={isError} />
                  </div>
                  <p className="text-xs text-md-on-surface-variant mt-0.5 leading-relaxed">
                    {status?.message || 'Pick a model below to start serving it locally.'}
                  </p>
                </div>
              </div>

              {(isReady || isLoading || metalModelId || metalBusy) && (
                <button
                  type="button"
                  onClick={handleStopServer}
                  disabled={isStopping}
                  className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl bg-md-error hover:opacity-90 disabled:opacity-50 text-md-on-error text-xs font-semibold shadow-xs transition-opacity cursor-pointer shrink-0"
                >
                  {isStopping ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Square className="w-3.5 h-3.5 fill-current" />
                  )}
                  <span>Stop Server</span>
                </button>
              )}
            </div>

            {isError && status?.error && (
              <div className="p-3 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-300 dark:border-rose-800/80 text-rose-900 dark:text-rose-100 text-[11px] leading-relaxed">
                {status.error}
              </div>
            )}

            {/* The reason anyone starts a local server: a model string to point an
                agent at. It was never shown, so it had to be reconstructed by hand. */}
            {isReady && agentModelString && (
              <div className="pt-3 border-t border-md-outline-variant grid grid-cols-1 md:grid-cols-3 gap-3">
                <div className="md:col-span-2 bg-md-primary-container/40 border border-md-primary/50 p-3 rounded-xl">
                  <span className="text-[10px] text-md-on-surface-variant uppercase font-bold block mb-1">
                    Use in an agent
                  </span>
                  <div className="flex items-center gap-2">
                    <code className="text-xs font-mono font-semibold text-md-on-surface truncate flex-1">
                      {agentModelString}
                    </code>
                    <button
                      type="button"
                      onClick={handleCopyModelString}
                      className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-md-outline-variant bg-md-surface text-[10px] font-semibold text-md-on-surface hover:bg-md-surface-container-high transition-colors cursor-pointer shrink-0"
                    >
                      {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                      {copied ? 'Copied' : 'Copy'}
                    </button>
                  </div>
                </div>
                <div className="bg-md-surface-container-lowest p-3 rounded-xl border border-md-outline-variant">
                  <span className="text-[10px] text-md-on-surface-variant uppercase font-bold block mb-1">
                    Endpoint
                  </span>
                  <span className="text-xs font-mono text-md-on-surface break-all">
                    {status?.endpoint}
                  </span>
                </div>
              </div>
            )}

            {(isReady || isLoading || isError || logs.length > 0) && (
              <div className="pt-2 border-t border-md-outline-variant space-y-2">
                <button
                  type="button"
                  onClick={() => setShowLogs(!showLogs)}
                  className="inline-flex items-center gap-2 px-3 py-1.5 rounded-xl border border-md-outline-variant bg-md-surface-container-low hover:bg-md-surface-container-high text-xs font-semibold text-md-on-surface transition-colors cursor-pointer shadow-2xs"
                >
                  <Terminal className="w-3.5 h-3.5 text-md-on-surface-variant" />
                  <span>Container Logs ({logs.length} lines)</span>
                  {showLogs ? (
                    <ChevronUp className="w-3.5 h-3.5 text-md-on-surface-variant" />
                  ) : (
                    <ChevronDown className="w-3.5 h-3.5 text-md-on-surface-variant" />
                  )}
                </button>

                {showLogs && (
                  <div
                    ref={logContainerRef}
                    className="p-3.5 bg-md-surface-container-lowest text-md-on-surface font-mono text-[11px] rounded-xl overflow-y-auto space-y-0.5 leading-relaxed h-80 border border-md-outline-variant"
                  >
                    {logs.length === 0 ? (
                      <div className="text-md-on-surface-variant italic">
                        Waiting for container log output...
                      </div>
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
        </section>

        {/* ---------------------------------------------------------------- Machine */}
        <section className="space-y-3">
          <SectionHeading
            icon={<HardDrive className="w-3.5 h-3.5 text-md-primary" />}
            title="This machine"
            note={cache ? `${formatBytes(cache.total_bytes)} of weights cached` : undefined}
          />

          {capability && !capability.docker_available && (
            <div className="p-3.5 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-300 dark:border-rose-800/80 text-rose-900 dark:text-rose-100 text-[11px] leading-relaxed flex items-start gap-2">
              <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <p>
                Docker is not reachable, so no model can be served locally. Start Docker Desktop
                (or the daemon) and refresh.
              </p>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <Stat
              icon={<Zap className="w-3.5 h-3.5" />}
              label="Accelerator"
              value={
                // The GPU probe only knows about NVIDIA, so an Apple Silicon Mac
                // reported "CPU only" while sitting next to a usable GPU. Metal
                // has to be asked separately.
                metal?.supported
                  ? 'Apple GPU'
                  : capability
                  ? capability.gpus.length > 0
                    ? capability.gpus.map((gpu) => gpu.name).join(', ')
                    : 'CPU only'
                  : '—'
              }
              detail={
                metal?.supported
                  ? 'Available for MLX models'
                  : metal?.detail
                  ? metal.detail
                  : capability && capability.total_vram_mb > 0
                  ? `${(capability.total_vram_mb / 1024).toFixed(0)} GB VRAM`
                  : capability
                  ? 'Generation will be slow'
                  : undefined
              }
              tone={
                metal?.supported
                  ? 'default'
                  : capability && capability.gpus.length === 0
                  ? 'warning'
                  : 'default'
              }
            />
            <Stat
              icon={<Package className="w-3.5 h-3.5" />}
              label="vLLM image"
              value={
                capability?.image_present === true
                  ? 'Pulled'
                  : capability?.image_present === false
                  ? 'Not pulled'
                  : '—'
              }
              detail={
                capability?.image_present === false
                  ? 'First start downloads several GB'
                  : undefined
              }
              tone={capability?.image_present === false ? 'warning' : 'default'}
            />
            <Stat
              icon={<HardDrive className="w-3.5 h-3.5" />}
              label="Weights on disk"
              value={cache ? formatBytes(cache.total_bytes) : '—'}
              detail={cache ? `${cache.models.length} model${cache.models.length === 1 ? '' : 's'}` : undefined}
            />
          </div>

          {/* Downloaded weights: the inventory that made the cache invisible before. */}
          {cache && cache.models.length > 0 && (
            <div className="space-y-1.5 pt-1">
              {cache.models.map((model) => {
                // Disjoint on purpose: a model whose server is still coming up was
                // shown as "Serving" the instant Start was clicked, which is a lie
                // the logs directly above it contradicted.
                const isServing = status?.model_id === model.repo_id && isReady;
                const isStarting = status?.model_id === model.repo_id && isLoading;
                const occupiesServer = isServing || isStarting;
                return (
                  <div
                    key={model.repo_id}
                    className={`p-3 rounded-xl border flex items-center justify-between gap-4 transition-colors ${
                      occupiesServer
                        ? 'bg-md-primary-container/40 border-md-primary/50'
                        : 'bg-md-surface border-md-outline-variant'
                    }`}
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-semibold text-xs font-mono text-md-on-surface truncate">
                          {model.repo_id}
                        </span>
                        {isServing && (
                          <span className="text-[9px] font-semibold uppercase px-1.5 py-0.5 rounded bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-200 border border-emerald-300 dark:border-emerald-800/80">
                            Serving
                          </span>
                        )}
                        {isStarting && (
                          <span className="inline-flex items-center gap-1 text-[9px] font-semibold uppercase px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-950/80 text-amber-800 dark:text-amber-200 border border-amber-300 dark:border-amber-800/80">
                            <Loader2 className="w-2.5 h-2.5 animate-spin" />
                            Starting
                          </span>
                        )}
                      </div>
                      <p className="text-[11px] text-md-on-surface-variant mt-0.5">
                        {formatBytes(model.size_bytes)} on disk
                      </p>
                    </div>

                    <div className="flex items-center gap-1.5 shrink-0">
                      {!occupiesServer && (
                        <button
                          type="button"
                          onClick={() => handleLaunchRequest(model.repo_id)}
                          disabled={isLoading}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-md-outline-variant bg-md-surface-container-low hover:bg-md-surface-container-high disabled:opacity-40 text-[11px] font-semibold text-md-on-surface transition-colors cursor-pointer"
                        >
                          <Play className="w-3 h-3 fill-current" />
                          Start
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => handleDeleteWeights(model.repo_id, model.size_bytes)}
                        disabled={occupiesServer}
                        title={
                          occupiesServer
                            ? 'Stop the server before deleting these weights'
                            : 'Delete these weights'
                        }
                        className="p-1.5 rounded-lg border border-md-outline-variant text-md-on-surface-variant hover:text-md-error hover:border-md-error/50 disabled:opacity-30 disabled:cursor-not-allowed transition-colors cursor-pointer"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {cache && cache.models.length === 0 && (
            <p className="text-[11px] text-md-on-surface-variant py-2">
              No weights downloaded yet. Starting a model from the catalog below downloads it to{' '}
              <code className="font-mono">{cache.path}</code>.
            </p>
          )}
        </section>

        {/* ---------------------------------------------------------------- Catalog */}
        <section className="space-y-3">
          <SectionHeading
            icon={<Layers className="w-3.5 h-3.5 text-md-primary" />}
            title="Find a model"
            note="Open-weights text-generation models from the Hugging Face Hub"
          />

          <HuggingFaceCatalog
            mode="deploy"
            onDeployVLLM={handleLaunchRequest}
            // Only a model that is actually up or coming up occupies the server:
            // after a stop or a failure, status.model_id still names the last
            // model, and passing it here showed that model as "Active".
            activeVllmModelId={metalModelId ?? (isReady || isLoading ? status?.model_id : null)}
            isVllmLoading={isLoading || metalBusy}
            cachedModelIds={cachedIds}
            availableVramGB={(capability?.total_vram_mb ?? 0) / 1024}
            metalSupported={metal?.supported ?? false}
          />
        </section>
      </div>

      {pendingLaunch && (
        <VLLMLaunchDialog
          modelId={pendingLaunch}
          capability={capability}
          replacingModelId={isReady || isLoading ? status?.model_id : null}
          isCached={cachedIds.has(pendingLaunch)}
          onCancel={() => setPendingLaunch(null)}
          onLaunch={handleLaunch}
        />
      )}
    </div>
  );
};

const SectionHeading: React.FC<{ icon: React.ReactNode; title: string; note?: string }> = ({
  icon,
  title,
  note,
}) => (
  <div className="flex items-center justify-between gap-3 flex-wrap">
    <h4 className="text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
      {icon} {title}
    </h4>
    {note && <span className="text-[11px] text-md-on-surface-variant">{note}</span>}
  </div>
);

const StatusPill: React.FC<{ isReady: boolean; isLoading: boolean; isError: boolean }> = ({
  isReady,
  isLoading,
  isError,
}) => (
  <span
    className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border ${
      isReady
        ? 'bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-200 border-emerald-300 dark:border-emerald-800/80'
        : isLoading
        ? 'bg-amber-100 dark:bg-amber-950/80 text-amber-800 dark:text-amber-200 border-amber-300 dark:border-amber-800/80'
        : isError
        ? 'bg-rose-100 dark:bg-rose-950/80 text-rose-800 dark:text-rose-200 border-rose-300 dark:border-rose-800/80'
        : 'bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant'
    }`}
  >
    {isReady ? 'ONLINE' : isLoading ? 'STARTING' : isError ? 'ERROR' : 'STOPPED'}
  </span>
);

const Stat: React.FC<{
  icon: React.ReactNode;
  label: string;
  value: string;
  detail?: string;
  tone?: 'default' | 'warning';
}> = ({ icon, label, value, detail, tone = 'default' }) => (
  <div
    className={`p-3.5 rounded-xl border ${
      tone === 'warning'
        ? 'bg-amber-50 dark:bg-amber-950/40 border-amber-300 dark:border-amber-800/80'
        : 'bg-md-surface-container-lowest border-md-outline-variant'
    }`}
  >
    <span className="text-[10px] text-md-on-surface-variant uppercase font-bold flex items-center gap-1.5">
      {icon} {label}
    </span>
    <span className="text-xs font-semibold text-md-on-surface block mt-1 truncate" title={value}>
      {value}
    </span>
    {detail && <span className="text-[11px] text-md-on-surface-variant block">{detail}</span>}
  </div>
);
