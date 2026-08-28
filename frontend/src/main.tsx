import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import { AuthGate } from './ui/AuthGate';
import { ConversationActivityProvider } from './context/ConversationActivityContext';
import { DialogProvider } from './context/DialogContext';
import { ThemeProvider } from './context/ThemeContext';
import { VLLMStatusProvider } from './context/VLLMStatusContext';
import { watchForNewAppShell } from './utils/appShellGuard';
import './index.css';

watchForNewAppShell();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <DialogProvider>
        <AuthGate>
          <VLLMStatusProvider>
            <ConversationActivityProvider>
              <App />
            </ConversationActivityProvider>
          </VLLMStatusProvider>
        </AuthGate>
      </DialogProvider>
    </ThemeProvider>
  </React.StrictMode>
);

