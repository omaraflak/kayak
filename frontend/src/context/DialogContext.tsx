import React, { createContext, useContext, useState, useCallback, useEffect, useMemo, ReactNode } from 'react';
import { 
  AlertTriangle, 
  AlertCircle, 
  CheckCircle2, 
  Info, 
  HelpCircle, 
  X,
} from 'lucide-react';

export type DialogVariant = 'danger' | 'warning' | 'info' | 'success' | 'question';

export interface DialogOptions {
  title: string;
  message: string | ReactNode;
  variant?: DialogVariant;
  confirmText?: string;
  cancelText?: string;
  showCancel?: boolean;
}

interface DialogContextType {
  confirm: (options: {
    title: string;
    message: string | ReactNode;
    confirmText?: string;
    cancelText?: string;
    variant?: DialogVariant;
  }) => Promise<boolean>;
  alert: (options: {
    title: string;
    message: string | ReactNode;
    confirmText?: string;
    variant?: DialogVariant;
  }) => Promise<void>;
}

const DialogContext = createContext<DialogContextType | null>(null);

export const useDialog = () => {
  const context = useContext(DialogContext);
  if (!context) {
    throw new Error('useDialog must be used within a DialogProvider');
  }
  return context;
};

export const DialogProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  // Dialogs wait their turn. A second one used to replace whatever was open, and the
  // first never settled: an error arriving over a "Revert?" confirmation left the
  // revert waiting on an answer the user could no longer give.
  const [queue, setQueue] = useState<
    Array<{ config: DialogOptions; resolve: (confirmed: boolean) => void }>
  >([]);
  const current = queue[0];
  const dialogConfig = current?.config ?? null;
  const isOpen = Boolean(current);

  const open = useCallback(
    (config: DialogOptions) =>
      new Promise<boolean>((resolve) => {
        setQueue((pending) => [...pending, { config, resolve }]);
      }),
    []
  );

  const confirm = useCallback(
    (options: {
      title: string;
      message: string | ReactNode;
      confirmText?: string;
      cancelText?: string;
      variant?: DialogVariant;
    }): Promise<boolean> =>
      open({
        title: options.title,
        message: options.message,
        variant: options.variant || 'question',
        confirmText: options.confirmText || 'Confirm',
        cancelText: options.cancelText || 'Cancel',
        showCancel: true,
      }),
    [open]
  );

  const alert = useCallback(
    (options: {
      title: string;
      message: string | ReactNode;
      confirmText?: string;
      variant?: DialogVariant;
    }): Promise<void> =>
      open({
        title: options.title,
        message: options.message,
        variant: options.variant || 'info',
        confirmText: options.confirmText || 'OK',
        showCancel: false,
      }).then(() => undefined),
    [open]
  );

  // Removes exactly the dialog being answered. A double click reached here twice
  // before the next render, and dropped the dialog queued behind it unanswered.
  const settle = (confirmed: boolean) => {
    if (!current) return;
    current.resolve(confirmed);
    setQueue((pending) => (pending[0] === current ? pending.slice(1) : pending));
  };

  const handleConfirm = () => settle(true);

  const handleCancel = () => settle(false);

  // Escape closes a dialog the way the close button does: a notice is acknowledged,
  // a question is answered "no".
  useEffect(() => {
    if (!current) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      current.resolve(false);
      setQueue((pending) => (pending[0] === current ? pending.slice(1) : pending));
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [current]);

  const getIcon = (variant?: DialogVariant) => {
    switch (variant) {
      case 'danger':
        return (
          <div className="w-10 h-10 rounded-xl bg-rose-100 dark:bg-rose-950/80 border border-rose-300 dark:border-rose-800/80 text-rose-800 dark:text-rose-200 flex items-center justify-center shrink-0">
            <AlertTriangle className="w-5 h-5" />
          </div>
        );
      case 'warning':
        return (
          <div className="w-10 h-10 rounded-xl bg-amber-100 dark:bg-amber-950/80 border border-amber-300 dark:border-amber-800/80 text-amber-800 dark:text-amber-200 flex items-center justify-center shrink-0">
            <AlertCircle className="w-5 h-5" />
          </div>
        );
      case 'success':
        return (
          <div className="w-10 h-10 rounded-xl bg-emerald-100 dark:bg-emerald-950/80 border border-emerald-300 dark:border-emerald-800/80 text-emerald-800 dark:text-emerald-200 flex items-center justify-center shrink-0">
            <CheckCircle2 className="w-5 h-5 stroke-[2.5]" />
          </div>
        );
      case 'question':
        return (
          <div className="w-10 h-10 rounded-xl bg-md-primary-container border border-md-outline-variant text-md-on-primary-container flex items-center justify-center shrink-0">
            <HelpCircle className="w-5 h-5" />
          </div>
        );
      case 'info':
      default:
        return (
          <div className="w-10 h-10 rounded-xl bg-md-primary-container border border-md-outline-variant text-md-on-primary-container flex items-center justify-center shrink-0">
            <Info className="w-5 h-5" />
          </div>
        );
    }
  };

  const getConfirmButtonClass = (variant?: DialogVariant) => {
    if (variant === 'danger') {
      return 'bg-md-error hover:opacity-90 text-md-on-error shadow-xs';
    }
    if (variant === 'warning') {
      return 'bg-amber-600 hover:bg-amber-700 text-white shadow-xs';
    }
    return 'bg-md-primary hover:opacity-90 text-md-on-primary shadow-xs';
  };

  // Stable across renders. Handing consumers a fresh object each time made the
  // `dialog` value change identity whenever any dialog merely opened or closed, so
  // every callback and effect keyed on it re-ran -- silently refetching data and
  // discarding in-progress form state on an unrelated part of the screen.
  const contextValue = useMemo(() => ({ confirm, alert }), [confirm, alert]);

  return (
    <DialogContext.Provider value={contextValue}>
      {children}

      {isOpen && dialogConfig && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs animate-fade-in font-sans">
          <div className="bg-md-surface-container border border-md-outline-variant rounded-2xl max-w-md w-full p-6 shadow-2xl space-y-4 animate-scale-up transition-colors">
            <div className="flex items-start justify-between">
              <div className="flex items-start space-x-3.5">
                {getIcon(dialogConfig.variant)}
                <div className="space-y-1">
                  <h3 className="font-bold text-sm text-md-on-surface leading-tight">
                    {dialogConfig.title}
                  </h3>
                  <div className="text-xs text-md-on-surface-variant leading-relaxed">
                    {dialogConfig.message}
                  </div>
                </div>
              </div>
              <button
                onClick={handleCancel}
                className="text-md-on-surface-variant hover:text-md-on-surface transition-colors p-1 rounded-lg hover:bg-md-surface-container-high cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="flex justify-end space-x-2.5 pt-3 border-t border-md-outline-variant">
              {dialogConfig.showCancel && (
                <button
                  type="button"
                  onClick={handleCancel}
                  className="px-4 py-2 rounded-xl text-xs font-semibold text-md-on-surface hover:bg-md-surface-container-high transition-colors cursor-pointer border border-md-outline-variant"
                >
                  {dialogConfig.cancelText || 'Cancel'}
                </button>
              )}
              <button
                type="button"
                onClick={handleConfirm}
                autoFocus
                className={`px-4 py-2 rounded-xl text-xs font-semibold transition-opacity cursor-pointer ${getConfirmButtonClass(
                  dialogConfig.variant
                )}`}
              >
                {dialogConfig.confirmText || 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}
    </DialogContext.Provider>
  );
};
