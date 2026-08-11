import React, { useState, useEffect } from 'react';
import { ProviderModels, ModelItem, HuggingFaceModelSearchResult, VLLMDeploymentProgress } from '../types';
import { api } from '../api/client';
import { VLLMDeploymentModal } from './VLLMDeploymentModal';
import { HuggingFaceCatalog } from './HuggingFaceCatalog';
import { 
  X, 
  Search, 
  Check, 
  Sparkles, 
  Cpu, 
  Server, 
  AlertCircle, 
  CheckCircle2, 
  Loader2, 
  Download, 
  Heart,
  Rocket
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
  const [providers, setProviders] = useState<ProviderModels[]>([]);
  const [activeTab, setActiveTab] = useState<string>('gemini');
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [searchQuery, setSearchQuery] = useState<string>('');

  // Staged candidate model (pending user confirmation)
  const [candidateModel, setCandidateModel] = useState<string>(selectedModel);
  const [candidateHfModel, setCandidateHfModel] = useState<HuggingFaceModelSearchResult | null>(null);
  const [candidateHfMode, setCandidateHfMode] = useState<'hf' | 'vllm' | null>(null);

  // Hugging Face Live Hub Search State
  const [hfSearchQuery, setHfSearchQuery] = useState<string>('qwen2.5-coder');
  const [hfResults, setHfResults] = useState<HuggingFaceModelSearchResult[]>([]);
  const [isSearchingHf, setIsSearchingHf] = useState<boolean>(false);

  // vLLM Deployment Modal State
  const [isVLLMDeployModalOpen, setIsVLLMDeployModalOpen] = useState<boolean>(false);
  const [vllmDeployTarget, setVllmDeployTarget] = useState<string | null>(null);
  const [vllmStatus, setVllmStatus] = useState<VLLMDeploymentProgress | null>(null);

  useEffect(() => {
    if (isOpen) {
      setCandidateModel(selectedModel);
      loadModelProviders();
      loadVLLMStatus();

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

  const loadVLLMStatus = async () => {
    try {
      const data = await api.getVLLMStatus();
      setVllmStatus(data);
    } catch (error) {
      console.error('Failed to load vLLM status:', error);
    }
  };

  const handleSearchHuggingFace = async (queryToSearch: string) => {
    if (!queryToSearch.trim() || queryToSearch.trim().length < 2) return;
    setIsSearchingHf(true);
    try {
      const results = await api.searchHuggingFaceModels(queryToSearch.trim());
      setHfResults(results);
    } catch (error) {
      console.error('Failed to search Hugging Face:', error);
    } finally {
      setIsSearchingHf(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'huggingface' && hfResults.length === 0) {
      handleSearchHuggingFace(hfSearchQuery);
    }
  }, [activeTab]);

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
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-zinc-950/60 backdrop-blur-xs animate-fade-in font-sans">
        <div className="bg-white rounded-2xl border border-zinc-200 shadow-2xl w-full max-w-4xl overflow-hidden flex flex-col max-h-[90vh]">
          {/* Modal Header */}
          <div className="px-6 py-4 border-b border-zinc-200 flex items-center justify-between bg-zinc-50/70 shrink-0">
            <div className="flex items-center space-x-2.5">
              <div className="w-8 h-8 rounded-xl bg-indigo-50 border border-indigo-200 flex items-center justify-center text-indigo-600 shadow-2xs">
                <Cpu className="w-4 h-4" />
              </div>
              <div>
                <h2 className="text-sm font-bold text-zinc-900">Select Language Model</h2>
                <p className="text-[11px] text-zinc-500">
                  Select a model candidate below, then click Confirm Selection to apply.
                </p>
              </div>
            </div>

            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-700 hover:bg-zinc-200/60 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Provider Tabs Bar */}
          <div className="px-6 pt-3 border-b border-zinc-200 bg-white flex space-x-2 overflow-x-auto shrink-0">
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
                      ? 'border-indigo-600 text-indigo-600'
                      : 'border-transparent text-zinc-500 hover:text-zinc-800'
                  }`}
                >
                  <span className="text-sm">{tab.icon}</span>
                  <span>{tab.name}</span>
                  {tab.id !== 'huggingface' && (
                    <span
                      className={`w-2 h-2 rounded-full ${
                        isConfigured ? 'bg-emerald-500' : 'bg-zinc-300'
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
            <div className="px-6 py-2.5 bg-zinc-50 border-b border-zinc-200 flex items-center justify-between text-xs shrink-0">
              <div className="flex items-center space-x-2">
                {currentProvider.is_configured ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                ) : (
                  <AlertCircle className="w-4 h-4 text-amber-500" />
                )}
                <span className="font-medium text-zinc-700">
                  {currentProvider.status_message}
                </span>
              </div>
            </div>
          )}

          {/* Hugging Face Tab Active vLLM Container Banner */}
          {activeTab === 'huggingface' && vllmStatus?.state === 'ready' && (
            <div className="px-6 py-2.5 bg-emerald-50/70 border-b border-emerald-200 flex items-center justify-between text-xs shrink-0">
              <div className="flex items-center space-x-2">
                <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                <span className="font-medium text-emerald-900">
                  Local vLLM Container Active: <code className="font-mono font-bold">{vllmStatus.model_id}</code> (Port {vllmStatus.port || 8001})
                </span>
              </div>

              <button
                type="button"
                onClick={() => {
                  setVllmDeployTarget(vllmStatus.model_id || null);
                  setIsVLLMDeployModalOpen(true);
                }}
                className="inline-flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-semibold bg-emerald-100/80 border border-emerald-300 text-emerald-800 hover:bg-emerald-200 transition-colors"
              >
                <Rocket className="w-3.5 h-3.5" />
                <span>Manage Server & Logs</span>
              </button>
            </div>
          )}

          {/* Modal Main Body */}
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            {isLoading ? (
              <div className="py-20 text-center text-zinc-400 text-xs flex flex-col items-center justify-center space-y-2">
                <Loader2 className="w-6 h-6 animate-spin text-indigo-600" />
                <span>Probing available models and local servers...</span>
              </div>
            ) : activeTab !== 'huggingface' ? (
              /* Tabs 1-4: Structured Providers (Gemini, OpenAI, Anthropic, Ollama) */
              <div className="space-y-4">
                {/* Search Bar for provider models */}
                <div className="relative">
                  <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-400" />
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder={`Filter ${currentProvider?.provider_name || ''} models...`}
                    className="w-full bg-zinc-50 border border-zinc-200 rounded-xl pl-9 pr-4 py-2 text-xs text-zinc-900 focus:bg-white focus:outline-none focus:border-indigo-600 focus:ring-1 focus:ring-indigo-600"
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
                            ? 'bg-indigo-50/80 border-indigo-600 ring-2 ring-indigo-500/30 text-zinc-950 shadow-xs'
                            : 'bg-white border-zinc-200 hover:border-zinc-300 hover:bg-zinc-50/80 shadow-xs'
                        }`}
                      >
                        <div>
                          <div className="flex items-center justify-between mb-1.5">
                            <span className="font-bold text-xs text-zinc-900">{model.name}</span>
                            {model.is_running_locally ? (
                              <span className="inline-flex items-center gap-1 text-[10px] font-mono px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800 border border-emerald-200 font-semibold">
                                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                                Local
                              </span>
                            ) : (
                              <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-zinc-100 text-zinc-600 border border-zinc-200">
                                {model.context_window || 'Cloud'}
                              </span>
                            )}
                          </div>

                          <p className="text-[11px] text-zinc-500 line-clamp-2 leading-relaxed">
                            {model.description}
                          </p>
                        </div>

                        <div className="mt-3 pt-2.5 border-t border-zinc-100 flex items-center justify-between text-[11px]">
                          <code className="text-[10px] text-zinc-500 font-mono">{model.id}</code>
                          {isCandidate ? (
                            <span className="inline-flex items-center gap-1 text-indigo-700 font-bold text-xs">
                              <Check className="w-3.5 h-3.5 stroke-[3]" /> Staged
                            </span>
                          ) : isCurrentActive ? (
                            <span className="text-zinc-500 text-xs font-medium">
                              (Current)
                            </span>
                          ) : (
                            <span className="text-zinc-400 group-hover:text-indigo-600 text-xs font-semibold">
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
                isVllmLoading={['pulling_image', 'starting_container', 'loading'].includes(vllmStatus?.state || '')}
              />
            )}
          </div>

          {/* Modal Footer: Confirmation Action Bar */}
          <div className="p-4 border-t border-zinc-200 bg-zinc-50 flex items-center justify-between shrink-0">
            <div className="flex items-center space-x-2">
              <span className="text-xs text-zinc-500 font-semibold">Selected Model:</span>
              <code className="text-xs font-mono bg-white border border-zinc-300 px-3 py-1.5 rounded-xl text-indigo-700 font-bold shadow-2xs">
                {candidateModel}
              </code>
              {candidateHfMode === 'vllm' && (
                <span className="text-[11px] font-semibold text-indigo-700 bg-indigo-100/70 px-2 py-0.5 rounded-lg">
                  (vLLM Container Deployment)
                </span>
              )}
            </div>

            <div className="flex items-center space-x-2.5">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 rounded-xl border border-zinc-300 text-xs font-semibold text-zinc-700 bg-white hover:bg-zinc-100 transition-colors shadow-2xs"
              >
                Cancel
              </button>

              <button
                type="button"
                onClick={handleConfirmSelection}
                disabled={!candidateModel.trim()}
                className="px-5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 text-white text-xs font-bold flex items-center gap-1.5 shadow-xs transition-colors"
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
          loadVLLMStatus();
          loadModelProviders();
        }}
        targetModelId={vllmDeployTarget}
        onModelReady={(modelString) => {
          onSelectModel(modelString);
          onClose();
        }}
      />
    </>
  );
};
