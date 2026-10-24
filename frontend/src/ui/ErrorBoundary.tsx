import React from 'react';
import { AlertTriangle, RotateCw } from 'lucide-react';

interface ErrorBoundaryProps {
  children: React.ReactNode;
  /** Rendered instead of a subtree that crashed. Defaults to a notice filling the pane. */
  fallback?: React.ReactNode;
  /**
   * Clears a caught crash when it changes, e.g. on moving to another page, so
   * one broken screen does not stay broken after the user has left it.
   */
  resetKey?: unknown;
}

interface ErrorBoundaryState {
  failed: boolean;
}

/**
 * Contains a rendering crash to the part of the window that caused it.
 *
 * React unmounts the whole tree when a render throws and nothing catches it, so a
 * single malformed message used to leave the window blank, with no way back short
 * of reloading -- which a person who does not know the app is a web page has no
 * reason to try.
 */
export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: unknown, info: React.ErrorInfo): void {
    console.error('Kayak could not display part of the page:', error, info.componentStack);
  }

  componentDidUpdate(previous: ErrorBoundaryProps): void {
    if (this.state.failed && previous.resetKey !== this.props.resetKey) {
      this.setState({ failed: false });
    }
  }

  render(): React.ReactNode {
    if (!this.state.failed) return this.props.children;
    if (this.props.fallback !== undefined) return this.props.fallback;
    return <CrashNotice onRetry={() => this.setState({ failed: false })} />;
  }
}

const CrashNotice: React.FC<{ onRetry: () => void }> = ({ onRetry }) => (
  <div className="flex-1 flex h-full w-full items-center justify-center bg-md-surface p-8">
    <div className="max-w-sm text-center space-y-3">
      <AlertTriangle className="w-6 h-6 text-md-on-surface-variant mx-auto" />
      <h1 className="text-sm font-bold text-md-on-surface">This page couldn't be shown</h1>
      <p className="text-[11px] text-md-on-surface-variant leading-relaxed">
        Something went wrong while displaying it. Your conversations and files are safe.
      </p>
      <div className="flex items-center justify-center gap-2 pt-1">
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-md-surface-container-high text-md-on-surface border border-md-outline-variant hover:opacity-90 transition-opacity cursor-pointer"
        >
          Try again
        </button>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-xs font-bold bg-md-primary text-md-on-primary hover:opacity-90 transition-opacity cursor-pointer"
        >
          <RotateCw className="w-3.5 h-3.5" /> Reload Kayak
        </button>
      </div>
    </div>
  </div>
);
