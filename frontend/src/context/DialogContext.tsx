import React, { createContext, useContext, useState, useCallback, useMemo, ReactNode } from 'react';
import { 
  AlertTriangle, 
  AlertCircle, 
  CheckCircle2, 
  Info, 
  HelpCircle, 
  X,
  Trash2
} from 'lucide-react';

export type DialogVariant = 'danger' | 'warning' | 'info' | 'success' | 'question';

export interface DialogOptions {
  title: string;
  message: string | ReactNode;
  variant?: DialogVariant;
  confirmText?: string;
  cancelText?: string;
  showCancel?: boolean;
  inputPlaceholder?: string;
  inputValue?: string;
  isPrompt?: boolean;
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
  prompt: (options: {
    title: string;
    message: string | ReactNode;
    placeholder?: string;
    defaultValue?: string;
    confirmText?: string;
    cancelText?: string;
  }) => Promise<string | null>;
}

const DialogContext = createContext<DialogContextType | null>(null);

export const useDialog = () => {
  const context = useContext(DialogContext);
  if (!context) {
    throw new Error('useDialog must be used within a DialogProvider');
  }
  return context;
};

/** What a dialog settles with: a decision, entered text, or a dismissal. */
type DialogResult = boolean | string | null;

export const DialogProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [dialogConfig, setDialogConfig] = useState<DialogOptions | null>(null);
  const [promptInput, setPromptInput] = useState('');
  // A dialog answers with a boolean (confirm) or a string/null (prompt), and one
  // resolver serves both, so the settled value is the union rather than anything.
  const [resolver, setResolver] = useState<{
    resolve: (value: DialogResult) => void;
  } | null>(null);

  const confirm = useCallback(
    (options: {
      title: string;
      message: string | ReactNode;
      confirmText?: string;
      cancelText?: string;
      variant?: DialogVariant;
    }): Promise<boolean> => {
      return new Promise((resolve) => {
        setDialogConfig({
          title: options.title,
          message: options.message,
          variant: options.variant || 'question',
          confirmText: options.confirmText || 'Confirm',
          cancelText: options.cancelText || 'Cancel',
          showCancel: true,
          isPrompt: false,
        });
        // Narrowed at the boundary rather than cast: one resolver serves three kinds
        // of dialog, and each converts the settled value into its own answer.
        setResolver({ resolve: (value) => resolve(value === true) });
        setIsOpen(true);
      });
    },
    []
  );

  const alert = useCallback(
    (options: {
      title: string;
      message: string | ReactNode;
      confirmText?: string;
      variant?: DialogVariant;
    }): Promise<void> => {
      return new Promise((resolve) => {
        setDialogConfig({
          title: options.title,
          message: options.message,
          variant: options.variant || 'info',
          confirmText: options.confirmText || 'OK',
          showCancel: false,
          isPrompt: false,
        });
        setResolver({ resolve: () => resolve() });
        setIsOpen(true);
      });
    },
    []
  );

  const prompt = useCallback(
    (options: {
      title: string;
      message: string | ReactNode;
      placeholder?: string;
      defaultValue?: string;
      confirmText?: string;
      cancelText?: string;
    }): Promise<string | null> => {
      return new Promise((resolve) => {
        setPromptInput(options.defaultValue || '');
        setDialogConfig({
          title: options.title,
          message: options.message,
          variant: 'question',
          confirmText: options.confirmText || 'Submit',
          cancelText: options.cancelText || 'Cancel',
          showCancel: true,
          isPrompt: true,
          inputPlaceholder: options.placeholder || '',
        });
        setResolver({
          resolve: (value) => resolve(typeof value === 'string' ? value : null),
        });
        setIsOpen(true);
      });
    },
    []
  );

  const handleConfirm = () => {
    setIsOpen(false);
    if (resolver) {
      if (dialogConfig?.isPrompt) {
        resolver.resolve(promptInput);
      } else {
        resolver.resolve(true);
      }
    }
  };

  const handleCancel = () => {
    setIsOpen(false);
    if (resolver) {
      if (dialogConfig?.isPrompt) {
        resolver.resolve(null);
      } else {
        resolver.resolve(false);
      }
    }
  };

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
  const contextValue = useMemo(
    () => ({ confirm, alert, prompt }),
    [confirm, alert, prompt]
  );

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

            {dialogConfig.isPrompt && (
              <div className="pt-1">
                <input
                  type="text"
                  value={promptInput}
                  onChange={(e) => setPromptInput(e.target.value)}
                  placeholder={dialogConfig.inputPlaceholder}
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleConfirm();
                    if (e.key === 'Escape') handleCancel();
                  }}
                  className="w-full bg-md-surface-container-lowest border border-md-outline-variant rounded-xl px-3.5 py-2 text-xs font-mono text-md-on-surface placeholder:text-md-on-surface-variant/70 focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary transition-colors"
                />
              </div>
            )}

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
                autoFocus={!dialogConfig.isPrompt}
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
