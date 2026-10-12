import React, { useMemo, useState } from 'react';
import { HostCapability, Modality, VLLMDeployRequest } from '../../types';
import { estimateModelSize, judgeFit, maxWorkingMemoryGB } from './modelSizing';
import { AlertTriangle, Cpu, Rocket, ShieldAlert, X, Zap } from 'lucide-react';

/**
 * Confirms and configures a local deployment.
 *
 * Offers what the runtime honours -- context length, KV cache, precision, and the
 * container's memory and cores -- so that after an out-of-memory failure a user can
 * ask for less. It also stands between the user and two irreversible-feeling side
 * effects: a very large download, and replacing whatever model is currently serving.
 */

const DTYPE_OPTIONS = [
  { value: 'auto', label: 'Auto', hint: 'bfloat16, which the CPU runtime uses unless told otherwise.' },
  { value: 'bfloat16', label: 'bfloat16', hint: 'Half the memory of full precision.' },
  { value: 'float32', label: 'float32', hint: 'Full precision, at twice the memory of bfloat16.' },
];

const CONTEXT_PRESETS = [
  { value: null, label: 'Model default' },
  { value: 4096, label: '4K' },
  { value: 8192, label: '8K' },
  { value: 16384, label: '16K' },
  { value: 32768, label: '32K' },
  { value: 65536, label: '64K' },
  { value: 131072, label: '128K' },
];

interface VLLMLaunchDialogProps {
  modelId: string;
  /** Which local server this starts. Only text generation has a GPU alternative. */
  modality: Modality;
  /**
   * Settings the target runtime actually honours, from its descriptor.
   *
   * Offering the rest is not merely untidy: a transcription model has no context
   * window and no KV cache, so those controls promise a effect they cannot have.
   * Undefined means "everything", which is what the vLLM path has always shown.
   */
  tunableFields?: string[];
  capability: HostCapability | null;
  /** The model currently serving, when starting this one would replace it. */
  replacingModelId?: string | null;
  /** True when this model's weights are already downloaded. */
  isCached: boolean;
  onCancel: () => void;
  onLaunch: (request: VLLMDeployRequest) => void;
}

