import React, { useState, useEffect } from 'react';
import { AppSettings } from '../types';
import { api } from '../api/client';
import { 
  Settings as SettingsIcon, 
  Key, 
  Server, 
  Cpu, 
  CheckCircle2, 
  Save, 
  ExternalLink,
  Shield,
  Loader2
} from 'lucide-react';

export const SettingsView: React.FC = () => {
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [geminiKey, setGeminiKey] = useState('');
  const [openaiKey, setOpenaiKey] = useState('');
  const [anthropicKey, setAnthropicKey] = useState('');
  const [vllmBase, setVllmBase] = useState('');
  const [ollamaBase, setOllamaBase] = useState('');
  const [defaultModel, setDefaultModel] = useState('gemini/gemini-3.6-flash');
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      const data = await api.getSettings();
      setSettings(data);
      setGeminiKey(data.GEMINI_API_KEY || '');
      setOpenaiKey(data.OPENAI_API_KEY || '');
      setAnthropicKey(data.ANTHROPIC_API_KEY || '');
      setVllmBase(data.VLLM_API_BASE || 'http://localhost:8000/v1');
      setOllamaBase(data.OLLAMA_API_BASE || 'http://localhost:11434');
      setDefaultModel(data.DEFAULT_MODEL || 'gemini/gemini-3.6-flash');
    } catch (e) {
      console.error('Failed to load settings:', e);
    }
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSaving(true);
    setSaveSuccess(false);

    try {
      const res = await api.updateSettings({
        GEMINI_API_KEY: geminiKey.trim(),
        OPENAI_API_KEY: openaiKey.trim(),
        ANTHROPIC_API_KEY: anthropicKey.trim(),
        VLLM_API_BASE: vllmBase.trim(),
        OLLAMA_API_BASE: ollamaBase.trim(),
        DEFAULT_MODEL: defaultModel.trim(),
      });
      setSettings(res.settings);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch (e) {
      console.error('Failed to save settings:', e);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col h-screen bg-zinc-50 overflow-y-auto">
      {/* Header */}
      <div className="h-16 border-b border-zinc-200 px-8 flex items-center justify-between bg-white shrink-0">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded-lg bg-zinc-100 border border-zinc-200 flex items-center justify-center text-zinc-700">
            <SettingsIcon className="w-4 h-4" />
          </div>
          <div>
            <h1 className="font-bold text-base text-zinc-900">Platform Settings</h1>
            <p className="text-xs text-zinc-500">
              Configure LLM provider API credentials, local inference endpoints, and default models.
            </p>
          </div>
        </div>

        <button
          onClick={handleSave}
          disabled={isSaving}
          className="px-5 py-2.5 rounded-xl text-xs font-bold bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white flex items-center gap-1.5 shadow-sm transition-all focus:ring-2 focus:ring-indigo-500"
        >
          {isSaving ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : saveSuccess ? (
            <CheckCircle2 className="w-3.5 h-3.5 text-white" />
          ) : (
            <Save className="w-3.5 h-3.5" />
          )}
          <span>{saveSuccess ? 'Settings Saved!' : 'Save Settings'}</span>
        </button>
      </div>

      {/* Main Settings Form */}
      <div className="p-8 max-w-4xl w-full mx-auto space-y-6">
        {saveSuccess && (
          <div className="p-3.5 bg-emerald-50 border border-emerald-200 rounded-xl text-xs text-emerald-800 flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-600" />
            <span>Settings and API credentials updated successfully.</span>
          </div>
        )}

        {/* Cloud Providers API Keys Card */}
        <div className="bg-white border border-zinc-200 rounded-2xl p-6 shadow-sm space-y-5">
          <div className="flex items-center space-x-2.5 border-b border-zinc-100 pb-3">
            <Key className="w-4 h-4 text-zinc-600" />
            <h2 className="text-sm font-bold text-zinc-900">Cloud Model Providers</h2>
          </div>

          <div className="space-y-4">
            {/* Gemini */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-xs font-semibold text-zinc-700">
                  Google Gemini API Key
                </label>
                <a
                  href="https://aistudio.google.com/app/apikey"
                  target="_blank"
                  rel="noreferrer"
                  className="text-[11px] text-teal-600 hover:text-teal-700 flex items-center gap-1"
                >
                  <span>Get API Key</span>
                  <ExternalLink className="w-3 h-3" />
                </a>
              </div>
              <input
                type="password"
                value={geminiKey}
                onChange={(e) => setGeminiKey(e.target.value)}
                placeholder="AIzaSy..."
                className="w-full bg-zinc-50 border border-zinc-300 rounded-lg px-3.5 py-2 text-xs font-mono text-zinc-900 focus:bg-white focus:outline-none focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900 transition-all"
              />
            </div>

            {/* OpenAI */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-xs font-semibold text-zinc-700">
                  OpenAI API Key
                </label>
                <a
                  href="https://platform.openai.com/api-keys"
                  target="_blank"
                  rel="noreferrer"
                  className="text-[11px] text-teal-600 hover:text-teal-700 flex items-center gap-1"
                >
                  <span>Get API Key</span>
                  <ExternalLink className="w-3 h-3" />
                </a>
              </div>
              <input
                type="password"
                value={openaiKey}
                onChange={(e) => setOpenaiKey(e.target.value)}
                placeholder="sk-proj-..."
                className="w-full bg-zinc-50 border border-zinc-300 rounded-lg px-3.5 py-2 text-xs font-mono text-zinc-900 focus:bg-white focus:outline-none focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900 transition-all"
              />
            </div>

            {/* Anthropic */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-xs font-semibold text-zinc-700">
                  Anthropic Claude API Key
                </label>
                <a
                  href="https://console.anthropic.com/settings/keys"
                  target="_blank"
                  rel="noreferrer"
                  className="text-[11px] text-teal-600 hover:text-teal-700 flex items-center gap-1"
                >
                  <span>Get API Key</span>
                  <ExternalLink className="w-3 h-3" />
                </a>
              </div>
              <input
                type="password"
                value={anthropicKey}
                onChange={(e) => setAnthropicKey(e.target.value)}
                placeholder="sk-ant-..."
                className="w-full bg-zinc-50 border border-zinc-300 rounded-lg px-3.5 py-2 text-xs font-mono text-zinc-900 focus:bg-white focus:outline-none focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900 transition-all"
              />
            </div>
          </div>
        </div>

        {/* Local Inference Card */}
        <div className="bg-white border border-zinc-200 rounded-2xl p-6 shadow-sm space-y-5">
          <div className="flex items-center space-x-2.5 border-b border-zinc-100 pb-3">
            <Server className="w-4 h-4 text-zinc-600" />
            <h2 className="text-sm font-bold text-zinc-900">Local & Self-Hosted Endpoints</h2>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-semibold text-zinc-700 mb-1.5">
                vLLM Base URL
              </label>
              <input
                type="text"
                value={vllmBase}
                onChange={(e) => setVllmBase(e.target.value)}
                placeholder="http://localhost:8000/v1"
                className="w-full bg-zinc-50 border border-zinc-300 rounded-lg px-3.5 py-2 text-xs font-mono text-zinc-900 focus:bg-white focus:outline-none focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900 transition-all"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-zinc-700 mb-1.5">
                Ollama Base URL
              </label>
              <input
                type="text"
                value={ollamaBase}
                onChange={(e) => setOllamaBase(e.target.value)}
                placeholder="http://localhost:11434"
                className="w-full bg-zinc-50 border border-zinc-300 rounded-lg px-3.5 py-2 text-xs font-mono text-zinc-900 focus:bg-white focus:outline-none focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900 transition-all"
              />
            </div>
          </div>
        </div>

        {/* Default Model Selection Card */}
        <div className="bg-white border border-zinc-200 rounded-2xl p-6 shadow-sm space-y-4">
          <div className="flex items-center space-x-2.5 border-b border-zinc-100 pb-3">
            <Cpu className="w-4 h-4 text-zinc-600" />
            <h2 className="text-sm font-bold text-zinc-900">Default Global Model</h2>
          </div>

          <div>
            <label className="block text-xs font-semibold text-zinc-700 mb-1.5">
              Default Model String
            </label>
            <input
              type="text"
              value={defaultModel}
              onChange={(e) => setDefaultModel(e.target.value)}
              placeholder="gemini/gemini-3.6-flash"
              className="w-full bg-zinc-50 border border-zinc-300 rounded-lg px-3.5 py-2 text-xs font-mono text-zinc-900 focus:bg-white focus:outline-none focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900 transition-all"
            />
          </div>
        </div>

        {/* Environment Status Card */}
        <div className="bg-white border border-zinc-200 rounded-2xl p-6 shadow-sm flex items-center justify-between">
          <div className="flex items-center space-x-3">
            <div className="w-9 h-9 rounded-xl bg-blue-50 border border-blue-100 flex items-center justify-center text-lg">
              🐳
            </div>
            <div>
              <div className="text-xs font-bold text-zinc-900">
                Docker Sandbox Engine
              </div>
              <div className="text-[11px] text-zinc-500">
                {settings?.DOCKER_AVAILABLE 
                  ? 'Docker socket connected. Per-conversation container isolation is enabled.' 
                  : 'Docker socket not mounted. Running in host workspace mode.'}
              </div>
            </div>
          </div>

          <span className={`text-xs px-2.5 py-1 rounded-full font-semibold ${
            settings?.DOCKER_AVAILABLE
              ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
              : 'bg-amber-50 text-amber-700 border border-amber-200'
          }`}>
            {settings?.DOCKER_AVAILABLE ? 'Available' : 'Host Mode'}
          </span>
        </div>
      </div>
    </div>
  );
};
