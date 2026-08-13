import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import { AuthGate } from './components/AuthGate';
import { DialogProvider } from './context/DialogContext';
import { ThemeProvider } from './context/ThemeContext';
import { VLLMStatusProvider } from './context/VLLMStatusContext';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <DialogProvider>
        <AuthGate>
          <VLLMStatusProvider>
            <App />
          </VLLMStatusProvider>
        </AuthGate>
      </DialogProvider>
    </ThemeProvider>
  </React.StrictMode>
);
