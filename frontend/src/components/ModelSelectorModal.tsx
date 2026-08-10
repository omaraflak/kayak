import React, { useState, useEffect } from 'react';
import { ProviderModels, ModelItem, HuggingFaceModelSearchResult } from '../types';
import { api } from '../api/client';
import { 
  X, 
  Search, 
  Check, 
  ExternalLink, 
  Sparkles, 
  Cpu, 
  Server, 
  AlertCircle, 
  CheckCircle2, 
  Loader2, 
  Download, 
  Heart,
  HelpCircle
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

  // Hugging Face Live Hub Search State
  const [hfSearchQuery, setHfSearchQuery] = useState<string>('qwen2.5-coder');
  const [hfResults, setHfResults] = useState<HuggingFaceModelSearchResult[]>([]);
  const [isSearchingHf, setIsSearchingHf] = useState<boolean>(false);

  // Custom string fallback
  const [customModelInput, setCustomModelInput] = useState<string>('');

  useEffect(() => {
    if (isOpen) {
      loadModelProviders();
      // Set active tab based on selected model
      if (selectedModel.startsWith('gemini/')) setActiveTab('gemini');
      else if (selectedModel.startsWith('openai/')) setActiveTab('openai');
      else if (selectedModel.startsWith('anthropic/')) setActiveTab('anthropic');
      else if (selectedModel.startsWith('ollama/')) setActiveTab('ollama');
      else if (selectedModel.startsWith('vllm/')) setActiveTab('vllm');
      else if (selectedModel.startsWith('huggingface/') || selectedModel.startsWith('hf/')) setActiveTab('huggingface');
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-zinc-950/40 backdrop-blur-xs animate-fade-in">
      <div className="bg-white rounded-2xl border border-zinc-200 shadow-2xl w-full max-w-4xl max-h-[85vh] flex flex-col overflow-hidden">
        {/* Modal Header */}
        <div className="h-16 px-6 border-b border-zinc-200 flex items-center justify-between bg-white shrink-0">
          <div className="flex items-center space-x-2.5">
            <div className="w-8 h-8 rounded-lg bg-indigo-50 border border-indigo-200 flex items-center justify-center text-indigo-600">
              <Cpu className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-zinc-900">Select LLM Model</h2>
              <p className="text-[11px] text-zinc-500">
                Choose cloud APIs, local inference backends (Ollama/vLLM), or load from Hugging Face Hub.
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-2 text-zinc-400 hover:text-zinc-700 hover:bg-zinc-100 rounded-xl transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Provider Tabs */}
        <div className="flex border-b border-zinc-200 bg-zinc-50/70 px-6 gap-2 overflow-x-auto shrink-0">
          {providers.map((provider) => {
            const isActive = activeTab === provider.provider_id;
            return (
              <button
                key={provider.provider_id}
                onClick={() => setActiveTab(provider.provider_id)}
                className={`py-3 px-3 text-xs font-semibold border-b-2 flex items-center gap-2 whitespace-nowrap transition-all ${
                  isActive
                    ? 'border-indigo-600 text-indigo-700 bg-white shadow-xs rounded-t-lg'
                    : 'border-transparent text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100/60 rounded-t-lg'
                }`}
              >
                <span>{provider.icon}</span>
                <span>{provider.provider_name}</span>
                {provider.is_configured ? (
                  <span className="w-2 h-2 rounded-full bg-emerald-500" title="Configured & Ready" />
                ) : (
                  <span className="w-2 h-2 rounded-full bg-amber-400" title="Not Configured" />
                )}
              </button>
            );
          })}
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-5 bg-zinc-50/30">
          {/* Provider Status Banner */}
          {currentProvider && (
            <div className={`p-4 rounded-xl border flex items-center justify-between text-xs ${
              currentProvider.is_configured
                ? 'bg-emerald-50/70 border-emerald-200 text-emerald-900'
                : 'bg-amber-50/70 border-amber-200 text-amber-900'
            }`}>
              <div className="flex items-center space-x-2.5">
                {currentProvider.is_configured ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
                ) : (
                  <AlertCircle className="w-4 h-4 text-amber-600 shrink-0" />
                )}
                <div>
                  <span className="font-semibold">{currentProvider.provider_name}: </span>
                  <span>{currentProvider.status_message}</span>
                </div>
              </div>

              {!currentProvider.is_configured && (
                <span className="text-[11px] text-amber-700 font-medium">
                  Go to Settings tab to enter keys or start local server
                </span>
              )}
            </div>
          )}

          {/* Tab 1-5: Cloud Providers, Local Ollama, and Local vLLM Models */}
          {activeTab !== 'huggingface' ? (
            <div className="space-y-4">
              {/* Search Filter */}
              <div className="relative">
                <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-400" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={`Search ${currentProvider?.provider_name || 'available'} models...`}
                  className="w-full bg-white border border-zinc-200 rounded-xl pl-9 pr-4 py-2 text-xs text-zinc-900 focus:outline-none focus:border-indigo-600 focus:ring-1 focus:ring-indigo-600 shadow-xs"
                />
              </div>

              {/* Models List */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {filteredModels.map((model) => {
                  const isSelected = selectedModel === model.id;
                  return (
                    <div
                      key={model.id}
                      onClick={() => {
                        onSelectModel(model.id);
                        onClose();
                      }}
                      className={`p-4 rounded-xl border cursor-pointer transition-all flex flex-col justify-between ${
                        isSelected
                          ? 'bg-indigo-50/80 border border-indigo-600 text-zinc-950 shadow-xs ring-1 ring-indigo-500/20'
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
                        {isSelected ? (
                          <span className="inline-flex items-center gap-1 text-indigo-600 font-bold text-xs">
                            <Check className="w-3.5 h-3.5 stroke-[3]" /> Active
                          </span>
                        ) : (
                          <span className="text-zinc-400 group-hover:text-indigo-600 text-xs font-semibold">
                            Select →
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            /* Tab 6: Hugging Face Live Hub Search & Loader */
            <div className="space-y-4">
              {/* Hugging Face Search Bar */}
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  handleSearchHuggingFace(hfSearchQuery);
                }}
                className="flex gap-2"
              >
                <div className="relative flex-1">
                  <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-400" />
                  <input
                    type="text"
                    value={hfSearchQuery}
                    onChange={(e) => setHfSearchQuery(e.target.value)}
                    placeholder="Search Hugging Face Hub (e.g. qwen2.5-coder, mistral, llama-3, deepseek)..."
                    className="w-full bg-white border border-zinc-200 rounded-xl pl-9 pr-4 py-2.5 text-xs text-zinc-900 focus:outline-none focus:border-indigo-600 focus:ring-1 focus:ring-indigo-600 shadow-xs"
                  />
                </div>
                <button
                  type="submit"
                  disabled={isSearchingHf || !hfSearchQuery.trim()}
                  className="px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-xs font-bold flex items-center gap-1.5 transition-colors shadow-xs"
                >
                  {isSearchingHf ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Search className="w-3.5 h-3.5" />
                  )}
                  <span>Search Hub</span>
                </button>
              </form>

              {/* Hugging Face Hub Results */}
              {isSearchingHf ? (
                <div className="py-16 text-center text-zinc-500 text-xs flex flex-col items-center justify-center space-y-2">
                  <Loader2 className="w-6 h-6 animate-spin text-indigo-600" />
                  <span>Querying Hugging Face text-generation models...</span>
                </div>
              ) : hfResults.length === 0 ? (
                <div className="py-16 text-center text-zinc-400 text-xs">
                  No Hugging Face models loaded. Type a keyword above and click Search Hub.
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {hfResults.map((hfModel) => {
                    const isSelected = selectedModel === hfModel.model_string_hf || selectedModel === hfModel.model_string_vllm;
                    return (
                      <div
                        key={hfModel.id}
                        className={`p-4 rounded-xl border transition-all flex flex-col justify-between bg-white shadow-xs ${
                          isSelected
                            ? 'border-indigo-600 ring-1 ring-indigo-500/20 bg-indigo-50/30'
                            : 'border-zinc-200 hover:border-zinc-300'
                        }`}
                      >
                        <div>
                          <div className="flex items-center justify-between mb-1">
                            <span className="font-bold text-xs text-zinc-900 truncate max-w-[240px]">
                              {hfModel.name}
                            </span>
                            <div className="flex items-center space-x-2 text-[10px] text-zinc-500 font-mono">
                              <span className="flex items-center gap-0.5">
                                <Download className="w-3 h-3 text-zinc-400" />
                                {hfModel.downloads > 1000 ? `${(hfModel.downloads / 1000).toFixed(0)}k` : hfModel.downloads}
                              </span>
                              <span className="flex items-center gap-0.5">
                                <Heart className="w-3 h-3 text-rose-400 fill-rose-400" />
                                {hfModel.likes}
                              </span>
                            </div>
                          </div>

                          <p className="text-[10px] text-zinc-500 font-mono mb-2">
                            {hfModel.pipeline_tag || 'text-generation'}
                          </p>
                        </div>

                        {/* Deployment Options for this Hugging Face Model */}
                        <div className="pt-2 border-t border-zinc-100 space-y-1.5">
                          <div className="text-[10px] font-semibold text-zinc-400 uppercase tracking-wider">
                            Choose Execution Mode:
                          </div>

                          <div className="grid grid-cols-2 gap-1.5">
                            <button
                              type="button"
                              onClick={() => {
                                onSelectModel(hfModel.model_string_hf);
                                onClose();
                              }}
                              className={`px-2.5 py-1.5 rounded-lg text-[11px] font-medium border text-left flex items-center justify-between transition-colors ${
                                selectedModel === hfModel.model_string_hf
                                  ? 'bg-indigo-600 text-white border-indigo-600 font-bold'
                                  : 'bg-zinc-50 hover:bg-zinc-100 border-zinc-200 text-zinc-700'
                              }`}
                              title="Route through Hugging Face Inference API"
                            >
                              <span>🤗 HF Inference</span>
                              {selectedModel === hfModel.model_string_hf && <Check className="w-3 h-3" />}
                            </button>

                            <button
                              type="button"
                              onClick={() => {
                                onSelectModel(hfModel.model_string_vllm);
                                onClose();
                              }}
                              className={`px-2.5 py-1.5 rounded-lg text-[11px] font-medium border text-left flex items-center justify-between transition-colors ${
                                selectedModel === hfModel.model_string_vllm
                                  ? 'bg-indigo-600 text-white border-indigo-600 font-bold'
                                  : 'bg-zinc-50 hover:bg-zinc-100 border-zinc-200 text-zinc-700'
                              }`}
                              title="Serve model via local vLLM endpoint"
                            >
                              <span>🚀 Local vLLM</span>
                              {selectedModel === hfModel.model_string_vllm && <Check className="w-3 h-3" />}
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Modal Footer: Manual Model String Input & Current Selection */}
        <div className="p-4 border-t border-zinc-200 bg-white flex items-center justify-between shrink-0">
          <div className="flex items-center space-x-2">
            <span className="text-xs text-zinc-500 font-semibold">Active Selection:</span>
            <code className="text-xs font-mono bg-zinc-100 border border-zinc-200 px-2.5 py-1 rounded-lg text-indigo-700 font-bold">
              {selectedModel}
            </code>
          </div>

          <div className="flex items-center space-x-2">
            <input
              type="text"
              value={customModelInput}
              onChange={(e) => setCustomModelInput(e.target.value)}
              placeholder="Or enter custom model ID..."
              className="w-64 bg-zinc-50 border border-zinc-300 rounded-xl px-3 py-1.5 text-xs font-mono text-zinc-900 focus:bg-white focus:outline-none focus:border-indigo-600 focus:ring-1 focus:ring-indigo-600"
            />
            {customModelInput.trim() && (
              <button
                type="button"
                onClick={() => {
                  onSelectModel(customModelInput.trim());
                  onClose();
                }}
                className="px-3 py-1.5 rounded-xl bg-zinc-900 hover:bg-black text-white text-xs font-semibold transition-colors"
              >
                Apply
              </button>
            )}
            <button
              onClick={onClose}
              className="px-4 py-1.5 rounded-xl border border-zinc-200 text-xs font-medium text-zinc-700 hover:bg-zinc-50 transition-colors"
            >
              Done
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
