import React, { useState, useEffect } from 'react';
import { ProviderModels, HuggingFaceModelSearchResult } from '../types';
import { api } from '../api/client';
import { useVLLMStatus, VLLM_LOADING_STATES } from '../context/VLLMStatusContext';
import { VLLMDeploymentModal } from './VLLMDeploymentModal';
import { HuggingFaceCatalog } from './HuggingFaceCatalog';
import {
  X,
  Search,
  Check,
  Cpu,
  AlertCircle,
  CheckCircle2,
  Loader2,
  Rocket,
} from 'lucide-react';

interface ModelSelectorModalProps {
  isOpen: boolean;
  onClose: () => void;
  selectedModel: string;
  onSelectModel: (modelId: string) => void;
}

export const ModelSelectorModal: React.FC<ModelSelectorModalProps> = ({
  isOpen,
  onClose,
  selectedModel,
  onSelectModel,
}) => {
  // Shared with the rest of the app: this modal used to fetch status once on open and
  // never again, so its "container active" banner went stale the moment anything moved.
  const { status: vllmStatus } = useVLLMStatus();
  const [providers, setProviders] = useState<ProviderModels[]>([]);
  const [activeTab, setActiveTab] = useState<string>('gemini');
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [searchQuery, setSearchQuery] = useState<string>('');

  // Staged candidate model (pending user confirmation)
  const [candidateModel, setCandidateModel] = useState<string>(selectedModel);
  const [candidateHfModel, setCandidateHfModel] = useState<HuggingFaceModelSearchResult | null>(null);
  const [candidateHfMode, setCandidateHfMode] = useState<'hf' | 'vllm' | null>(null);

  // vLLM Deployment Modal State
  const [isVLLMDeployModalOpen, setIsVLLMDeployModalOpen] = useState<boolean>(false);
  const [vllmDeployTarget, setVllmDeployTarget] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      setCandidateModel(selectedModel);
      loadModelProviders();

      // Set active tab based on selected model
      if (selectedModel.startsWith('gemini/')) setActiveTab('gemini');
      else if (selectedModel.startsWith('openai/')) setActiveTab('openai');
      else if (selectedModel.startsWith('anthropic/')) setActiveTab('anthropic');
      else if (selectedModel.startsWith('ollama/')) setActiveTab('ollama');
      else if (selectedModel.startsWith('huggingface/') || selectedModel.startsWith('hf/') || selectedModel.startsWith('vllm/')) setActiveTab('huggingface');
    }
  }, [isOpen, selectedModel]);

  const loadModelProviders = async () => {
    setIsLoading(true);
    try {
      const data = await api.listModels();
      setProviders(data);
    } catch (error) {
      console.error('Failed to load models:', error);
    } finally {
      setIsLoading(false);
    }
  };

  if (!isOpen) return null;

  const currentProvider = providers.find((p) => p.provider_id === activeTab);

  const filteredModels = (currentProvider?.models || []).filter((model) => {
    if (!searchQuery.trim()) return true;
    const query = searchQuery.toLowerCase();
    return (
      model.name.toLowerCase().includes(query) ||
      model.id.toLowerCase().includes(query) ||
      model.description.toLowerCase().includes(query)
    );
  });

  const providerTabs = [
    { id: 'gemini', name: 'Google', icon: '✨' },
    { id: 'openai', name: 'OpenAI', icon: '🧠' },
    { id: 'anthropic', name: 'Anthropic', icon: '⚡' },
    { id: 'huggingface', name: 'Hugging Face Hub', icon: '🤗' },
  ];

  const handleConfirmSelection = () => {
    if (!candidateModel.trim()) return;
    onSelectModel(candidateModel.trim());
    onClose();
  };

  return (
    <>
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-md-scrim/60 backdrop-blur-xs animate-fade-in font-sans">
        <div className="bg-md-surface-container-low rounded-2xl border border-md-outline-variant shadow-2xl w-full max-w-4xl overflow-hidden flex flex-col max-h-[90vh] transition-colors">
          {/* Modal Header */}
          <div className="px-6 py-4 border-b border-md-outline-variant flex items-center justify-between bg-md-surface-container shrink-0 transition-colors">
            <div className="flex items-center space-x-2.5">
              <div className="w-8 h-8 rounded-xl bg-md-primary-container text-md-on-primary-container border border-md-outline-variant flex items-center justify-center shadow-2xs">
                <Cpu className="w-4 h-4" />
              </div>
              <div>
                <h2 className="text-sm font-bold text-md-on-surface">Select Language Model</h2>
                <p className="text-[11px] text-md-on-surface-variant">
                  Select a model candidate below, then click Confirm Selection to apply.
                </p>
              </div>
            </div>

            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high transition-colors cursor-pointer"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Provider Tabs Bar */}
          <div className="px-6 pt-3 border-b border-md-outline-variant bg-md-surface-container-low flex space-x-2 overflow-x-auto shrink-0 transition-colors">
            {providerTabs.map((tab) => {
              const isActive = activeTab === tab.id;
              const providerData = providers.find((p) => p.provider_id === tab.id);
              const isConfigured = providerData?.is_configured ?? true;

              return (
                <button
                  key={tab.id}
                  onClick={() => {
                    setActiveTab(tab.id);
                    setSearchQuery('');
                  }}
                  className={`pb-3 px-3.5 text-xs font-semibold flex items-center space-x-2 border-b-2 transition-all cursor-pointer whitespace-nowrap ${
                    isActive
                      ? 'border-md-primary text-md-primary'
                      : 'border-transparent text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high rounded-t-lg'
                  }`}
                >
                  <span className="text-sm">{tab.icon}</span>
                  <span>{tab.name}</span>
                  {tab.id !== 'huggingface' && (
                    <span
                      className={`w-2 h-2 rounded-full ${
                        isConfigured ? 'bg-emerald-500' : 'bg-md-outline-variant'
                      }`}
                      title={isConfigured ? 'Provider configured' : 'Provider missing API key or server offline'}
                    />
                  )}
                </button>
              );
            })}
          </div>

          {/* Provider Status / Warning Banner */}
          {activeTab !== 'huggingface' && currentProvider && (
            <div className="px-6 py-2.5 bg-md-surface-container border-b border-md-outline-variant flex items-center justify-between text-xs shrink-0 transition-colors">
              <div className="flex items-center space-x-2">
                {currentProvider.is_configured ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                ) : (
                  <AlertCircle className="w-4 h-4 text-amber-600 dark:text-amber-400" />
                )}
                <span className="font-medium text-md-on-surface">
                  {currentProvider.status_message}
                </span>
              </div>
            </div>
          )}

          {/* Hugging Face Tab Active vLLM Container Banner */}
          {activeTab === 'huggingface' && vllmStatus?.state === 'ready' && (
            <div className="px-6 py-2.5 bg-emerald-100 dark:bg-emerald-950/80 border-b border-emerald-300 dark:border-emerald-800/80 flex items-center justify-between text-xs shrink-0 transition-colors">
              <div className="flex items-center space-x-2">
                <CheckCircle2 className="w-4 h-4 text-emerald-700 dark:text-emerald-300 stroke-[2.5]" />
                <span className="font-medium text-emerald-950 dark:text-emerald-100">
                  Local vLLM Container Active: <code className="font-mono font-bold">{vllmStatus.model_id}</code> (Port {vllmStatus.port || 8001})
                </span>
              </div>

              <button
                type="button"
                onClick={() => {
                  setVllmDeployTarget(vllmStatus.model_id || null);
                  setIsVLLMDeployModalOpen(true);
                }}
                className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-semibold bg-emerald-200 dark:bg-emerald-900/90 border border-emerald-400 dark:border-emerald-700 text-emerald-950 dark:text-emerald-100 hover:opacity-90 transition-opacity cursor-pointer"
              >
                <Rocket className="w-3.5 h-3.5" />
                <span>Manage Server & Logs</span>
              </button>
            </div>
          )}

          {/* Modal Main Body */}
          <div className="flex-1 overflow-y-auto p-6 space-y-4 bg-md-surface">
            {isLoading ? (
              <div className="py-20 text-center text-md-on-surface-variant text-xs flex flex-col items-center justify-center space-y-2">
                <Loader2 className="w-6 h-6 animate-spin text-md-primary" />
                <span>Probing available models and local servers...</span>
              </div>
            ) : activeTab !== 'huggingface' ? (
              /* Tabs 1-4: Structured Providers (Gemini, OpenAI, Anthropic, Ollama) */
              <div className="space-y-4">
                {/* Search Bar for provider models */}
                <div className="relative">
                  <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-md-on-surface-variant" />
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder={`Filter ${currentProvider?.provider_name || ''} models...`}
                    className="w-full bg-md-surface-container-lowest border border-md-outline-variant rounded-xl pl-9 pr-4 py-2 text-xs text-md-on-surface placeholder:text-md-on-surface-variant/70 focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary transition-colors"
                  />
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {filteredModels.map((model) => {
                    const isCandidate = candidateModel === model.id;
                    const isCurrentActive = selectedModel === model.id;
                    return (
                      <div
                        key={model.id}
                        onClick={() => {
                          setCandidateModel(model.id);
                          setCandidateHfModel(null);
                          setCandidateHfMode(null);
                        }}
                        className={`p-4 rounded-xl border cursor-pointer transition-all flex flex-col justify-between ${
                          isCandidate
                            ? 'bg-md-primary-container border-2 border-md-primary ring-2 ring-md-primary/30 text-md-on-primary-container shadow-sm'
                            : 'bg-md-surface border-md-outline-variant hover:border-md-outline hover:bg-md-surface-container text-md-on-surface shadow-xs'
                        }`}
                      >
                        <div>
                          <div className="flex items-center justify-between mb-1.5">
                            <span className="font-bold text-xs text-md-on-surface">{model.name}</span>
                            {model.is_running_locally ? (
                              <span className="inline-flex items-center gap-1 text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-200 border border-emerald-300 dark:border-emerald-800/80 font-semibold">
                                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                                Local
                              </span>
                            ) : (
                              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-md-surface-container-high text-md-on-surface-variant border border-md-outline-variant">
                                {model.context_window || 'Cloud'}
                              </span>
                            )}
                          </div>

                          <p className="text-[11px] text-md-on-surface-variant line-clamp-2 leading-relaxed">
                            {model.description}
                          </p>
                        </div>

                        <div className="mt-3 pt-2.5 border-t border-md-outline-variant flex items-center justify-between text-[11px]">
                          <code className="text-[10px] text-md-on-surface-variant font-mono">{model.id}</code>
                          {isCandidate ? (
                            <span className="inline-flex items-center gap-1 text-md-primary font-bold text-xs">
                              <Check className="w-3.5 h-3.5 stroke-[3]" /> Staged
                            </span>
                          ) : isCurrentActive ? (
                            <span className="text-md-on-surface-variant text-xs font-semibold">
                              (Current)
                            </span>
                          ) : (
                            <span className="text-md-on-surface-variant hover:text-md-primary text-xs font-semibold">
                              Select
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              /* Tab 5: Hugging Face Live Hub Search & Loader */
              <HuggingFaceCatalog
                mode="select"
                selectedModelString={candidateModel}
                selectedHfMode={candidateHfMode}
                onSelectModel={(hfModel, execMode) => {
                  if (execMode === 'hf') {
                    setCandidateModel(hfModel.model_string_hf);
                    setCandidateHfModel(hfModel);
                    setCandidateHfMode('hf');
                  } else {
                    setCandidateModel(hfModel.model_string_vllm);
                    setCandidateHfModel(hfModel);
                    setCandidateHfMode('vllm');
                  }
                }}
                activeVllmModelId={vllmStatus?.model_id}
                isVllmLoading={VLLM_LOADING_STATES.includes(vllmStatus?.state || '')}
              />
            )}
          </div>

          {/* Modal Footer: Confirmation Action Bar */}
          <div className="p-4 border-t border-md-outline-variant bg-md-surface-container flex items-center justify-between shrink-0 transition-colors">
            <div className="flex items-center space-x-2">
              <span className="text-xs text-md-on-surface font-semibold">Selected Model:</span>
              <code className="text-xs font-mono bg-md-surface-container-lowest border border-md-outline-variant px-3 py-1.5 rounded-xl text-md-primary font-bold shadow-2xs">
                {candidateModel}
              </code>
              {candidateHfMode === 'vllm' && (
                <span className="text-[11px] font-semibold text-md-on-tertiary-container bg-md-tertiary-container px-2 py-0.5 rounded-lg border border-md-outline-variant">
                  (vLLM Container Deployment)
                </span>
              )}
            </div>

            <div className="flex items-center space-x-2.5">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 rounded-xl border border-md-outline-variant text-xs font-semibold text-md-on-surface bg-md-surface-container-low hover:bg-md-surface-container-high transition-colors shadow-2xs cursor-pointer"
              >
                Cancel
              </button>

              <button
                type="button"
                onClick={handleConfirmSelection}
                disabled={!candidateModel.trim()}
                className="px-5 py-2 rounded-xl bg-md-primary hover:opacity-90 disabled:opacity-40 text-md-on-primary text-xs font-bold flex items-center gap-1.5 shadow-xs transition-opacity cursor-pointer"
              >
                <Check className="w-3.5 h-3.5 stroke-[3]" />
                <span>Confirm Selection</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* vLLM Deployment Modal */}
      <VLLMDeploymentModal
        isOpen={isVLLMDeployModalOpen}
        onClose={() => {
          setIsVLLMDeployModalOpen(false);
          loadModelProviders();
        }}
        targetModelId={vllmDeployTarget}
      />
    </>
  );
};
