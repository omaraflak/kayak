import React, { createContext, useContext, useState, useCallback, ReactNode } from 'react';
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

export const DialogProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [dialogConfig, setDialogConfig] = useState<DialogOptions | null>(null);
  const [promptInput, setPromptInput] = useState('');
  const [resolver, setResolver] = useState<{
    resolve: (value: any) => void;
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
        setResolver({ resolve });
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
        setResolver({ resolve });
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
        setResolver({ resolve });
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
          <div className="w-10 h-10 rounded-xl bg-rose-50 border border-rose-200 text-rose-600 flex items-center justify-center shrink-0">
            <AlertTriangle className="w-5 h-5" />
          </div>
        );
      case 'warning':
        return (
          <div className="w-10 h-10 rounded-xl bg-amber-50 border border-amber-200 text-amber-600 flex items-center justify-center shrink-0">
            <AlertCircle className="w-5 h-5" />
          </div>
        );
      case 'success':
        return (
          <div className="w-10 h-10 rounded-xl bg-emerald-50 border border-emerald-200 text-emerald-600 flex items-center justify-center shrink-0">
            <CheckCircle2 className="w-5 h-5" />
          </div>
        );
      case 'question':
        return (
          <div className="w-10 h-10 rounded-xl bg-zinc-100 border border-zinc-200 text-zinc-700 flex items-center justify-center shrink-0">
            <HelpCircle className="w-5 h-5" />
          </div>
        );
      case 'info':
      default:
        return (
          <div className="w-10 h-10 rounded-xl bg-blue-50 border border-blue-200 text-blue-600 flex items-center justify-center shrink-0">
            <Info className="w-5 h-5" />
          </div>
        );
    }
  };

  const getConfirmButtonClass = (variant?: DialogVariant) => {
    if (variant === 'danger') {
      return 'bg-rose-600 hover:bg-rose-700 text-white shadow-xs';
    }
    if (variant === 'warning') {
      return 'bg-amber-600 hover:bg-amber-700 text-white shadow-xs';
    }
    return 'bg-zinc-900 hover:bg-zinc-800 text-white shadow-xs';
  };

  return (
    <DialogContext.Provider value={{ confirm, alert, prompt }}>
      {children}

      {isOpen && dialogConfig && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-zinc-900/40 backdrop-blur-xs animate-fade-in">
          <div className="bg-white border border-zinc-200 rounded-2xl max-w-md w-full p-6 shadow-xl space-y-4 animate-scale-up">
            <div className="flex items-start justify-between">
              <div className="flex items-start space-x-3.5">
                {getIcon(dialogConfig.variant)}
                <div className="space-y-1">
                  <h3 className="font-bold text-sm text-zinc-900 leading-tight">
                    {dialogConfig.title}
                  </h3>
                  <div className="text-xs text-zinc-600 leading-relaxed">
                    {dialogConfig.message}
                  </div>
                </div>
              </div>
              <button
                onClick={handleCancel}
                className="text-zinc-400 hover:text-zinc-600 transition-colors p-1 rounded-lg hover:bg-zinc-100"
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
                  className="w-full bg-zinc-50 border border-zinc-300 rounded-lg px-3.5 py-2 text-xs text-zinc-900 focus:bg-white focus:outline-none focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900"
                />
              </div>
            )}

            <div className="flex justify-end space-x-2.5 pt-2 border-t border-zinc-100">
              {dialogConfig.showCancel && (
                <button
                  type="button"
                  onClick={handleCancel}
                  className="px-4 py-2 rounded-lg text-xs font-semibold text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100 transition-colors"
                >
                  {dialogConfig.cancelText || 'Cancel'}
                </button>
              )}
              <button
                type="button"
                onClick={handleConfirm}
                autoFocus={!dialogConfig.isPrompt}
                className={`px-4 py-2 rounded-lg text-xs font-semibold transition-colors ${getConfirmButtonClass(
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
