/**
 * Rough sizing for Hugging Face models, from their repository id.
 *
 * A local deployment downloads tens of gigabytes and then either fits in memory or
 * dies partway through loading. The repository id almost always carries the parameter
 * count and the quantization, which is enough to say "this will not fit" before the
 * download starts rather than after it fails.
 *
 * These are estimates presented as estimates -- they inform a warning, never a block.
 */

/** Bytes per parameter for each weight format we can recognize. */
const BYTES_PER_PARAM = {
  full: 2, // fp16 / bf16, the default for an unquantized checkpoint
  fp8: 1,
  int4: 0.5,
} as const;

export type WeightFormat = keyof typeof BYTES_PER_PARAM;

export interface ModelSizeEstimate {
  /** Parameter count in billions, as advertised by the repository name. */
  parametersB: number;
  format: WeightFormat;
  /** Approximate size of the weights alone. */
  weightsGB: number;
  /**
   * Weights plus room for the KV cache, activations and CUDA graphs. vLLM reserves a
   * fraction of total VRAM rather than sizing to the model, so headroom is not optional.
   */
  requiredGB: number;
}

// Matches "7B", "1.5b", "70B" but not the "2.5" in "Qwen2.5-Coder": a bare decimal
// with no unit is a version number, not a parameter count.
const PARAMETER_PATTERN = /(?:^|[-_. ])(\d+(?:\.\d+)?)\s*b(?:$|[-_. ])/i;

const QUANTIZATION_MARKERS: { pattern: RegExp; format: WeightFormat }[] = [
  { pattern: /(^|[-_.])(awq|gptq|int4|4bit|w4a16|nf4|gguf)($|[-_.])/i, format: 'int4' },
  { pattern: /(^|[-_.])(fp8|int8|8bit|w8a8)($|[-_.])/i, format: 'fp8' },
];

/** Identifies the weight format a repository name advertises. */
export function detectWeightFormat(modelId: string): WeightFormat {
  for (const { pattern, format } of QUANTIZATION_MARKERS) {
    if (pattern.test(modelId)) return format;
  }
  return 'full';
}

/** Reads the advertised parameter count, in billions, from a repository id. */
export function parseParameterCount(modelId: string): number | null {
  // Only the model name carries the size; an organization like "01-ai" must not be
  // read as a parameter count.
  const name = modelId.includes('/') ? modelId.slice(modelId.lastIndexOf('/') + 1) : modelId;
  const match = PARAMETER_PATTERN.exec(name);
  if (!match) return null;

  const value = Number.parseFloat(match[1]);
  if (!Number.isFinite(value) || value <= 0 || value > 2000) return null;
  return value;
}

/**
 * Estimates what serving a model will cost in memory.
 *
 * Returns null when the repository id does not advertise a size, which is common
 * enough that "unknown" has to be a normal answer rather than a guess.
 */
export function estimateModelSize(modelId: string): ModelSizeEstimate | null {
  const parametersB = parseParameterCount(modelId);
  if (parametersB === null) return null;

  const format = detectWeightFormat(modelId);
  const weightsGB = parametersB * BYTES_PER_PARAM[format];

  return {
    parametersB,
    format,
    weightsGB: round(weightsGB),
    requiredGB: round(weightsGB * 1.2 + 2),
  };
}

export type FitVerdict = 'fits' | 'tight' | 'too-large' | 'cpu-only' | 'unknown';

/**
 * Compares an estimate against the memory the host actually has.
 *
 * @param estimate Sizing for the model, or null when it could not be determined.
 * @param availableGB Total accelerator memory, or 0 on a machine without a GPU.
 */
export function judgeFit(
  estimate: ModelSizeEstimate | null,
  availableGB: number,
  hasAccelerator = false
): FitVerdict {
  // A GPU whose size could not be read is not the same as no GPU. Kayak usually
  // runs in a container that cannot see the cards even when the daemon can hand
  // one over, so treating "no reading" as "no accelerator" called every model too
  // large on exactly the machines that could run them.
  if (availableGB <= 0) return hasAccelerator ? 'unknown' : 'cpu-only';
  if (!estimate) return 'unknown';
  if (estimate.requiredGB > availableGB) return 'too-large';
  if (estimate.requiredGB > availableGB * 0.85) return 'tight';
  return 'fits';
}

/**
 * Memory a CPU deployment consumes beyond the size of the weight files.
 *
 * Measured rather than guessed. On a 7.77 GB host, a model whose weights are 1.4 GB on
 * disk reported a 2.33 GB loaded footprint and left 2.5 GB free -- so about 4 GB went
 * to the runtime plus the gap between the file size and what the loaded model actually
 * occupies. Rounded up, because over-promising here is precisely what stops a
 * deployment from starting.
 */
const CPU_RUNTIME_OVERHEAD_GB = 4.5;

/**
 * The largest working-memory reservation a CPU deployment can actually use.
 *
 * vLLM refuses to start when the reservation exceeds the memory still free after the
 * weights are loaded, so this is a hard ceiling rather than a suggestion.
 *
 * @param totalMemoryGB Memory available to Docker.
 * @param estimate Sizing for the model, when its repository name reveals it.
 */
export function maxWorkingMemoryGB(
  totalMemoryGB: number,
  estimate: ModelSizeEstimate | null
): number {
  if (!Number.isFinite(totalMemoryGB) || totalMemoryGB <= 0) return 1;

  const weightsGB = estimate?.weightsGB ?? 0;
  const spare = Math.floor(totalMemoryGB - weightsGB - CPU_RUNTIME_OVERHEAD_GB);
  return Math.max(1, spare);
}

/** Formats a byte count for display, in the units people use for model weights. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** exponent;
  return `${value >= 10 || exponent === 0 ? Math.round(value) : value.toFixed(1)} ${units[exponent]}`;
}

function round(value: number): number {
  return Math.round(value * 10) / 10;
}
