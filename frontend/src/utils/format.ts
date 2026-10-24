/** Formats a byte count for display: 512 B, 1.5 MB, 15 GB. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** exponent;
  return `${value >= 10 || exponent === 0 ? Math.round(value) : value.toFixed(1)} ${units[exponent]}`;
}

/**
 * The short name of a model, as a person would say it.
 *
 * "vllm/Qwen/Qwen3-0.6B" -> "Qwen3-0.6B", "gemini/gemini-3.6-flash" -> "gemini-3.6-flash".
 * The provider and organisation prefixes are how Kayak routes a model, not how
 * anyone refers to it.
 */
export function shortModelName(model: string): string {
  return model.split('/').pop() || model;
}
