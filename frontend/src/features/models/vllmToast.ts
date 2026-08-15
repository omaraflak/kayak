/**
 * Visibility rule for the floating vLLM status toast.
 *
 * The toast exists to narrate a deployment this tab is watching. Deriving it from
 * the server state alone made it permanent: a tab opened (or refreshed) while a
 * model was serving showed "ready" forever, because the server is simply always in
 * that state. Visibility therefore also depends on what this tab has witnessed.
 */

/** How long the "ready" confirmation stays on screen before hiding itself. */
export const READY_TOAST_TIMEOUT_MS = 10_000;

export interface VllmToastInputs {
  /** Current server state, or undefined while nothing has been fetched yet. */
  state: string | null | undefined;
  /** The user closed the toast by hand. */
  dismissed: boolean;
  /** This tab observed the deployment in progress (not just its end state). */
  sawDeployment: boolean;
  /** The 10s confirmation window after reaching ready has elapsed. */
  readyExpired: boolean;
}

export function shouldShowVllmToast({
  state,
  dismissed,
  sawDeployment,
  readyExpired,
}: VllmToastInputs): boolean {
  if (!state || state === 'idle' || state === 'stopped') return false;
  if (dismissed) return false;

  if (state === 'ready') {
    // A refresh lands here with sawDeployment=false: the model was already
    // serving, there is nothing to announce. After a live deployment the ready
    // confirmation shows briefly and then retires itself.
    return sawDeployment && !readyExpired;
  }

  // Deployments in progress and errors always show.
  return true;
}
