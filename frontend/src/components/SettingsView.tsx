import React, { useState, useEffect, useMemo } from 'react';
import { AppSettings } from '../types';
import { api } from '../api/client';
import { useTheme, ThemeMode } from '../context/ThemeContext';
import { 
  Settings as SettingsIcon, 
  Key, 
  Server, 
  Cpu, 
  CheckCircle2, 
  Save, 
  ExternalLink,
  Loader2,
  Rocket,
  Sun,
  Moon,
  Monitor,
  Palette
} from 'lucide-react';
import { VLLMDeploymentModal } from './VLLMDeploymentModal';

export const SettingsView: React.FC = () => {
  const { theme, setTheme } = useTheme();
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [geminiKey, setGeminiKey] = useState('');
  const [openaiKey, setOpenaiKey] = useState('');
  const [anthropicKey, setAnthropicKey] = useState('');
  const [huggingfaceKey, setHuggingfaceKey] = useState('');
  const [vllmBase, setVllmBase] = useState('');
  const [defaultModel, setDefaultModel] = useState('gemini/gemini-3.6-flash');
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [isVLLMModalOpen, setIsVLLMModalOpen] = useState(false);

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
      setHuggingfaceKey(data.HUGGINGFACE_API_KEY || '');
      setVllmBase(data.VLLM_API_BASE || 'http://localhost:8000/v1');
      setDefaultModel(data.DEFAULT_MODEL || 'gemini/gemini-3.6-flash');
    } catch (error) {
      console.error('Failed to load settings:', error);
    }
  };

  const isDirty = useMemo(() => {
    if (!settings) return false;
    return (
      geminiKey.trim() !== (settings.GEMINI_API_KEY || '').trim() ||
      openaiKey.trim() !== (settings.OPENAI_API_KEY || '').trim() ||
      anthropicKey.trim() !== (settings.ANTHROPIC_API_KEY || '').trim() ||
      huggingfaceKey.trim() !== (settings.HUGGINGFACE_API_KEY || '').trim() ||
      vllmBase.trim() !== (settings.VLLM_API_BASE || '').trim() ||
      defaultModel.trim() !== (settings.DEFAULT_MODEL || '').trim()
    );
  }, [settings, geminiKey, openaiKey, anthropicKey, huggingfaceKey, vllmBase, defaultModel]);

  const handleSave = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!isDirty || isSaving) return;
    setIsSaving(true);
    setSaveSuccess(false);

    try {
      const response = await api.updateSettings({
        GEMINI_API_KEY: geminiKey.trim(),
        OPENAI_API_KEY: openaiKey.trim(),
        ANTHROPIC_API_KEY: anthropicKey.trim(),
        HUGGINGFACE_API_KEY: huggingfaceKey.trim(),
        VLLM_API_BASE: vllmBase.trim(),
        DEFAULT_MODEL: defaultModel.trim(),
      });
      setSettings(response.settings);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch (error) {
      console.error('Failed to save settings:', error);
    } finally {
      setIsSaving(false);
    }
  };

  const themeOptions: { mode: ThemeMode; label: string; description: string; icon: React.ReactNode }[] = [
    {
      mode: 'light',
      label: 'Light Mode',
      description: 'Clean high-contrast daytime theme with Google Material design tokens',
      icon: <Sun className="w-4 h-4 text-amber-500" />,
    },
    {
      mode: 'dark',
      label: 'Dark Mode',
      description: 'Deep contrast nighttime theme optimized for OLED and low-light environments',
      icon: <Moon className="w-4 h-4 text-indigo-400" />,
    },
    {
      mode: 'system',
      label: 'System Preference',
      description: 'Automatically synchronizes with your operating system appearance',
      icon: <Monitor className="w-4 h-4 text-zinc-500 dark:text-zinc-400" />,
    },
  ];

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 bg-zinc-50 dark:bg-zinc-950 overflow-y-auto transition-colors">
      {/* Header */}
      <div className="h-16 border-b border-zinc-200 dark:border-zinc-800 px-8 flex items-center justify-between bg-white dark:bg-zinc-900 shrink-0 transition-colors">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded-lg bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 flex items-center justify-center text-zinc-700 dark:text-zinc-300">
            <SettingsIcon className="w-4 h-4" />
          </div>
          <div>
            <h1 className="font-bold text-base text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
              <span>Platform Settings</span>
              {isDirty && (
                <span className="text-[10px] font-medium text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/50 border border-amber-200 dark:border-amber-800/60 px-2 py-0.5 rounded-full flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse"></span>
                  Unsaved changes
                </span>
              )}
            </h1>
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              Configure appearance, LLM provider API credentials, local inference endpoints, and default models.
            </p>
          </div>
        </div>

        <button
          onClick={handleSave}
          disabled={isSaving || !isDirty}
          className="px-5 py-2.5 rounded-xl text-xs font-bold bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 disabled:hover:bg-indigo-600 disabled:cursor-not-allowed text-white flex items-center gap-1.5 shadow-sm transition-all focus:ring-2 focus:ring-indigo-500"
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
          <div className="p-3.5 bg-emerald-50 dark:bg-emerald-950/50 border border-emerald-200 dark:border-emerald-800/60 rounded-xl text-xs text-emerald-800 dark:text-emerald-300 flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
            <span>Settings and API credentials updated successfully.</span>
          </div>
        )}

        {/* Theme & Appearance Card */}
        <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded-2xl p-6 shadow-xs space-y-4 transition-colors">
          <div className="flex items-center space-x-2.5 border-b border-zinc-100 dark:border-zinc-800 pb-3">
            <Palette className="w-4 h-4 text-zinc-600 dark:text-zinc-400" />
            <h2 className="text-sm font-bold text-zinc-900 dark:text-zinc-100">Appearance & Theme</h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {themeOptions.map((opt) => {
              const isSelected = theme === opt.mode;
              return (
                <div
                  key={opt.mode}
                  onClick={() => setTheme(opt.mode)}
                  className={`p-4 rounded-xl border cursor-pointer transition-all flex flex-col justify-between ${
                    isSelected
                      ? 'bg-indigo-50/90 dark:bg-indigo-950/80 border-2 border-indigo-600 dark:border-indigo-400 ring-2 ring-indigo-500/20 text-zinc-950 dark:text-zinc-100 shadow-sm'
                      : 'bg-white dark:bg-zinc-800/80 border-zinc-200 dark:border-zinc-700 text-zinc-800 dark:text-zinc-200 hover:bg-zinc-100/70 dark:hover:bg-zinc-750 hover:border-zinc-300 dark:hover:border-zinc-600 shadow-xs'
                  }`}
                >
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center space-x-2">
                        {opt.icon}
                        <span className="font-semibold text-xs text-zinc-900 dark:text-zinc-100">{opt.label}</span>
                      </div>
                      {isSelected && (
                        <span className="w-2 h-2 rounded-full bg-indigo-600 dark:bg-indigo-400"></span>
                      )}
                    </div>
                    <p className="text-[11px] text-zinc-600 dark:text-zinc-300 leading-relaxed">
                      {opt.description}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Cloud Providers API Keys Card */}
        <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded-2xl p-6 shadow-xs space-y-5 transition-colors">
          <div className="flex items-center space-x-2.5 border-b border-zinc-100 dark:border-zinc-800 pb-3">
            <Key className="w-4 h-4 text-zinc-600 dark:text-zinc-400" />
            <h2 className="text-sm font-bold text-zinc-900 dark:text-zinc-100">Cloud Model Providers & Hugging Face</h2>
          </div>

          <div className="space-y-4">
            {/* Gemini */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
                  Google Gemini API Key
                </label>
                <a
                  href="https://aistudio.google.com/app/apikey"
                  target="_blank"
                  rel="noreferrer"
                  className="text-[11px] text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 flex items-center gap-1 font-semibold"
                >
                  <span>Get Gemini Key</span>
                  <ExternalLink className="w-3 h-3" />
                </a>
              </div>
              <input
                type="password"
                value={geminiKey}
                onChange={(event) => setGeminiKey(event.target.value)}
                placeholder="AIzaSy..."
                className="w-full bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded-xl px-3.5 py-2 text-xs font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-indigo-600 dark:focus:border-indigo-400 focus:ring-1 focus:ring-indigo-500 transition-all"
              />
            </div>

            {/* OpenAI */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
                  OpenAI API Key
                </label>
                <a
                  href="https://platform.openai.com/api-keys"
                  target="_blank"
                  rel="noreferrer"
                  className="text-[11px] text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 flex items-center gap-1 font-semibold"
                >
                  <span>Get OpenAI Key</span>
                  <ExternalLink className="w-3 h-3" />
                </a>
              </div>
              <input
                type="password"
                value={openaiKey}
                onChange={(event) => setOpenaiKey(event.target.value)}
                placeholder="sk-proj-..."
                className="w-full bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded-xl px-3.5 py-2 text-xs font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-indigo-600 dark:focus:border-indigo-400 focus:ring-1 focus:ring-indigo-500 transition-all"
              />
            </div>

            {/* Anthropic */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
                  Anthropic Claude API Key
                </label>
                <a
                  href="https://console.anthropic.com/settings/keys"
                  target="_blank"
                  rel="noreferrer"
                  className="text-[11px] text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 flex items-center gap-1 font-semibold"
                >
                  <span>Get Claude Key</span>
                  <ExternalLink className="w-3 h-3" />
                </a>
              </div>
              <input
                type="password"
                value={anthropicKey}
                onChange={(event) => setAnthropicKey(event.target.value)}
                placeholder="sk-ant-..."
                className="w-full bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded-xl px-3.5 py-2 text-xs font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-indigo-600 dark:focus:border-indigo-400 focus:ring-1 focus:ring-indigo-500 transition-all"
              />
            </div>

            {/* Hugging Face */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
                  Hugging Face User Access Token (HF_TOKEN)
                </label>
                <a
                  href="https://huggingface.co/settings/tokens"
                  target="_blank"
                  rel="noreferrer"
                  className="text-[11px] text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 flex items-center gap-1 font-semibold"
                >
                  <span>Get Hugging Face Token</span>
                  <ExternalLink className="w-3 h-3" />
                </a>
              </div>
              <input
                type="password"
                value={huggingfaceKey}
                onChange={(event) => setHuggingfaceKey(event.target.value)}
                placeholder="hf_..."
                className="w-full bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded-xl px-3.5 py-2 text-xs font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-indigo-600 dark:focus:border-indigo-400 focus:ring-1 focus:ring-indigo-500 transition-all"
              />
              <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-1">
                Used to search the Hugging Face Hub, query serverless inference endpoints, and access gated models.
              </p>
            </div>
          </div>
        </div>

        {/* Local Inference Card */}
        <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded-2xl p-6 shadow-xs space-y-5 transition-colors">
          <div className="flex items-center justify-between border-b border-zinc-100 dark:border-zinc-800 pb-3">
            <div className="flex items-center space-x-2.5">
              <Server className="w-4 h-4 text-zinc-600 dark:text-zinc-400" />
              <h2 className="text-sm font-bold text-zinc-900 dark:text-zinc-100">Local & Self-Hosted Endpoints</h2>
            </div>

            <button
              type="button"
              onClick={() => setIsVLLMModalOpen(true)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-bold bg-indigo-50 dark:bg-indigo-950/80 border border-indigo-200 dark:border-indigo-800/80 text-indigo-700 dark:text-indigo-300 hover:bg-indigo-100 dark:hover:bg-indigo-900/80 transition-colors shadow-2xs cursor-pointer"
            >
              <Rocket className="w-3.5 h-3.5" />
              <span>Manage vLLM Docker Server</span>
            </button>
          </div>

          <div>
            <label className="block text-xs font-semibold text-zinc-800 dark:text-zinc-200 mb-1.5">
              vLLM Endpoint Base URL
            </label>
            <input
              type="text"
              value={vllmBase}
              onChange={(event) => setVllmBase(event.target.value)}
              placeholder="http://localhost:8000/v1"
              className="w-full bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded-xl px-3.5 py-2 text-xs font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-indigo-600 dark:focus:border-indigo-400 focus:ring-1 focus:ring-indigo-500 transition-all"
            />
            <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-1">
              The local OpenAI-compatible inference endpoint served by the vLLM Docker container.
            </p>
          </div>
        </div>

        {/* Default Model Selection Card */}
        <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded-2xl p-6 shadow-xs space-y-4 transition-colors">
          <div className="flex items-center space-x-2.5 border-b border-zinc-100 dark:border-zinc-800 pb-3">
            <Cpu className="w-4 h-4 text-zinc-600 dark:text-zinc-400" />
            <h2 className="text-sm font-bold text-zinc-900 dark:text-zinc-100">Default Global Model</h2>
          </div>

          <div>
            <label className="block text-xs font-semibold text-zinc-800 dark:text-zinc-200 mb-1.5">
              Default Model String
            </label>
            <input
              type="text"
              value={defaultModel}
              onChange={(event) => setDefaultModel(event.target.value)}
              placeholder="gemini/gemini-3.6-flash"
              className="w-full bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded-xl px-3.5 py-2 text-xs font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-indigo-600 dark:focus:border-indigo-400 focus:ring-1 focus:ring-indigo-500 transition-all"
            />
          </div>
        </div>

        {/* Environment Status Card */}
        <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded-2xl p-6 shadow-xs flex items-center justify-between transition-colors">
          <div className="flex items-center space-x-3">
            <div className="w-9 h-9 rounded-xl bg-blue-50 dark:bg-blue-950/60 border border-blue-100 dark:border-blue-800/60 flex items-center justify-center text-lg">
              🐳
            </div>
            <div>
              <div className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                Docker Sandbox Engine
              </div>
              <div className="text-[11px] text-zinc-500 dark:text-zinc-400">
                {settings?.DOCKER_AVAILABLE 
                  ? 'Docker socket connected. Per-conversation container isolation is enabled.' 
                  : 'Docker socket not mounted. Running in host workspace mode.'}
              </div>
            </div>
          </div>

          <span className={`text-xs px-2.5 py-1 rounded-full font-semibold ${
            settings?.DOCKER_AVAILABLE
              ? 'bg-emerald-50 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800/60'
              : 'bg-amber-50 dark:bg-amber-950/60 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-800/60'
          }`}>
            {settings?.DOCKER_AVAILABLE ? 'Available' : 'Host Mode'}
          </span>
        </div>
      </div>

      {/* vLLM Container Modal */}
      <VLLMDeploymentModal
        isOpen={isVLLMModalOpen}
        onClose={() => setIsVLLMModalOpen(false)}
      />
    </div>
  );
};