export const VLLMLaunchDialog: React.FC<VLLMLaunchDialogProps> = ({
  modelId,
  modality,
  tunableFields,
  capability,
  replacingModelId,
  isCached,
  onCancel,
  onLaunch,
}) => {
  const [maxModelLen, setMaxModelLen] = useState<number | null>(null);
  const [dtype, setDtype] = useState('auto');
  const [trustRemoteCode, setTrustRemoteCode] = useState(false);
  /**
   * Whether the target runtime honours a setting.
   *
   * Undefined means unrestricted, which keeps the vLLM path exactly as it was
   * before other runtimes existed.
   */
  const offers = (field: string): boolean => !tunableFields || tunableFields.includes(field);

  const systemMemoryGB = (capability?.total_memory_mb ?? 0) / 1024;
  const totalCpus = capability?.total_cpus ?? 0;
  const estimate = useMemo(() => estimateModelSize(modelId), [modelId]);

  // Container-level allocation. Everything Docker has is the recommended default
  // for model serving; lowering either protects the rest of the machine.
  const maxContainerMemoryGB = Math.max(1, Math.floor(systemMemoryGB));
  const [containerMemoryGB, setContainerMemoryGB] = useState<number | null>(null);
  const [containerCpus, setContainerCpus] = useState<number | null>(null);
  const effectiveContainerMemoryGB = Math.min(
    containerMemoryGB ?? maxContainerMemoryGB,
    maxContainerMemoryGB
  );
  const effectiveContainerCpus = Math.min(containerCpus ?? totalCpus, Math.max(totalCpus, 1));
  // Judged against the container's allocation, so lowering it warns before the
  // start fails rather than after.
  const fit = judgeFit(estimate, systemMemoryGB > 0 ? effectiveContainerMemoryGB : 0);

  // Sized from the container's allocation by the server; adjustable here because it
  // is the setting that decides whether a CPU deployment starts at all.
  const [kvCacheGB, setKvCacheGB] = useState<number | null>(null);
  const memoryCeilingGB = useMemo(
    () =>
      maxWorkingMemoryGB(
        systemMemoryGB > 0 ? effectiveContainerMemoryGB : systemMemoryGB,
        estimate
      ),
    [systemMemoryGB, effectiveContainerMemoryGB, estimate]
  );
  const effectiveKvCacheGB = Math.min(
    kvCacheGB ?? capability?.default_cpu_kvcache_gb ?? 1,
    memoryCeilingGB
  );

  const handleLaunch = () => {
    onLaunch({
      model_id: modelId,
      max_model_len: maxModelLen,
      dtype,
      trust_remote_code: trustRemoteCode,
      cpu_kvcache_space_gb: offers('cpu_kvcache_space_gb') ? effectiveKvCacheGB : null,
      // "All of it" is expressed as no limit at all, so a machine whose Docker
      // allocation grows later is not silently pinned to today's figure.
      memory_limit_gb:
        systemMemoryGB > 0 && effectiveContainerMemoryGB < maxContainerMemoryGB
          ? effectiveContainerMemoryGB
          : null,
      cpu_limit:
        totalCpus > 0 && effectiveContainerCpus < totalCpus ? effectiveContainerCpus : null,
      // Settings picked here are deliberate: applying them to a model that is
      // already up requires the restart a bare deploy deliberately avoids.
      force_restart: true,
    });
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-md-scrim/60 backdrop-blur-xs animate-fade-in font-sans">
      <div className="bg-md-surface-container-low rounded-2xl border border-md-outline-variant shadow-2xl w-full max-w-lg overflow-hidden flex flex-col max-h-[88vh] transition-colors">
        <div className="px-6 py-4 border-b border-md-outline-variant flex items-start justify-between bg-md-surface-container shrink-0">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-8 h-8 rounded-xl bg-md-primary-container text-md-on-primary-container border border-md-outline-variant flex items-center justify-center shadow-2xs shrink-0">
              <Rocket className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <h2 className="text-sm font-bold text-md-on-surface">Start local server</h2>
              <p className="text-[11px] text-md-on-surface-variant font-mono truncate" title={modelId}>
                {modelId}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high transition-colors cursor-pointer shrink-0"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-6 space-y-5 overflow-y-auto flex-1 bg-md-surface">
          {/* What this machine is about to do */}
          <div className="space-y-2">
            {replacingModelId && (
              <Notice tone="warning" icon={<AlertTriangle className="w-3.5 h-3.5" />}>
                This stops the server currently running{' '}
                <code className="font-mono font-semibold">{replacingModelId}</code>. Conversations
                using it will fail until the new model is ready.
              </Notice>
            )}

            {modality === 'text' && (
              <Notice tone="neutral" icon={<Cpu className="w-3.5 h-3.5" />}>
                Runs on the CPU inside Docker, which is slow for anything but small models.
                On an Apple Silicon Mac, MLX models run on the GPU instead.
              </Notice>
            )}

            {fit === 'too-large' && estimate && (
              <Notice tone="danger" icon={<AlertTriangle className="w-3.5 h-3.5" />}>
                This model needs roughly {estimate.requiredGB} GB but the container has{' '}
                {effectiveContainerMemoryGB} GB. It will most likely fail to load — try a
                quantized build or a smaller model, or give Docker Desktop more memory.
              </Notice>
            )}

            {fit === 'tight' && estimate && (
              <Notice tone="warning" icon={<AlertTriangle className="w-3.5 h-3.5" />}>
                About {estimate.requiredGB} GB needed against {effectiveContainerMemoryGB} GB
                available. It may load, but a shorter context length gives it room.
              </Notice>
            )}

            {!isCached && (
              <Notice tone="neutral" icon={<Zap className="w-3.5 h-3.5" />}>
                These weights are not on this Mac yet
                {estimate ? `, so roughly ${estimate.weightsGB} GB will be downloaded` : ''}. The
                first start takes as long as the download does.
              </Notice>
            )}
          </div>

          {offers('max_model_len') && (
          <Field
            label="Context length"
            hint="How much of a conversation the model can take into account at once. Shorter needs less memory, and is the first thing to reduce if a model will not start."
          >
            <div className="flex flex-wrap gap-1.5">
              {CONTEXT_PRESETS.map((preset) => (
                <button
                  key={preset.label}
                  type="button"
                  onClick={() => setMaxModelLen(preset.value)}
                  className={`px-3 py-1.5 rounded-lg text-[11px] font-semibold border transition-colors cursor-pointer ${
                    maxModelLen === preset.value
                      ? 'bg-md-primary text-md-on-primary border-md-primary shadow-2xs'
                      : 'bg-md-surface-container-low text-md-on-surface border-md-outline-variant hover:bg-md-surface-container-high'
                  }`}
                >
                  {preset.label}
                </button>
              ))}
            </div>
          </Field>
          )}

          {offers('dtype') && (
          <Field label="Weight precision" hint={DTYPE_OPTIONS.find((o) => o.value === dtype)?.hint}>
            <div className="flex gap-1.5">
              {DTYPE_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setDtype(option.value)}
                  className={`px-3 py-1.5 rounded-lg text-[11px] font-semibold border transition-colors cursor-pointer ${
                    dtype === option.value
                      ? 'bg-md-primary text-md-on-primary border-md-primary shadow-2xs'
                      : 'bg-md-surface-container-low text-md-on-surface border-md-outline-variant hover:bg-md-surface-container-high'
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </Field>
          )}

          {offers('memory_limit_gb') && systemMemoryGB > 0 && (
            <Field
              label={`Container memory — ${effectiveContainerMemoryGB} GB${
                effectiveContainerMemoryGB >= maxContainerMemoryGB ? ' (all of it)' : ''
              }`}
              hint={
                'RAM the model container is allowed to use. Giving it everything is the ' +
                'right choice while serving; lower it to keep room for agent sandboxes ' +
                'and anything else running in Docker. Docker Desktop caps its own total ' +
                'memory independently of the machine — raise it under Settings > ' +
                'Resources > Memory if the maximum here looks low.'
              }
            >
              <input
                type="range"
                min={1}
                max={maxContainerMemoryGB}
                step={1}
                value={effectiveContainerMemoryGB}
                onChange={(event) =>
                  setContainerMemoryGB(Number.parseInt(event.target.value, 10))
                }
                disabled={maxContainerMemoryGB <= 1}
                className="w-full accent-md-primary cursor-pointer disabled:opacity-50"
              />
              <div className="flex items-center justify-between text-[10px] text-md-on-surface-variant font-mono pt-1">
                <span>1 GB</span>
                <span>{maxContainerMemoryGB} GB available to Docker</span>
              </div>
            </Field>
          )}

          {offers('cpu_limit') && totalCpus > 0 && (
            <Field
              label={`Container CPU — ${effectiveContainerCpus} core${
                effectiveContainerCpus === 1 ? '' : 's'
              }${effectiveContainerCpus >= totalCpus ? ' (all of them)' : ''}`}
              hint="Cores the model may use. More cores generate faster; lower it to keep the machine responsive while a model is serving."
            >
              <input
                type="range"
                min={1}
                max={totalCpus}
                step={1}
                value={effectiveContainerCpus}
                onChange={(event) => setContainerCpus(Number.parseInt(event.target.value, 10))}
                disabled={totalCpus <= 1}
                className="w-full accent-md-primary cursor-pointer disabled:opacity-50"
              />
              <div className="flex items-center justify-between text-[10px] text-md-on-surface-variant font-mono pt-1">
                <span>1 core</span>
                <span>{totalCpus} cores available</span>
              </div>
            </Field>
          )}

          {offers('cpu_kvcache_space_gb') && (
            <Field
              label={`Conversation memory — ${effectiveKvCacheGB} GB`}
              hint={
                'Space set aside for the model to remember the conversation as it runs. ' +
                'More lets it handle longer conversations at once; the slider stops where ' +
                'the container runs out of room, because asking for more than that ' +
                'prevents the model from starting at all.'
              }
            >
              <input
                type="range"
                min={1}
                // Hard-capped: past this the launch cannot succeed, so it is not
                // offered rather than left to fail several minutes later.
                max={memoryCeilingGB}
                step={1}
                value={effectiveKvCacheGB}
                onChange={(event) => setKvCacheGB(Number.parseInt(event.target.value, 10))}
                disabled={memoryCeilingGB <= 1}
                className="w-full accent-md-primary cursor-pointer disabled:opacity-50"
              />
              <div className="flex items-center justify-between text-[10px] text-md-on-surface-variant font-mono pt-1">
                <span>1 GB</span>
                <span>
                  {memoryCeilingGB} GB max
                  {systemMemoryGB > 0 &&
                    ` · ${effectiveContainerMemoryGB} GB allocated to the container`}
                </span>
              </div>
            </Field>
          )}

          <div className="space-y-2.5 pt-1">
            {offers('trust_remote_code') && (
              <Toggle
                checked={trustRemoteCode}
                onChange={setTrustRemoteCode}
                label="Trust remote code"
                danger
                hint="Runs Python published in the model repository inside the container, with your Hugging Face token in its environment. Only needed for architectures the runtime does not ship support for."
              />
            )}
          </div>
        </div>

        <div className="px-6 py-3.5 border-t border-md-outline-variant bg-md-surface-container flex items-center justify-between shrink-0 gap-3">
          {trustRemoteCode ? (
            <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-amber-800 dark:text-amber-200">
              <ShieldAlert className="w-3.5 h-3.5 shrink-0" />
              Remote code execution enabled
            </span>
          ) : (
            <span className="text-[11px] text-md-on-surface-variant">
              Weights cache to <code className="font-mono">data/huggingface_cache/</code>
            </span>
          )}

          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={onCancel}
              className="px-4 py-2 rounded-xl border border-md-outline-variant text-xs font-semibold text-md-on-surface bg-md-surface-container-low hover:bg-md-surface-container-high transition-colors cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleLaunch}
              className="px-5 py-2 rounded-xl bg-md-primary hover:opacity-90 text-md-on-primary text-xs font-bold inline-flex items-center gap-1.5 shadow-xs transition-opacity cursor-pointer"
            >
              <Rocket className="w-3.5 h-3.5" />
              <span>Start server</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

const NOTICE_TONES = {
  neutral: 'bg-md-surface-container-high border-md-outline-variant text-md-on-surface-variant',
  warning:
    'bg-amber-50 dark:bg-amber-950/40 border-amber-300 dark:border-amber-800/80 text-amber-900 dark:text-amber-100',
  danger:
    'bg-rose-50 dark:bg-rose-950/40 border-rose-300 dark:border-rose-800/80 text-rose-900 dark:text-rose-100',
};

const Notice: React.FC<{
  tone: keyof typeof NOTICE_TONES;
  icon: React.ReactNode;
  children: React.ReactNode;
}> = ({ tone, icon, children }) => (
  <div className={`flex items-start gap-2 p-3 rounded-xl border text-[11px] leading-relaxed ${NOTICE_TONES[tone]}`}>
    <span className="shrink-0 mt-0.5">{icon}</span>
    <p>{children}</p>
  </div>
);

const Field: React.FC<{ label: string; hint?: string; children: React.ReactNode }> = ({
  label,
  hint,
  children,
}) => (
  <div className="space-y-1.5">
    <label className="text-[11px] font-bold uppercase tracking-wider text-md-on-surface block">
      {label}
    </label>
    {hint && <p className="text-[11px] text-md-on-surface-variant leading-relaxed">{hint}</p>}
    <div className="pt-0.5">{children}</div>
  </div>
);

const Toggle: React.FC<{
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  hint: string;
  danger?: boolean;
}> = ({ checked, onChange, label, hint, danger }) => (
  <button
    type="button"
    onClick={() => onChange(!checked)}
    className={`w-full text-left p-3 rounded-xl border transition-colors cursor-pointer flex items-start gap-3 ${
      checked
        ? danger
          ? 'bg-amber-50 dark:bg-amber-950/40 border-amber-300 dark:border-amber-800/80'
          : 'bg-md-primary-container/40 border-md-primary/50'
        : 'bg-md-surface border-md-outline-variant hover:bg-md-surface-container'
    }`}
  >
    <span
      className={`mt-0.5 w-8 h-4.5 rounded-full shrink-0 relative transition-colors ${
        checked ? 'bg-md-primary' : 'bg-md-outline-variant'
      }`}
      style={{ height: '18px', width: '32px' }}
    >
      <span
        className="absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white shadow-xs transition-all"
        style={{ left: checked ? '15px' : '3px' }}
      />
    </span>
    <span className="min-w-0">
      <span className="text-xs font-semibold text-md-on-surface block">{label}</span>
      <span className="text-[11px] text-md-on-surface-variant leading-relaxed block mt-0.5">
        {hint}
      </span>
    </span>
  </button>
);
