/**
 * Which models the Apple GPU can serve.
 *
 * The Metal backend runs MLX weights, which on Hugging Face are published under
 * one organisation. Deriving this from the repository id keeps it a rule rather
 * than a list, so no component has to be edited when models are published.
 */

/** Prefix that narrows a Hugging Face search to MLX weights. */
export const MLX_SEARCH_PREFIX = 'mlx-community/';

/**
 * Reports whether a repository can run on the Apple GPU.
 *
 * Matched case-insensitively because Hugging Face resolves repository owners
 * that way, so the same model can be referred to with either casing.
 */
export function isMlxModel(modelId: string): boolean {
  const parts = modelId.split('/');
  if (parts.length !== 2) return false;
  const [org, name] = parts;
  return org.toLowerCase() === 'mlx-community' && name.trim().length > 0;
}
