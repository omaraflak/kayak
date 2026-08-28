/**
 * Reloads the page when the server is offering a newer build of the app.
 *
 * After a Kayak update, browsers and the launcher's webview kept showing the
 * previous version's UI from cache: the app shell went out without cache
 * headers, so clients reused it without asking the server. The API underneath
 * answered with the new version number, which made the mismatch invisible
 * until a feature was missing. Cache headers stop the shell from going stale
 * in the first place; this guard is the recovery path for a shell that
 * already is.
 *
 * Detection is by asset URL rather than a version string: Vite fingerprints
 * the entry script per build, so the shell the server serves and the shell
 * that is running can be compared directly, with nothing to keep in step.
 */

const RELOADED_FOR_KEY = 'kayak-shell-reloaded-for';

/** Pulls the fingerprinted entry-script path out of a served index.html. */
export function extractEntryScript(html: string): string | null {
  return html.match(/\/assets\/index-[^"']+\.js/)?.[0] ?? null;
}

/**
 * Whether a page running one entry script should reload for the one served.
 *
 * @param running Full URL of the script this page is executing.
 * @param served Path of the entry script in the server's current index.html.
 * @param alreadyReloadedFor The served script a reload was last attempted for.
 *   Reloading at most once per served build is what stops a server that keeps
 *   answering unexpectedly from putting the page in a reload loop.
 */
export function shouldReloadForServedShell(
  running: string | null,
  served: string | null,
  alreadyReloadedFor: string | null
): boolean {
  if (!running || !served) return false;
  if (running.endsWith(served)) return false;
  return alreadyReloadedFor !== served;
}

/** The fingerprinted entry script this page is actually running. */
function runningEntryScript(): string | null {
  const script = document.querySelector<HTMLScriptElement>(
    'script[type="module"][src*="/assets/"]'
  );
  return script?.src ?? null;
}

async function checkForNewShell(): Promise<void> {
  let served: string | null = null;
  try {
    const response = await fetch('/', { cache: 'no-store' });
    if (!response.ok) return;
    served = extractEntryScript(await response.text());
  } catch {
    return; // offline or the backend is restarting; check again next time
  }

  let alreadyReloadedFor: string | null = null;
  try {
    alreadyReloadedFor = sessionStorage.getItem(RELOADED_FOR_KEY);
  } catch {
    return; // storage unavailable: skip rather than risk a reload loop
  }

  if (!shouldReloadForServedShell(runningEntryScript(), served, alreadyReloadedFor)) {
    return;
  }

  try {
    sessionStorage.setItem(RELOADED_FOR_KEY, served as string);
  } catch {
    return;
  }
  window.location.reload();
}

/** Checks on startup and whenever the page comes back into view. */
export function watchForNewAppShell(): void {
  checkForNewShell();
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) checkForNewShell();
  });
}
