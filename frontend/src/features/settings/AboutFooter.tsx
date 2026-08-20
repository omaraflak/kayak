import React, { useEffect, useState } from 'react';
import { LifeBuoy, Loader2 } from 'lucide-react';
import { api, errorMessage } from '../../api/client';
import { InstalledVersions } from '../../types';
import { SupportDialog } from './SupportDialog';

/** Where a support bundle should be sent. */
const SUPPORT_ADDRESS = 'aflakomar@gmail.com';

/**
 * Versions and a way to report a problem, at the foot of the settings page.
 *
 * Two separately updatable pieces are installed and either can be the one at
 * fault, so both versions are stated: a mismatch between them explains a whole
 * class of "it does not work for me" reports, and neither was visible anywhere
 * before.
 */
export const AboutFooter: React.FC = () => {
  const [versions, setVersions] = useState<InstalledVersions | null>(null);
  const [busy, setBusy] = useState(false);
  const [bundle, setBundle] = useState<string | null>(null);

  useEffect(() => {
    api
      .getVersions()
      .then(setVersions)
      // Versions are decoration; failing to read them should not colour the page.
      .catch(() => setVersions(null));
  }, []);

  /**
   * Fetches the bundle and shows it, rather than acting on the user's behalf.
   *
   * The dialog offers copy, download and email because none of them works
   * everywhere: a machine may have no mail client configured, and inside the
   * desktop app a `mailto:` that nothing handles fails without saying so. Text
   * on screen that can be selected always works.
   */
  const reportProblem = async () => {
    setBusy(true);
    try {
      setBundle(await api.getSupportBundle());
    } catch (error) {
      // The failure opens the dialog rather than printing a line of red text
      // beside the button. When the report cannot be built, the reason for that
      // is the one thing worth sending, and a truncated span can be neither
      // read nor copied.
      setBundle(
        [
          'The report could not be built.',
          '',
          `Error: ${errorMessage(error)}`,
          `Page:  ${window.location.href}`,
          `Time:  ${new Date().toISOString()}`,
        ].join('\n')
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {bundle !== null && (
        <SupportDialog
          bundle={bundle}
          address={SUPPORT_ADDRESS}
          onClose={() => setBundle(null)}
        />
      )}

      <div className="pt-2 flex items-center justify-between gap-4 flex-wrap">
        <p className="text-[10px] text-md-on-surface-variant/70 font-mono leading-relaxed">
          Kayak {versions?.kayak ?? '—'}
          {' · '}
          {versions?.launcher ? `Launcher ${versions.launcher}` : 'no launcher'}
        </p>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={reportProblem}
            disabled={busy}
            className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-md-on-surface-variant hover:text-md-primary disabled:opacity-50 transition-colors cursor-pointer"
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <LifeBuoy className="w-3.5 h-3.5" />}
            Report a problem
          </button>
        </div>
      </div>
    </>
  );
};
