import { describe, expect, it } from 'vitest';
import { extractEntryScript, shouldReloadForServedShell } from './appShellGuard';

/**
 * The guard exists because updated installs kept showing the previous
 * version's UI: the cached shell never asked the server whether it was
 * current. These tests pin the decision that recovers from that state.
 */

const SERVED_HTML = `<!doctype html>
<html><head>
<script type="module" crossorigin src="/assets/index-BcrUBDGj.js"></script>
<link rel="stylesheet" crossorigin href="/assets/index-D2u9Yv1x.css">
</head><body><div id="root"></div></body></html>`;

describe('extractEntryScript', () => {
  it('finds the fingerprinted entry script in a served shell', () => {
    expect(extractEntryScript(SERVED_HTML)).toBe('/assets/index-BcrUBDGj.js');
  });

  it('returns null for a page that is not the app shell', () => {
    expect(extractEntryScript('<html><body>502 Bad Gateway</body></html>')).toBeNull();
  });
});

describe('shouldReloadForServedShell', () => {
  const running = 'http://localhost:8000/assets/index-BwV7EThh.js';

  it('reloads a stale shell for the build the server now serves', () => {
    expect(
      shouldReloadForServedShell(running, '/assets/index-BcrUBDGj.js', null)
    ).toBe(true);
  });

  it('leaves a current shell alone', () => {
    expect(
      shouldReloadForServedShell(running, '/assets/index-BwV7EThh.js', null)
    ).toBe(false);
  });

  it('never reloads twice for the same served build', () => {
    // A server that keeps answering with something unexpected must not put
    // the page in a reload loop.
    expect(
      shouldReloadForServedShell(
        running,
        '/assets/index-BcrUBDGj.js',
        '/assets/index-BcrUBDGj.js'
      )
    ).toBe(false);
  });

  it('does nothing when either side cannot be determined', () => {
    expect(shouldReloadForServedShell(null, '/assets/index-B.js', null)).toBe(false);
    expect(shouldReloadForServedShell(running, null, null)).toBe(false);
  });
});
