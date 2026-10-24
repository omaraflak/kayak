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
import { activeModelOf, isLoadingState, useVLLMStatus } from '../../context/VLLMStatusContext';
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
import { shortModelName, startBlocker } from './serverStatus';
import { VLLMLaunchDialog } from './VLLMLaunchDialog';
import { formatBytes } from '../../utils/format';

/**
 * Local model serving.
 *
 * The page answers three questions in the order they get asked: what is running right
 * now and how do I point an agent at it, what does this Mac hold, and how do I get
 * something new.
 *
 * Models run in Docker on the CPU, except MLX models on Apple Silicon, which the
 * launcher serves on the GPU. Both are the text server as far as this page is
 * concerned: starting, stopping and status go through the same calls.
 */

export interface ModelsViewProps {
  /** Which runtime's catalogue to browse. Comes from the URL. */
  modality: Modality;
  onSelectModality: (modality: Modality) => void;
}

export const ModelsView: React.FC<ModelsViewProps> = ({
  modality,
  onSelectModality,
}) => {
  const dialog = useDialog();
  const { status, statuses, logsByModality, isStale, refresh: fetchStatus } = useVLLMStatus();
  const [capability, setCapability] = useState<HostCapability | null>(null);
  const [metal, setMetal] = useState<MetalStatus | null>(null);
  const [cache, setCache] = useState<ModelCacheInfo | null>(null);
  /** Which server is winding down, so only its row shows the stopping state. */
  const [stoppingModality, setStoppingModality] = useState<Modality | null>(null);
  const [isDeploying, setIsDeploying] = useState(false);
  /**
   * The model whose Start is being checked with Hugging Face before anything
   * begins -- a few seconds in which the button would otherwise do nothing visible.
   */
  const [checkingModelId, setCheckingModelId] = useState<string | null>(null);
  const [pendingLaunch, setPendingLaunch] = useState<string | null>(null);
  /** Which server's log drawer is open. Only one at a time; they are long. */
  const [openLogsFor, setOpenLogsFor] = useState<Modality | null>(null);
  const [runtimes, setRuntimes] = useState<RuntimeDescriptor[]>([]);
  // Which runtime the pending launch goes to. Distinct from the browsing
  // modality above: a cached model is started from "This machine", where no
  // filter is in play, and it has to reach the runtime that can load it.
  const [launchModality, setLaunchModality] = useState<Modality>('text');
  const logContainerRef = useRef<HTMLDivElement>(null);

  // The catalogue's busy state follows the server being browsed, not always the
  // text one: browsing voice models while a text model starts must not read as a
  // voice model starting.
  const isBrowsedServerBusy = isLoadingState(statuses[modality]?.state) || isDeploying;

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
      if (isLoadingState(progress.state) || progress.state === 'error') {
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
    isDeploying || Object.values(statuses).some((progress) => isLoadingState(progress?.state));

  /**
   * Every server holding a model right now, keyed by the model it holds. Status,
   * logs, and the stop control live in that model's row in the inventory below.
   */
  const activeByModel = new Map<string, VLLMDeploymentProgress>();
  for (const progress of Object.values(statuses)) {
    if (!progress?.model_id) continue;
    if (SERVER_HELD_STATES.includes(progress.state)) {
      activeByModel.set(progress.model_id, progress);
    }
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
        title: "Docker Desktop isn't running",
        message:
          'Models that run on the processor need Docker Desktop. Open it, wait for it to ' +
          'finish starting, then try again. This page notices on its own.',
        variant: 'danger',
      });
      return;
    }
    setPendingLaunch(modelId);
  };

  /**
   * Sends a model to whichever backend can actually run it.
   *
   * MLX weights only run on the Apple GPU, where the container dialog's options --
   * KV cache size, container memory and cores -- mean nothing, so those models
   * skip it and start directly.
   *
   * Before anything starts, Hugging Face is asked whether the model can be
   * fetched at all: a model whose makers require accepting their terms, or one
   * that no longer exists, used to fail only after gigabytes had downloaded.
   *
   * @param fallbackModality Where the model goes when the Hub cannot say: the
   *   row's own server for a model that is already running or failed, otherwise
   *   the catalogue being browsed.
   */
  const handleLaunchRequest = async (modelId: string, fallbackModality: Modality = modality) => {
    const name = shortModelName(modelId);
    const blocker = startBlocker(modelId, metal);
    if (blocker) {
      dialog.alert({ title: `${name} can't run on this Mac`, message: blocker, variant: 'danger' });
      return;
    }

    setCheckingModelId(modelId);
    let target: Modality = fallbackModality;
    try {
      // Which runtime can serve it is decided by the Hub's own metadata, not by
      // the filter the user happens to be browsing: pressing Start on a cached
      // Kokoro sent it to vLLM, which spent minutes on a model it cannot load.
      try {
        const classification = await api.classifyModel(modelId);
        if (classification.message) {
          dialog.alert({
            title: `${name} can't start yet`,
            message: classification.message,
            variant: 'danger',
          });
          return;
        }
        if (classification.modality && !classification.supported) {
          dialog.alert({
            title: `${name} can't start`,
            message: "Kayak can't run this model yet. Try another one from the list.",
            variant: 'danger',
          });
          return;
        }
        if (classification.modality) target = classification.modality;
      } catch {
        // The Hub being unreachable must not stop a start; the best remaining
        // guess stands, and the start itself reports anything that goes wrong.
      }
    } finally {
      setCheckingModelId(null);
    }

    if (metal?.supported && isMlxModel(modelId)) {
      setOpenLogsFor('text');
      setIsDeploying(true);
      try {
        await api.deployVLLMModel({ model_id: modelId, force_restart: true }, 'text');
        await fetchStatus();
      } catch (err) {
        dialog.alert({
          title: `Couldn't start ${name}`,
          message: errorMessage(err, `Couldn't start ${name}. Try again.`),
          variant: 'danger',
        });
      } finally {
        setIsDeploying(false);
      }
      return;
    }

    setLaunchModality(target);
    requestLaunch(modelId);
  };

  const handleLaunch = async (request: VLLMDeployRequest) => {
    setPendingLaunch(null);
    setIsDeploying(true);
    // Open the drawer of the server being started, not whichever was open before.
    setOpenLogsFor(launchModality);
    try {
      await api.deployVLLMModel(request, launchModality);
      await fetchStatus();
    } catch (err) {
      const name = shortModelName(request.model_id);
      dialog.alert({
        title: `Couldn't start ${name}`,
        message: errorMessage(err, `Couldn't start ${name}. Try again.`),
        variant: 'danger',
      });
    } finally {
      setIsDeploying(false);
    }
  };

  /** Stops one server. Shared by Stop, which asks first, and Dismiss, which need not. */
  const stopServer = async (serverModality: Modality, modelId: string) => {
    setStoppingModality(serverModality);
    try {
      await api.stopVLLMServer(serverModality);
      await fetchStatus();
    } catch (err) {
      const name = shortModelName(modelId);
      dialog.alert({
        title: `Couldn't stop ${name}`,
        message: errorMessage(err, `Couldn't stop ${name}. Try again.`),
        variant: 'danger',
      });
    } finally {
      setStoppingModality(null);
    }
  };

  const handleStopServer = async (serverModality: Modality, modelId: string) => {
    const name = shortModelName(modelId);
    const confirmed = await dialog.confirm({
      title: `Stop ${name}?`,
      message:
        'It will be unloaded from memory, and anything using it right now will stop. ' +
        'Other models keep running.',
      confirmText: 'Stop',
      cancelText: 'Keep running',
      variant: 'danger',
    });
    if (!confirmed) return;
    await stopServer(serverModality, modelId);
  };

  /**
   * Clears a failed model's row. Nothing is running to interrupt, so there is
   * nothing to confirm; it is a stop only so the server forgets the failure too.
   */
  const handleDismiss = (serverModality: Modality, modelId: string) =>
    stopServer(serverModality, modelId);

  const handleDeleteWeights = async (repoId: string, sizeBytes: number) => {
    const name = shortModelName(repoId);
    const confirmed = await dialog.confirm({
      title: `Delete ${name}?`,
      message: `This frees ${formatBytes(sizeBytes)} on this Mac. Starting it again downloads the whole model again.`,
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
        title: `Couldn't delete ${name}`,
        message: errorMessage(err, "Couldn't delete this model's files. Try again."),
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
              Run AI models on this Mac, without sending anything online
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
            note={cache ? `${formatBytes(cache.total_bytes)} of models downloaded` : undefined}
          />

          {capability && !capability.docker_available && (
            <div className="p-3.5 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-300 dark:border-rose-800/80 text-rose-900 dark:text-rose-100 text-[11px] leading-relaxed flex items-start gap-2">
              <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <p>
                {metal?.supported
                  ? "Docker Desktop isn't running, so only models marked GPU can start. "
                  : "Docker Desktop isn't running, so no model can start. "}
                Open Docker Desktop — this page checks again every few seconds and updates by
                itself.
              </p>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <Stat
              icon={<Zap className="w-3.5 h-3.5" />}
              label="Speed"
              // Docker on a Mac has no GPU access; the Apple GPU is reached through
              // the launcher, for MLX models only.
              value={metal?.supported ? "Your Mac's GPU" : 'Processor only'}
              detail={
                metal?.supported
                  ? 'Models marked GPU run fastest'
                  : metal?.detail ?? 'Models will run slowly'
              }
              tone={metal?.supported ? 'default' : 'warning'}
            />
            <Stat
              icon={<Package className="w-3.5 h-3.5" />}
              label="Model software"
              value={
                capability?.image_present === true
                  ? 'Downloaded'
                  : capability?.image_present === false
                  ? 'Not downloaded yet'
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
              label="Downloaded models"
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
                const rowLoading = !!server && isLoadingState(server.state);
                const rowError = server?.state === 'error';
                const rowStopping = stoppingModality === rowModality;
                const rowLogs = logsByModality[rowModality] ?? [];
                const rowLogsOpen = openLogsFor === rowModality;
                const name = shortModelName(model.repo_id);

                if (!server) {
                  const blocker = startBlocker(model.repo_id, metal);
                  const isChecking = checkingModelId === model.repo_id;
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
                          {blocker && ' · Needs a Mac with an Apple chip'}
                        </p>
                      </div>

                      <div className="flex items-center gap-1.5 shrink-0">
                        <button
                          type="button"
                          onClick={() => handleLaunchRequest(model.repo_id)}
                          disabled={anyServerBusy || !!blocker || !!checkingModelId}
                          title={blocker ?? undefined}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-md-outline-variant bg-md-surface-container-low hover:bg-md-surface-container-high disabled:opacity-40 text-[11px] font-semibold text-md-on-surface transition-colors cursor-pointer"
                        >
                          {isChecking ? (
                            <Loader2 className="w-3 h-3 animate-spin" />
                          ) : (
                            <Play className="w-3 h-3 fill-current" />
                          )}
                          {isChecking ? 'Checking…' : 'Start'}
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDeleteWeights(model.repo_id, model.size_bytes)}
                          title={`Delete ${name} from this Mac`}
                          className="p-1.5 rounded-lg border border-md-outline-variant text-md-on-surface-variant hover:text-md-error hover:border-md-error/50 transition-colors cursor-pointer"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  );
                }

                // The headline sits in the row and the reason in the box under it;
                // a reason that repeats the headline is shown once.
                const errorText = rowError ? server.error ?? null : null;
                const showMessage =
                  rowStopping || !errorText || !errorText.startsWith(server.message.replace(/\.$/, ''));

                return (
                  <div
                    key={model.repo_id}
                    className={`rounded-2xl border shadow-xs transition-colors ${
                      rowError
                        ? 'bg-md-surface border-rose-300 dark:border-rose-800/80'
                        : 'bg-md-primary-container/20 border-md-primary/50'
                    } ${isStale ? 'opacity-60' : ''}`}
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
                              isStale={isStale}
                            />
                            <span className="text-[10px] font-bold uppercase tracking-wider text-md-on-surface-variant">
                              {runtimes.find((r) => r.modality === rowModality)?.label ??
                                rowModality}
                            </span>
                          </div>
                          {(showMessage || model.size_bytes > 0) && (
                            <p className="text-[11px] text-md-on-surface-variant mt-1 leading-relaxed">
                              {rowStopping
                                ? `Stopping ${name} and unloading it from memory…`
                                : showMessage
                                ? server.message
                                : null}
                              {model.size_bytes > 0 &&
                                `${showMessage ? ' · ' : ''}${formatBytes(model.size_bytes)} on disk`}
                            </p>
                          )}
                        </div>

                        <div className="flex items-center gap-1.5 shrink-0">
                          {rowError ? (
                            <>
                              <button
                                type="button"
                                onClick={() => handleLaunchRequest(model.repo_id, rowModality)}
                                disabled={rowStopping || !!checkingModelId || isDeploying}
                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-md-outline-variant bg-md-surface-container-low hover:bg-md-surface-container-high disabled:opacity-40 text-[11px] font-semibold text-md-on-surface transition-colors cursor-pointer"
                              >
                                {checkingModelId === model.repo_id ? (
                                  <Loader2 className="w-3 h-3 animate-spin" />
                                ) : (
                                  <Play className="w-3 h-3 fill-current" />
                                )}
                                {checkingModelId === model.repo_id ? 'Checking…' : 'Try again'}
                              </button>
                              <button
                                type="button"
                                onClick={() => handleDismiss(rowModality, model.repo_id)}
                                disabled={rowStopping}
                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-md-outline-variant text-[11px] font-semibold text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high disabled:opacity-40 transition-colors cursor-pointer"
                              >
                                {rowStopping && <Loader2 className="w-3 h-3 animate-spin" />}
                                Dismiss
                              </button>
                            </>
                          ) : (
                            <button
                              type="button"
                              onClick={() => handleStopServer(rowModality, model.repo_id)}
                              disabled={rowStopping}
                              className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl bg-md-error hover:opacity-90 disabled:opacity-50 text-md-on-error text-xs font-semibold shadow-xs transition-opacity cursor-pointer"
                            >
                              {rowStopping ? (
                                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                              ) : (
                                <Square className="w-3.5 h-3.5 fill-current" />
                              )}
                              <span>{rowStopping ? 'Stopping…' : 'Stop'}</span>
                            </button>
                          )}
                        </div>
                      </div>

                      {errorText && (
                        <div className="p-3 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-300 dark:border-rose-800/80 text-rose-900 dark:text-rose-100 text-[11px] leading-relaxed">
                          {errorText}
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
                            <span>Details</span>
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
                                <div className="text-md-on-surface-variant italic font-sans">
                                  Nothing here yet.
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
              No models downloaded yet. Start one from the list below.
            </p>
          )}

          {/* GPU models are downloaded by the desktop app into the Mac's own
              Hugging Face folder, which this list cannot see, so the list does not
              pretend to be complete. */}
          {metal?.supported && (
            <p className="text-[11px] text-md-on-surface-variant">
              Models that run on your Mac's GPU are stored separately and aren't listed here.
            </p>
          )}
        </section>

        {/* ---------------------------------------------------------------- Catalog */}
        <section className="space-y-3">
          <SectionHeading
            icon={<Layers className="w-3.5 h-3.5 text-md-primary" />}
            title="Find a model"
            note="Models anyone can download, from Hugging Face"
          />

          <HuggingFaceCatalog
            mode="deploy"
            onDeployVLLM={(modelId) => handleLaunchRequest(modelId)}
            activeVllmModelId={activeModelOf(statuses[modality])}
            isVllmLoading={isBrowsedServerBusy}
            cachedModelIds={cachedIds}
            availableMemoryGB={(capability?.total_memory_mb ?? 0) / 1024}
            metalSupported={metal?.supported ?? false}
            metalDetail={metal?.detail ?? null}
            startingModelId={checkingModelId}
            runtimes={runtimes}
            modality={modality}
            onSelectModality={onSelectModality}
          />
        </section>
      </div>

      {pendingLaunch && (
        <VLLMLaunchDialog
          modelId={pendingLaunch}
          modality={launchModality}
          capability={capability}
          replacingModelId={activeModelOf(statuses[launchModality])}
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

/** States in which a server is holding a model, so its row owns the status card. */
const SERVER_HELD_STATES = ['ready', 'pulling_image', 'starting_container', 'loading', 'error'];

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
  /** Kayak is unreachable, so the state shown is what was last heard. */
  isStale?: boolean;
}> = ({ isReady, isLoading, isError, isStopping, isStale }) => {
  // Both transitions get their own state with a spinner inside the pill: a
  // server winding down is not "online", and one coming up is not "serving".
  const checking = !!isStale && !isStopping;
  const inTransition = isStopping || isLoading || checking;
  return (
    <span
      className={`inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border ${
        checking
          ? 'bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant'
          : isStopping || isLoading
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
        : checking
        ? 'CHECKING…'
        : isLoading
        ? 'STARTING'
        : isReady
        ? 'ONLINE'
        : isError
        ? 'FAILED'
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
