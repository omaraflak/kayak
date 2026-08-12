import React, { useState, useEffect } from 'react';
import { HuggingFaceModelSearchResult } from '../types';
import { api } from '../api/client';
import { 
  Search, 
  Loader2, 
  Download, 
  Heart, 
  Rocket, 
  Play, 
  CheckCircle2, 
  Check, 
  Sparkles,
  ExternalLink
} from 'lucide-react';

export interface HuggingFaceCatalogProps {
  mode?: 'deploy' | 'select'; // 'deploy' is for direct vLLM start on Models page; 'select' is for ModelSelectorModal
  onDeployVLLM?: (modelId: string) => void;
  onSelectModel?: (model: HuggingFaceModelSearchResult, execMode: 'hf' | 'vllm') => void;
  selectedModelString?: string;
  selectedHfMode?: 'hf' | 'vllm' | null;
  activeVllmModelId?: string | null;
  isVllmLoading?: boolean;
  initialQuery?: string;
}

export const HuggingFaceCatalog: React.FC<HuggingFaceCatalogProps> = ({
  mode = 'deploy',
  onDeployVLLM,
  onSelectModel,
  selectedModelString,
  selectedHfMode,
  activeVllmModelId,
  isVllmLoading = false,
  initialQuery = 'qwen2.5-coder',
}) => {
  const [searchQuery, setSearchQuery] = useState<string>(initialQuery);
  const [results, setResults] = useState<HuggingFaceModelSearchResult[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);

  const handleSearch = async (queryToSearch: string) => {
    const trimmed = queryToSearch.trim();
    if (!trimmed || trimmed.length < 2) return;
    setIsLoading(true);
    try {
      const data = await api.searchHuggingFaceModels(trimmed);
      setResults(data);
    } catch (error) {
      console.error('Failed to search Hugging Face Hub:', error);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (results.length === 0 && initialQuery) {
      handleSearch(initialQuery);
    }
  }, []);

  return (
    <div className="space-y-4 font-sans">
      {/* Search Input Bar */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          handleSearch(searchQuery);
        }}
        className="flex gap-2"
      >
        <div className="relative flex-1">
          <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-400 dark:text-zinc-500" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search Hugging Face Hub (e.g. qwen2.5-coder, deepseek-r1, llama-3.2, mistral, gemma-2)..."
            className="w-full bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded-xl pl-9 pr-4 py-2.5 text-xs text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 dark:placeholder-zinc-500 focus:outline-none focus:border-indigo-600 dark:focus:border-indigo-500 focus:ring-1 focus:ring-indigo-600 shadow-xs transition-colors"
          />
        </div>
        <button
          type="submit"
          disabled={isLoading || !searchQuery.trim()}
          className="px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white text-xs font-bold flex items-center gap-1.5 transition-colors shadow-xs shrink-0 cursor-pointer"
        >
          {isLoading ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Search className="w-3.5 h-3.5" />
          )}
          <span>Search Hub</span>
        </button>
      </form>

      {/* Results Container */}
      {isLoading ? (
        <div className="py-16 text-center text-zinc-500 dark:text-zinc-400 text-xs flex flex-col items-center justify-center space-y-2">
          <Loader2 className="w-6 h-6 animate-spin text-indigo-600 dark:text-indigo-400" />
          <span>Querying Hugging Face text-generation repository catalog...</span>
        </div>
      ) : results.length === 0 ? (
        <div className="py-16 text-center text-zinc-400 dark:text-zinc-500 text-xs bg-white dark:bg-zinc-900 rounded-2xl border border-zinc-200 dark:border-zinc-800 p-8">
          No Hugging Face models found matching "{searchQuery}". Try a different keyword above.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5">
          {results.map((hfModel) => {
            const rawModelId = hfModel.id;
            const isCurrentlyServing = activeVllmModelId === rawModelId;
            const isCurrentlyLoading = isVllmLoading && activeVllmModelId === rawModelId;

            // Staging checks for 'select' mode
            const isHfInferenceStaged = selectedModelString === hfModel.model_string_hf && selectedHfMode === 'hf';
            const isVllmStaged = selectedModelString === hfModel.model_string_vllm && selectedHfMode === 'vllm';
            const isCardStaged = isHfInferenceStaged || isVllmStaged;

            return (
              <div
                key={hfModel.id}
                className={`p-4 rounded-2xl border transition-all flex flex-col justify-between bg-white dark:bg-zinc-900 shadow-xs ${
                  isCurrentlyServing
                    ? 'border-emerald-500/80 ring-1 ring-emerald-500/20 bg-emerald-50/10 dark:bg-emerald-950/20'
                    : isCardStaged
                    ? 'border-indigo-600 dark:border-indigo-500 ring-2 ring-indigo-500/20 bg-indigo-50/20 dark:bg-indigo-950/30'
                    : 'border-zinc-200 dark:border-zinc-800 hover:border-zinc-300 dark:hover:border-zinc-700'
                }`}
              >
                <div className="space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <h5 className="font-bold text-xs text-zinc-900 dark:text-zinc-100 truncate" title={hfModel.name}>
                        {hfModel.name}
                      </h5>
                      <span className="text-[10px] text-zinc-500 dark:text-zinc-400 font-mono block truncate">
                        {hfModel.id}
                      </span>
                    </div>

                    <div className="flex items-center space-x-2 text-[10px] text-zinc-500 dark:text-zinc-400 font-mono shrink-0">
                      <span className="flex items-center gap-0.5" title={`${hfModel.downloads} downloads`}>
                        <Download className="w-3 h-3 text-zinc-400 dark:text-zinc-500" />
                        {hfModel.downloads > 1000 ? `${(hfModel.downloads / 1000).toFixed(0)}k` : hfModel.downloads}
                      </span>
                      <span className="flex items-center gap-0.5" title={`${hfModel.likes} likes`}>
                        <Heart className="w-3 h-3 text-rose-400 fill-rose-400" />
                        {hfModel.likes}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center justify-between text-[10px] text-zinc-400 dark:text-zinc-500">
                    <span className="font-mono bg-zinc-100 dark:bg-zinc-800 px-1.5 py-0.5 rounded border border-zinc-200/80 dark:border-zinc-700 text-zinc-600 dark:text-zinc-400">
                      {hfModel.pipeline_tag || 'text-generation'}
                    </span>
                    <a
                      href={`https://huggingface.co/${hfModel.id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 flex items-center gap-0.5 hover:underline"
                    >
                      <span>HF Hub</span>
                      <ExternalLink className="w-2.5 h-2.5" />
                    </a>
                  </div>
                </div>

                {/* Card Actions */}
                <div className="pt-3 mt-3 border-t border-zinc-100 dark:border-zinc-800">
                  {mode === 'deploy' ? (
                    /* Direct deploy action on Local Models page */
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] font-mono text-zinc-400 dark:text-zinc-500">
                        {isCurrentlyServing ? 'Active in vLLM' : 'vLLM Ready'}
                      </span>

                      {isCurrentlyServing ? (
                        <span className="inline-flex items-center gap-1 text-[11px] font-bold text-emerald-600 dark:text-emerald-400">
                          <CheckCircle2 className="w-3.5 h-3.5" /> Active
                        </span>
                      ) : isCurrentlyLoading ? (
                        <span className="inline-flex items-center gap-1 text-[11px] font-bold text-amber-600 dark:text-amber-400">
                          <Loader2 className="w-3.5 h-3.5 animate-spin" /> Starting...
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => onDeployVLLM?.(hfModel.id)}
                          disabled={isVllmLoading}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-zinc-900 dark:bg-zinc-800 hover:bg-indigo-600 dark:hover:bg-indigo-600 disabled:opacity-40 text-white text-xs font-semibold transition-colors shadow-2xs cursor-pointer border border-zinc-700"
                        >
                          <Play className="w-3 h-3 fill-white" />
                          <span>Start Model</span>
                        </button>
                      )}
                    </div>
                  ) : (
                    /* Staging options for ModelSelectorModal */
                    <div className="space-y-1.5">
                      <div className="text-[10px] font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wider">
                        Choose Execution Mode:
                      </div>

                      <div className="grid grid-cols-2 gap-1.5">
                        <button
                          type="button"
                          onClick={() => onSelectModel?.(hfModel, 'hf')}
                          className={`px-2.5 py-1.5 rounded-lg text-[11px] font-medium border text-left flex items-center justify-between transition-colors cursor-pointer ${
                            isHfInferenceStaged
                              ? 'bg-indigo-600 dark:bg-indigo-500 text-white border-indigo-600 dark:border-indigo-500 font-bold shadow-xs'
                              : 'bg-zinc-50 dark:bg-zinc-800 hover:bg-zinc-100 dark:hover:bg-zinc-750 border-zinc-200 dark:border-zinc-700 text-zinc-800 dark:text-zinc-200'
                          }`}
                          title="Stage for Hugging Face Serverless Inference API"
                        >
                          <span>🤗 HF API</span>
                          {isHfInferenceStaged && <Check className="w-3 h-3 stroke-[3]" />}
                        </button>

                        <button
                          type="button"
                          onClick={() => onSelectModel?.(hfModel, 'vllm')}
                          className={`px-2.5 py-1.5 rounded-lg text-[11px] font-medium border text-left flex items-center justify-between transition-colors cursor-pointer ${
                            isVllmStaged
                              ? 'bg-indigo-600 dark:bg-indigo-500 text-white border-indigo-600 dark:border-indigo-500 font-bold shadow-xs'
                              : 'bg-indigo-50 dark:bg-indigo-950/80 hover:bg-indigo-100 dark:hover:bg-indigo-900/80 border-indigo-200 dark:border-indigo-800/80 text-indigo-800 dark:text-indigo-200'
                          }`}
                          title="Stage for local vLLM Docker container serving"
                        >
                          <span className="flex items-center gap-1 font-semibold">
                            <Rocket className="w-3 h-3" /> Local vLLM
                          </span>
                          {isVllmStaged && <Check className="w-3 h-3 stroke-[3]" />}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
