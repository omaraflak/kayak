import React, { useState } from 'react';
import { Cpu, Loader2, Play, Square, AlertCircle } from 'lucide-react';
import { MetalStatus } from '../../types';

interface MetalPanelProps {
  status: MetalStatus;
  onStart: (modelId: string) => Promise<void>;
  onStop: () => Promise<void>;
}

/**
 * Models the Metal backend can serve.
 *
 * A short curated list rather than the Hugging Face catalogue: Metal runs MLX
 * weights, which are a small subset of what is published, and browsing the full
 * catalogue would mostly surface repositories that fail on selection.
 */
const SUGGESTED = [
  { id: 'mlx-community/Llama-3.2-3B-Instruct-4bit', note: '3B · about 2 GB' },
  { id: 'mlx-community/Qwen2.5-7B-Instruct-4bit', note: '7B · about 4 GB' },
  { id: 'mlx-community/Qwen2.5-Coder-14B-Instruct-4bit', note: '14B · about 8 GB' },
];

const BUSY_STATES: MetalStatus['state'][] = ['installing', 'starting'];

/** Sentence describing the current state, in the terms a user cares about. */
function describe(status: MetalStatus): string {
  switch (status.state) {
    case 'installing':
      // The first run downloads a Python runtime plus several gigabytes of
      // wheels, which is slow enough to look stuck without saying so.
      return 'Setting up Metal support. This happens once and takes a few minutes.';
    case 'starting':
      return 'Loading the model onto the GPU.';
    case 'ready':
      return `Serving ${status.model} on the GPU.`;
    case 'error':
      return status.error ?? 'Metal inference stopped unexpectedly.';
    default:
      return status.installed
        ? 'Ready to serve a model on the GPU.'
        : 'Metal support will be installed the first time you start a model.';
  }
}

export const MetalPanel: React.FC<MetalPanelProps> = ({ status, onStart, onStop }) => {
  const [selected, setSelected] = useState(SUGGESTED[0].id);
  const [pending, setPending] = useState(false);

  const busy = pending || BUSY_STATES.includes(status.state);
  const running = status.state === 'ready' || BUSY_STATES.includes(status.state);

  const act = async (action: () => Promise<void>) => {
    setPending(true);
    try {
      await action();
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-[11px] text-md-on-surface-variant leading-relaxed">
        {describe(status)}
      </p>

      {status.state === 'error' && status.error && (
        <div className="p-3.5 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-300 dark:border-rose-800/80 text-rose-900 dark:text-rose-100 text-[11px] leading-relaxed flex items-start gap-2">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <p className="break-words">{status.error}</p>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <select
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
          disabled={running}
          className="flex-1 min-w-[16rem] text-[11px] font-mono rounded-lg border border-md-outline-variant bg-md-surface-container px-2.5 py-2 text-md-on-surface disabled:opacity-50"
        >
          {SUGGESTED.map((model) => (
            <option key={model.id} value={model.id}>
              {model.id} — {model.note}
            </option>
          ))}
        </select>

        {running ? (
          <button
            onClick={() => act(onStop)}
            disabled={pending}
            className="inline-flex items-center gap-1.5 text-[11px] font-bold rounded-lg px-3 py-2 bg-md-surface-container-high text-md-on-surface hover:brightness-95 disabled:opacity-50"
          >
            <Square className="w-3.5 h-3.5" />
            Stop
          </button>
        ) : (
          <button
            onClick={() => act(() => onStart(selected))}
            disabled={busy}
            className="inline-flex items-center gap-1.5 text-[11px] font-bold rounded-lg px-3 py-2 bg-md-primary text-md-on-primary hover:brightness-95 disabled:opacity-50"
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
            Start on GPU
          </button>
        )}
      </div>

      <p className="text-[10px] text-md-on-surface-variant leading-relaxed flex items-start gap-1.5">
        <Cpu className="w-3 h-3 shrink-0 mt-0.5" />
        <span>
          Runs outside Docker, directly on the Mac's GPU, so it is much faster than the
          CPU path. Only MLX builds work here — these are the same models, converted for
          Apple Silicon.
        </span>
      </p>
    </div>
  );
};
