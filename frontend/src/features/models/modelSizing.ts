/**
 * Rough sizing for Hugging Face models, from their repository id.
 *
 * A local deployment downloads tens of gigabytes and then either fits in memory or
 * dies partway through loading. The repository id almost always carries the parameter
 * count and the quantization, which is enough to say "this will not fit" before the
 * download starts rather than after it fails.
 *
 * Containers on a Mac run on the CPU, inside the memory Docker Desktop's VM has, so
 * that is the memory a model has to fit in.
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
  /** Weights, the runtime, and the smallest useful KV cache: what starting it takes. */
  requiredGB: number;
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

/** The smallest KV cache vLLM is given, which a model needs to start at all. */
const MIN_KV_CACHE_GB = 1;

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
    requiredGB: round(weightsGB + CPU_RUNTIME_OVERHEAD_GB + MIN_KV_CACHE_GB),
  };
}

export type FitVerdict = 'fits' | 'tight' | 'too-large' | 'unknown';

/**
 * Compares an estimate against the memory a container will have.
 *
 * @param estimate Sizing for the model, or null when it could not be determined.
 * @param availableGB Memory the container may use, or 0 when Docker did not say.
 */
export function judgeFit(estimate: ModelSizeEstimate | null, availableGB: number): FitVerdict {
  if (!estimate || availableGB <= 0) return 'unknown';
  if (estimate.requiredGB > availableGB) return 'too-large';
  if (estimate.requiredGB > availableGB * 0.85) return 'tight';
  return 'fits';
}

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

function round(value: number): number {
  return Math.round(value * 10) / 10;
}
