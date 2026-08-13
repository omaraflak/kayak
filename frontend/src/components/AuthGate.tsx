import React, { useEffect, useState } from 'react';
import { Loader2, LockKeyhole } from 'lucide-react';
import { api, setStoredAuthToken } from '../api/client';

/**
 * Blocks the app until the browser holds a valid shared secret.
 *
 * Authentication is off unless the server sets KAYAK_AUTH_TOKEN, in which case this
 * renders once and then never again: the token is exchanged for a session cookie so
 * that EventSource streams, which cannot send headers, authenticate too.
 */
export const AuthGate: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [state, setState] = useState<'checking' | 'required' | 'ready'>('checking');
  const [token, setToken] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const checkStatus = async () => {
    try {
      const status = await api.getAuthStatus();
      setState(!status.auth_required || status.authenticated ? 'ready' : 'required');
    } catch {
      // A server that cannot answer the status probe is treated as reachable but
      // unauthenticated rather than blocking the UI outright.
      setState('required');
    }
  };

  useEffect(() => {
    checkStatus();
  }, []);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!token.trim() || isSubmitting) return;

    setIsSubmitting(true);
    setError(null);
    try {
      setStoredAuthToken(token.trim());
      await api.createAuthSession(token.trim());
      setState('ready');
    } catch {
      setStoredAuthToken(null);
      setError('That token was not accepted. Check KAYAK_AUTH_TOKEN on the server.');
    } finally {
      setIsSubmitting(false);
    }
  };

  if (state === 'checking') {
    return (
      <div className="flex h-full w-full items-center justify-center bg-md-surface">
        <Loader2 className="w-5 h-5 animate-spin text-md-on-surface-variant" />
      </div>
    );
  }

  if (state === 'required') {
    return (
      <div className="flex h-full w-full items-center justify-center bg-md-surface px-6">
        <form
          onSubmit={handleSubmit}
          className="w-full max-w-sm space-y-4 rounded-2xl border border-md-outline-variant bg-md-surface-container-low p-6 shadow-xs"
        >
          <div className="space-y-1.5 text-center">
            <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-2xl bg-md-primary-container text-md-on-primary-container border border-md-outline-variant">
              <LockKeyhole className="h-5 w-5" />
            </div>
            <h1 className="text-base font-bold text-md-on-surface">Kayak is locked</h1>
            <p className="text-xs leading-relaxed text-md-on-surface-variant">
              This server requires a shared secret. Enter the value of{' '}
              <code className="font-mono">KAYAK_AUTH_TOKEN</code> to continue.
            </p>
          </div>

          <input
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            autoFocus
            placeholder="Access token"
            className="w-full rounded-xl border border-md-outline-variant bg-md-surface-container-lowest px-3.5 py-2.5 text-sm text-md-on-surface focus:border-md-primary focus:outline-none focus:ring-2 focus:ring-md-primary/20"
          />

          {error && <p className="text-xs text-md-error">{error}</p>}

          <button
            type="submit"
            disabled={!token.trim() || isSubmitting}
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-md-primary px-4 py-2.5 text-sm font-bold text-md-on-primary transition-opacity hover:opacity-90 disabled:opacity-40 cursor-pointer"
          >
            {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
            Unlock
          </button>
        </form>
      </div>
    );
  }

  return <>{children}</>;
};
