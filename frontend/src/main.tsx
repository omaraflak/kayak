import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import { AuthGate } from './ui/AuthGate';
import { ErrorBoundary } from './ui/ErrorBoundary';
import { ConversationActivityProvider } from './context/ConversationActivityContext';
import { DialogProvider } from './context/DialogContext';
import { ThemeProvider } from './context/ThemeContext';
import { VLLMStatusProvider } from './context/VLLMStatusContext';
import { watchForNewAppShell } from './utils/appShellGuard';
// Bundled rather than linked from a CDN, so maths renders when the Mac is offline.
import 'katex/dist/katex.min.css';
import './index.css';

watchForNewAppShell();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <DialogProvider>
        <AuthGate>
          <VLLMStatusProvider>
            <ConversationActivityProvider>
              {/* The last line of defence: a crash anywhere still leaves a way
                  back, rather than a blank window. */}
              <ErrorBoundary>
                <App />
              </ErrorBoundary>
            </ConversationActivityProvider>
          </VLLMStatusProvider>
        </AuthGate>
      </DialogProvider>
    </ThemeProvider>
  </React.StrictMode>
);

