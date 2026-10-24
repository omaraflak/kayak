import { MetalStatus, VLLMDeploymentProgress } from '../../types';
import { isMlxModel } from './metalModels';

/**
 * Small rules about local model servers that several surfaces share.
 *
 * Kept free of React so they can be tested on their own, and so the Local Models
 * page, the toast, the Audio page and the chat composer cannot each word the same
 * state differently.
 */

/** States in which a server is coming up rather than serving. */
const STARTING_STATES: readonly string[] = ['pulling_image', 'starting_container', 'loading'];

/**
 * The part of a repository id a person recognises: "Qwen2.5-Coder-7B-Instruct"
 * rather than "Qwen/Qwen2.5-Coder-7B-Instruct".
 */
export function shortModelName(modelId: string | null | undefined): string {
  if (!modelId) return 'The model';
  return modelId.replace(/\/+$/, '').split('/').pop() || modelId;
}

/**
 * Whether a status change begins a new start, whose log should begin empty.
 *
 * The log used to keep growing across starts, so a model's Details showed the
 * previous attempt's failure above -- and easily mistaken for -- the current one.
 */
export function shouldResetLogs(
  previous: VLLMDeploymentProgress | null | undefined,
  next: VLLMDeploymentProgress
): boolean {
  if (!STARTING_STATES.includes(next.state)) return false;
  if (!previous) return true;
  return !STARTING_STATES.includes(previous.state) || previous.model_id !== next.model_id;
}

/**
 * The headline for a server that failed: a model that never started, or one that
 * was answering and then stopped. Calling the second a failed start read as a lie.
 */
export function failureTitle(status: VLLMDeploymentProgress | null | undefined): string {
  const name = shortModelName(status?.model_id);
  return status?.crashed ? `${name} stopped unexpectedly` : `${name} couldn't start`;
}

/** "12 s", "3 min", "1 h 5 min": how long something has been going, at a glance. */
export function formatElapsed(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/** Said when a GPU-only model is offered on a Mac that has no GPU to give it. */
export const APPLE_CHIP_REQUIRED =
  'This model only runs on Macs with Apple chips (M1 or later). Pick a model without ' +
  '“mlx-community” in its name.';

/**
 * Why this Mac cannot start a model at all, or null when it can try.
 *
 * Models published by mlx-community only run on the Apple GPU. Offered on a Mac
 * that has none -- an Intel Mac, or one without the desktop app -- Start used to be
 * enabled and failed with a sentence about Metal and launchers.
 */
export function startBlocker(
  modelId: string,
  metal: Pick<MetalStatus, 'supported' | 'detail'> | null | undefined
): string | null {
  if (!isMlxModel(modelId) || metal?.supported) return null;
  return metal?.detail || APPLE_CHIP_REQUIRED;
}
