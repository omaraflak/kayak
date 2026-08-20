import React, { useState } from 'react';
import { Check, Copy, LifeBuoy, Mail, X } from 'lucide-react';

/**
 * The launcher's bridge, when Kayak is being shown inside the desktop app.
 *
 * A webview cannot open a `mailto:`, so it is handed to the launcher when one
 * is there. In an ordinary browser the bridge is absent and the normal
 * behaviour is used instead.
 */
function launcherBridge(): { openExternal?: (url: string) => void } | undefined {
  return (window as unknown as { __kayakLauncher?: { openExternal?: (url: string) => void } })
    .__kayakLauncher;
}

interface SupportDialogProps {
  bundle: string;
  address: string;
  onClose: () => void;
}

/**
 * Shows a diagnostics bundle with several ways to get it to us.
 *
 * A mail button alone is not enough: plenty of machines have no mail client
 * configured, and in the desktop app a `mailto:` that goes nowhere fails
 * silently. The text is therefore shown in full and copyable, so sending it is
 * always possible by hand even when nothing else works.
 */
export const SupportDialog: React.FC<SupportDialogProps> = ({ bundle, address, onClose }) => {
  const [copied, setCopied] = useState(false);
  const [addressCopied, setAddressCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(bundle);
    } catch {
      // Clipboard permission can be refused; the textarea below is selectable,
      // so falling back to selecting it is enough.
      const field = document.getElementById('support-bundle') as HTMLTextAreaElement | null;
      field?.select();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const copyAddress = async () => {
    try {
      await navigator.clipboard.writeText(address);
    } catch {
      // The address is `select-all`, so selecting it by hand still works.
    }
    setAddressCopied(true);
    setTimeout(() => setAddressCopied(false), 2000);
  };

  const sendEmail = () => {
    const subject = encodeURIComponent('Kayak problem report');
    // Mail clients drop very long URLs, and the bundle runs to hundreds of
    // lines, so the body carries a prompt and the text is pasted or attached.
    const body = encodeURIComponent(
      'Describe what went wrong here, then paste the report below this line.\n\n'
    );
    const mailto = `mailto:${address}?subject=${subject}&body=${body}`;

    const bridge = launcherBridge();
    if (bridge?.openExternal) {
      bridge.openExternal(mailto);
      return;
    }
    window.location.href = mailto;
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-md-scrim/60 backdrop-blur-xs animate-fade-in font-sans">
      <div className="bg-md-surface-container-low rounded-2xl border border-md-outline-variant shadow-2xl w-full max-w-2xl overflow-hidden flex flex-col max-h-[88vh]">
        <div className="px-6 py-4 border-b border-md-outline-variant flex items-start justify-between bg-md-surface-container shrink-0">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-8 h-8 rounded-xl bg-md-primary-container text-md-on-primary-container border border-md-outline-variant flex items-center justify-center shrink-0">
              <LifeBuoy className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <h2 className="text-sm font-bold text-md-on-surface">Report a problem</h2>
              <p className="text-[11px] text-md-on-surface-variant">
                Two steps: copy the report, then send it.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-md-on-surface-variant hover:bg-md-surface-container-high cursor-pointer"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-6 space-y-3 overflow-y-auto">
          <p className="text-[11px] text-md-on-surface-variant leading-relaxed">
            This describes what Kayak and the launcher were doing. Nothing is sent
            automatically — copy it and paste it into an email.
          </p>

          {/* The address is the one thing a reader must not have to hunt for, so
              it is stated at full size and kept selectable for anyone whose mail
              client the Open email button cannot reach. */}
          <div className="flex items-center justify-between gap-3 flex-wrap p-3 rounded-xl bg-md-primary-container/50 border border-md-outline-variant">
            <div className="min-w-0">
              <p className="text-[10px] uppercase tracking-wider font-bold text-md-on-surface-variant">
                Send it to
              </p>
              <p className="text-sm font-mono font-bold text-md-on-surface select-all break-all">
                {address}
              </p>
            </div>
            <button
              type="button"
              onClick={copyAddress}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-md-surface-container-high text-md-on-surface text-[11px] font-bold hover:brightness-95 cursor-pointer shrink-0"
            >
              {addressCopied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
              {addressCopied ? 'Copied' : 'Copy address'}
            </button>
          </div>
          <textarea
            id="support-bundle"
            readOnly
            value={bundle}
            spellCheck={false}
            className="w-full h-64 bg-md-surface border border-md-outline-variant rounded-lg p-3 text-[10px] font-mono text-md-on-surface-variant leading-relaxed resize-none focus:outline-none focus:border-md-primary"
          />
        </div>

        <div className="px-6 py-4 border-t border-md-outline-variant bg-md-surface-container flex items-center justify-end gap-2 shrink-0 flex-wrap">
          <button
            type="button"
            onClick={copy}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-md-surface-container-high text-md-on-surface text-[11px] font-bold hover:brightness-95 cursor-pointer"
          >
            {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
            {copied ? 'Copied' : 'Copy report'}
          </button>
          <button
            type="button"
            onClick={sendEmail}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-md-primary text-md-on-primary text-[11px] font-bold hover:opacity-90 cursor-pointer"
          >
            <Mail className="w-3.5 h-3.5" /> Open email
          </button>
        </div>
      </div>
    </div>
  );
};
