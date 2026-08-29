import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Cpu,
  Square,
  Terminal,
  AlertCircle,
  Loader2,
  Layers,
  ChevronDown,
  ChevronUp,
  HardDrive,
  Trash2,
  Play,
  Zap,
  Package,
} from 'lucide-react';
import { api, errorMessage } from '../../api/client';
import { useDialog } from '../../context/DialogContext';
import { useVLLMStatus } from '../../context/VLLMStatusContext';
import {
  HostCapability,
  MetalStatus,
  Modality,
  ModelCacheInfo,
  RuntimeDescriptor,
  VLLMDeployRequest,
  VLLMDeploymentProgress,
} from '../../types';
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
  const { status, logs, statuses, logsByModality, refresh: fetchStatus } = useVLLMStatus();
  const [capability, setCapability] = useState<HostCapability | null>(null);
  const [metal, setMetal] = useState<MetalStatus | null>(null);
  const [cache, setCache] = useState<ModelCacheInfo | null>(null);
  /** Which server is winding down, so only its row shows the stopping state. */
  const [stoppingModality, setStoppingModality] = useState<Modality | null>(null);
  const [isDeploying, setIsDeploying] = useState(false);
  const [pendingLaunch, setPendingLaunch] = useState<string | null>(null);
  /** Which server's log drawer is open. Only one at a time; they are long. */
  const [openLogsFor, setOpenLogsFor] = useState<Modality | null>(null);
  const [runtimes, setRuntimes] = useState<RuntimeDescriptor[]>([]);
  // Which runtime's catalogue is being browsed. Every start on this page goes to
  // the matching server, so a speech model can never be handed to vLLM.
  const [modality, setModality] = useState<Modality>('text');
  // Which runtime the pending launch goes to. Distinct from the browsing
  // modality above: a cached model is started from "This machine", where no
  // filter is in play, and it has to reach the runtime that can load it.
  const [launchModality, setLaunchModality] = useState<Modality>('text');
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
    api.listInferenceRuntimes().then(setRuntimes).catch(() => setRuntimes([]));
  }, []);

  useEffect(() => {
    refreshMachine();
  }, [refreshMachine]);

  // A deployment downloads weights, so the inventory is stale the moment one finishes.
  useEffect(() => {
    if (status?.state === 'ready' || status?.state === 'error') refreshMachine();
  }, [status?.state, refreshMachine]);

  // Machine facts change outside the app -- Docker started, weights deleted on
  // disk -- so they are re-read when the user comes back to the tab. This is
  // what the manual refresh button used to exist for.
  useEffect(() => {
    const onFocus = () => {
      fetchStatus();
      refreshMachine();
    };
    const onVisible = () => {
      if (!document.hidden) onFocus();
    };
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [fetchStatus, refreshMachine]);

  // While Docker is down the page watches for it to come up, so starting Docker
  // Desktop clears the warning without anyone having to reload anything.
  const dockerDown = capability ? !capability.docker_available : false;
  useEffect(() => {
    if (!dockerDown) return;
    const timer = setInterval(refreshMachine, 15000);
    return () => clearInterval(timer);
  }, [dockerDown, refreshMachine]);

  useEffect(() => {
    if (openLogsFor && logContainerRef.current) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [logsByModality, openLogsFor]);

  // While provisioning or after a failure the logs are the only thing worth reading,
  // so they open themselves rather than hiding behind a click. Whichever server is
  // in that state claims the drawer.
  useEffect(() => {
    for (const progress of Object.values(statuses)) {
      if (!progress) continue;
      if (LOADING_STATES.includes(progress.state) || progress.state === 'error') {
        setOpenLogsFor(progress.modality);
        return;
      }
    }
  }, [statuses]);

  const cachedIds = new Set((cache?.models || []).map((model) => model.repo_id));
  /**
   * Whether any server is mid-deployment.
   *
   * Start is blocked while one is: they share the weight cache and the machine's
   * memory, and two first-time downloads at once is not what anyone means by
   * clicking two buttons.
   */
  const anyServerBusy =
    isDeploying ||
    Object.values(statuses).some(
      (progress) => !!progress && LOADING_STATES.includes(progress.state)
    );

  /**
   * The model whose card carries the server state. Status, logs, and the stop
   * control live in that model's row in the inventory below -- a separate
   * "Server" section used to describe the same model a second time, and the two
   * could disagree.
   */
  /** The GPU server's model, while it is coming up or serving. */
  const metalModelId =
    metal && (metal.state === 'ready' || metal.state === 'starting') ? metal.model : null;
  /** True while the GPU server is installing or loading. */
  const metalBusy = metal?.state === 'installing' || metal?.state === 'starting';

  /**
   * Every server holding a model right now, keyed by the model it holds.
   *
   * Plural on purpose. This was a single value read from the text server, so once
   * a second modality existed a running speech or transcription model rendered as
   * an inert "Start" row -- the page said nothing was running while the Audio page
   * was happily using it.
   */
  const activeByModel = new Map<string, VLLMDeploymentProgress>();
  for (const progress of Object.values(statuses)) {
    if (!progress?.model_id) continue;
    if (SERVER_HELD_STATES.includes(progress.state)) {
      activeByModel.set(progress.model_id, progress);
    }
  }
  // The GPU server is the text server by another route, so its model belongs in
  // the same map rather than in a branch of its own.
  if (metalModelId && !activeByModel.has(metalModelId) && statuses.text) {
    activeByModel.set(metalModelId, statuses.text);
  }

  const cachedModels = cache?.models || [];
  /** Inventory rows, running models first -- including one being downloaded or
      served from outside the cache, which has no cached entry yet. */
  const uncachedActive = [...activeByModel.keys()].filter(
    (repoId) => !cachedModels.some((model) => model.repo_id === repoId)
  );
  const displayModels = [
    ...uncachedActive.map((repo_id) => ({ repo_id, size_bytes: 0, modified_at: 0 })),
    ...[...cachedModels].sort((a, b) => {
      const left = activeByModel.has(a.repo_id) ? 0 : 1;
      const right = activeByModel.has(b.repo_id) ? 0 : 1;
      return left - right;
    }),
  ];

  const requestLaunch = (modelId: string) => {
    if (capability && !capability.docker_available) {
      dialog.alert({
        title: 'Docker is not available',
        message:
          'Local serving runs vLLM in a Docker container. Start Docker and this page will notice on its own.',
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
      // Which runtime can serve it is decided by the Hub's own metadata, not by
      // the filter the user happens to be browsing: pressing Start on a cached
      // Kokoro sent it to vLLM, which spent minutes on a model it cannot load.
      let target: Modality = modality;
      try {
        const classification = await api.classifyModel(modelId);
        if (classification.modality && !classification.supported) {
          dialog.alert({
            title: 'This model cannot run here yet',
            message:
              `${modelId} is a ${classification.pipeline_tag ?? 'model'} that needs ` +
              `${classification.library_name ?? 'a library'}, which the local runtime ` +
              'does not ship a backend for.',
            variant: 'danger',
          });
          return;
        }
        if (classification.modality) target = classification.modality;
      } catch {
        // The Hub being unreachable must not stop a start; the filter in view is
        // the best remaining guess.
      }

      setLaunchModality(target);
      requestLaunch(modelId);
    },
    [metal?.supported, requestLaunch, fetchStatus, dialog, modality]
  );


  const handleLaunch = async (request: VLLMDeployRequest) => {
    setPendingLaunch(null);
    setIsDeploying(true);
    // Open the drawer of the server being started, not whichever was open before.
    setOpenLogsFor(launchModality);
    try {
      await api.deployVLLMModel(request, launchModality);
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

  const handleStopServer = async (modality: Modality) => {
    const label =
      runtimes.find((runtime) => runtime.modality === modality)?.label ?? 'this server';
    const confirmed = await dialog.confirm({
      title: `Stop ${label}?`,
      message:
        'Stopping the server will terminate its container and unload the model from ' +
        'memory. Anything using it right now will be interrupted. Other local servers ' +
        'keep running.',
      confirmText: 'Stop Server',
      cancelText: 'Keep Running',
      variant: 'danger',
    });
    if (!confirmed) return;

    setStoppingModality(modality);
    try {
      await api.stopVLLMServer(modality);
      // Metal is the text server by another route, so it winds down with it.
      if (modality === 'text' && metal?.supported) setMetal(await api.stopMetal());
      await fetchStatus();
    } catch (err) {
      dialog.alert({
        title: 'Could not stop the server',
        message: errorMessage(err),
        variant: 'danger',
      });
    } finally {
      setStoppingModality(null);
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

      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-8 bg-md-surface">
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
                (or the daemon) — this page checks again every few seconds and will
                update by itself.
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

          {/* The inventory is also where the server lives: the active model's row
              expands into the status card, so state, logs, and controls sit on the
              model they describe instead of in a parallel section. */}
          {displayModels.length > 0 && (
            <div className="space-y-1.5 pt-1">
              {displayModels.map((model) => {
                // Each row describes the server actually holding that model, not
                // whatever the text server happens to be doing.
                const server = activeByModel.get(model.repo_id);
                const rowModality = server?.modality ?? 'text';
                const rowReady = server?.state === 'ready';
                const rowLoading =
                  !!server && LOADING_STATES.includes(server.state);
                const rowError = server?.state === 'error';
                const rowStopping = stoppingModality === rowModality;
                const rowLogs = logsByModality[rowModality] ?? [];
                const rowLogsOpen = openLogsFor === rowModality;

                if (!server) {
                  return (
                    <div
                      key={model.repo_id}
                      className="p-3 rounded-xl border flex items-center justify-between gap-4 transition-colors bg-md-surface border-md-outline-variant"
                    >
                      <div className="min-w-0">
                        <span className="font-semibold text-xs font-mono text-md-on-surface truncate block">
                          {model.repo_id}
                        </span>
                        <p className="text-[11px] text-md-on-surface-variant mt-0.5">
                          {formatBytes(model.size_bytes)} on disk
                        </p>
                      </div>

                      <div className="flex items-center gap-1.5 shrink-0">
                        <button
                          type="button"
                          onClick={() => handleLaunchRequest(model.repo_id)}
                          disabled={anyServerBusy}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-md-outline-variant bg-md-surface-container-low hover:bg-md-surface-container-high disabled:opacity-40 text-[11px] font-semibold text-md-on-surface transition-colors cursor-pointer"
                        >
                          <Play className="w-3 h-3 fill-current" />
                          Start
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDeleteWeights(model.repo_id, model.size_bytes)}
                          title="Delete these weights"
                          className="p-1.5 rounded-lg border border-md-outline-variant text-md-on-surface-variant hover:text-md-error hover:border-md-error/50 transition-colors cursor-pointer"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  );
                }

                return (
                  <div
                    key={model.repo_id}
                    className={`rounded-2xl border shadow-xs transition-colors ${
                      isError
                        ? 'bg-md-surface border-rose-300 dark:border-rose-800/80'
                        : 'bg-md-primary-container/20 border-md-primary/50'
                    }`}
                  >
                    <div className="p-4 space-y-3">
                      <div className="flex items-start justify-between gap-4">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-semibold text-xs font-mono text-md-on-surface truncate">
                              {model.repo_id}
                            </span>
                            <StatusPill
                              isReady={rowReady}
                              isLoading={rowLoading}
                              isError={rowError}
                              isStopping={rowStopping}
                            />
                            <span className="text-[10px] font-bold uppercase tracking-wider text-md-on-surface-variant">
                              {runtimes.find((r) => r.modality === rowModality)?.label ??
                                rowModality}
                            </span>
                          </div>
                          <p className="text-[11px] text-md-on-surface-variant mt-1 leading-relaxed">
                            {rowStopping
                              ? 'Stopping the server and unloading the model...'
                              : server.message}
                            {model.size_bytes > 0 && ` · ${formatBytes(model.size_bytes)} on disk`}
                          </p>
                          {rowReady && server.endpoint && (
                            <p className="text-[10px] font-mono text-md-on-surface-variant/80 mt-0.5">
                              {server.endpoint}
                            </p>
                          )}
                        </div>

                        <div className="flex items-center gap-1.5 shrink-0">
                          {rowError ? (
                            <button
                              type="button"
                              onClick={() => handleLaunchRequest(model.repo_id)}
                              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-md-outline-variant bg-md-surface-container-low hover:bg-md-surface-container-high text-[11px] font-semibold text-md-on-surface transition-colors cursor-pointer"
                            >
                              <Play className="w-3 h-3 fill-current" />
                              Try again
                            </button>
                          ) : (
                            <button
                              type="button"
                              onClick={() => handleStopServer(rowModality)}
                              disabled={rowStopping}
                              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl bg-md-error hover:opacity-90 disabled:opacity-50 text-md-on-error text-xs font-semibold shadow-xs transition-opacity cursor-pointer"
                            >
                              {rowStopping ? (
                                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                              ) : (
                                <Square className="w-3.5 h-3.5 fill-current" />
                              )}
                              <span>Stop Server</span>
                            </button>
                          )}
                        </div>
                      </div>

                      {rowError && server.error && (
                        <div className="p-3 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-300 dark:border-rose-800/80 text-rose-900 dark:text-rose-100 text-[11px] leading-relaxed">
                          {server.error}
                        </div>
                      )}

                      {(rowLoading || rowError || rowLogs.length > 0) && (
                        <div className="pt-2 border-t border-md-outline-variant space-y-2">
                          <button
                            type="button"
                            onClick={() =>
                              setOpenLogsFor(rowLogsOpen ? null : rowModality)
                            }
                            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-xl border border-md-outline-variant bg-md-surface-container-low hover:bg-md-surface-container-high text-xs font-semibold text-md-on-surface transition-colors cursor-pointer shadow-2xs"
                          >
                            <Terminal className="w-3.5 h-3.5 text-md-on-surface-variant" />
                            <span>Container Logs ({rowLogs.length} lines)</span>
                            {rowLogsOpen ? (
                              <ChevronUp className="w-3.5 h-3.5 text-md-on-surface-variant" />
                            ) : (
                              <ChevronDown className="w-3.5 h-3.5 text-md-on-surface-variant" />
                            )}
                          </button>

                          {rowLogsOpen && (
                            <div
                              ref={logContainerRef}
                              className="p-3.5 bg-md-surface-container-lowest text-md-on-surface font-mono text-[11px] rounded-xl overflow-y-auto space-y-0.5 leading-relaxed h-72 border border-md-outline-variant"
                            >
                              {rowLogs.length === 0 ? (
                                <div className="text-md-on-surface-variant italic">
                                  Waiting for container log output...
                                </div>
                              ) : (
                                rowLogs.map((line, idx) => (
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
                  </div>
                );
              })}
            </div>
          )}

          {displayModels.length === 0 && cache && (
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
            note="Open-weights models from the Hugging Face Hub"
          />

          <HuggingFaceCatalog
            mode="deploy"
            onDeployVLLM={handleLaunchRequest}
            // Only a model that is actually up or coming up occupies the server:
            // after a stop or a failure, status.model_id still names the last
            // model, and passing it here showed that model as "Active".
            activeVllmModelId={
              modality === 'text'
                ? metalModelId ?? (isReady || isLoading ? status?.model_id : null)
                : activeModelFor(statuses[modality])
            }
            isVllmLoading={isLoading || metalBusy}
            cachedModelIds={cachedIds}
            availableVramGB={(capability?.total_vram_mb ?? 0) / 1024}
            metalSupported={metal?.supported ?? false}
            runtimes={runtimes}
            modality={modality}
            onSelectModality={setModality}
          />
        </section>
      </div>

      {pendingLaunch && (
        <VLLMLaunchDialog
          modelId={pendingLaunch}
          capability={capability}
          replacingModelId={isReady || isLoading ? status?.model_id : null}
          isCached={cachedIds.has(pendingLaunch)}
          // Only the settings the runtime this launch is bound for actually reads:
          // a transcription model has no context window to offer.
          tunableFields={runtimes.find((r) => r.modality === launchModality)?.tunable_fields}
          onCancel={() => setPendingLaunch(null)}
          onLaunch={handleLaunch}
        />
      )}
    </div>
  );
};

/**
 * The model a server is actually holding, or null.
 *
 * "Serving" and "starting" stay disjoint here as everywhere else: after a stop or
 * a failure the status still names the last model, and treating that as active
 * marked a dead server's model as running.
 */
/** States in which a server is holding a model, so its row owns the status card. */
const SERVER_HELD_STATES = ['ready', 'pulling_image', 'starting_container', 'loading', 'error'];
/** States in which a server is coming up rather than serving. */
const LOADING_STATES = ['pulling_image', 'starting_container', 'loading'];

function activeModelFor(progress?: VLLMDeploymentProgress): string | null {
  if (!progress) return null;
  const active = ['ready', 'pulling_image', 'starting_container', 'loading'];
  return active.includes(progress.state) ? progress.model_id ?? null : null;
}

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

const StatusPill: React.FC<{
  isReady: boolean;
  isLoading: boolean;
  isError: boolean;
  isStopping?: boolean;
}> = ({ isReady, isLoading, isError, isStopping }) => {
  // Both transitions get their own state with a spinner inside the pill: a
  // server winding down is not "online", and one coming up is not "serving".
  const inTransition = isStopping || isLoading;
  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border ${
        isStopping || isLoading
          ? 'bg-amber-100 dark:bg-amber-950/80 text-amber-800 dark:text-amber-200 border-amber-300 dark:border-amber-800/80'
          : isReady
          ? 'bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-200 border-emerald-300 dark:border-emerald-800/80'
          : isError
          ? 'bg-rose-100 dark:bg-rose-950/80 text-rose-800 dark:text-rose-200 border-rose-300 dark:border-rose-800/80'
          : 'bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant'
      }`}
    >
      {inTransition && <Loader2 className="w-2.5 h-2.5 animate-spin" />}
      {isStopping
        ? 'STOPPING'
        : isLoading
        ? 'STARTING'
        : isReady
        ? 'ONLINE'
        : isError
        ? 'ERROR'
        : 'STOPPED'}
    </span>
  );
};

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
