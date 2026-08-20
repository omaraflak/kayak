import React, { useEffect, useState } from 'react';
import { LifeBuoy, Loader2 } from 'lucide-react';
import { api, errorMessage } from '../../api/client';
import { InstalledVersions } from '../../types';

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
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    api
      .getVersions()
      .then(setVersions)
      // Versions are decoration; failing to read them should not colour the page.
      .catch(() => setVersions(null));
  }, []);

  /**
   * Downloads the bundle, then opens a message for the user to send.
   *
   * Deliberately two steps rather than sending it directly. The bundle contains
   * log output from the user's own machine, so it is theirs to look at and
   * theirs to send; and mail drafts cannot carry an attachment, so the file has
   * to reach them first.
   */
  const reportProblem = async () => {
    setBusy(true);
    setProblem(null);
    try {
      const bundle = await api.getSupportBundle();
      const url = URL.createObjectURL(new Blob([bundle], { type: 'text/plain' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = 'kayak-support.txt';
      link.click();
      URL.revokeObjectURL(url);

      const subject = encodeURIComponent(
        `Kayak problem report (Kayak ${versions?.kayak ?? '?'}, launcher ${versions?.launcher ?? 'none'})`
      );
      const body = encodeURIComponent(
        'Describe what you were doing when it went wrong, and attach the ' +
          'kayak-support.txt file that was just downloaded.\n\n'
      );
      window.location.href = `mailto:${SUPPORT_ADDRESS}?subject=${subject}&body=${body}`;
    } catch (error) {
      setProblem(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pt-2 flex items-center justify-between gap-4 flex-wrap">
      <p className="text-[10px] text-md-on-surface-variant/70 font-mono leading-relaxed">
        Kayak {versions?.kayak ?? '—'}
        {' · '}
        {versions?.launcher ? `Launcher ${versions.launcher}` : 'no launcher'}
      </p>

      <div className="flex items-center gap-2">
        {problem && (
          <span className="text-[10px] text-md-error max-w-xs truncate" title={problem}>
            {problem}
          </span>
        )}
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
  );
};
