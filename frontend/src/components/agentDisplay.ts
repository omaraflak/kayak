/** Shared presentation helpers for agent profiles. */

/** Picks an emoji for a model's provider prefix. */
export function getProviderIcon(model: string): string {
  if (model.startsWith('gemini/')) return '✨';
  if (model.startsWith('openai/')) return '🧠';
  if (model.startsWith('anthropic/')) return '⚡';
  if (model.startsWith('ollama/')) return '🦙';
  if (model.startsWith('vllm/')) return '🚀';
  if (model.startsWith('huggingface/') || model.startsWith('hf/')) return '🤗';
  return '🤖';
}
