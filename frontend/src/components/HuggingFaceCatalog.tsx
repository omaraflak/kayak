import React, { useState, useEffect } from 'react';
import { HuggingFaceModelSearchResult } from '../types';
import { api, errorMessage } from '../api/client';
import { estimateModelSize, judgeFit } from './modelSizing';
import {
  Search,
  Loader2,
  Download,
  Heart,
  Rocket,
  Play,
  CheckCircle2,
  Check,
  AlertTriangle,
  HardDrive,
  ExternalLink,
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
  /** Repositories already downloaded, so the catalog can say what is free to start. */
  cachedModelIds?: Set<string>;
  /** Accelerator memory available, for flagging models that cannot fit. */
  availableVramGB?: number;
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
  cachedModelIds,
  availableVramGB = 0,
}) => {
  const [searchQuery, setSearchQuery] = useState<string>(initialQuery);
  const [results, setResults] = useState<HuggingFaceModelSearchResult[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [lastQuery, setLastQuery] = useState<string>(initialQuery);

  const handleSearch = async (queryToSearch: string) => {
    const trimmed = queryToSearch.trim();
    if (!trimmed || trimmed.length < 2) return;
    setIsLoading(true);
    setSearchError(null);
    setLastQuery(trimmed);
    try {
      const data = await api.searchHuggingFaceModels(trimmed);
      setResults(data);
    } catch (error) {
      // Reporting this as "no models found" would send the user off rewording a query
      // that was never the problem.
      setSearchError(errorMessage(error));
      setResults([]);
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
          <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-md-on-surface-variant" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search Hugging Face Hub (e.g. qwen2.5-coder, deepseek-r1, llama-3.2, mistral, gemma-2)..."
            className="w-full bg-md-surface-container-lowest border border-md-outline-variant rounded-xl pl-9 pr-4 py-2.5 text-xs text-md-on-surface placeholder:text-md-on-surface-variant/70 focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary shadow-xs transition-colors"
          />
        </div>
        <button
          type="submit"
          disabled={isLoading || !searchQuery.trim()}
          className="px-5 py-2.5 rounded-xl bg-md-primary hover:opacity-90 disabled:opacity-50 text-md-on-primary text-xs font-bold flex items-center gap-1.5 transition-opacity shadow-xs shrink-0 cursor-pointer"
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
        <div className="py-16 text-center text-md-on-surface-variant text-xs flex flex-col items-center justify-center space-y-2">
          <Loader2 className="w-6 h-6 animate-spin text-md-primary" />
          <span>Querying Hugging Face text-generation repository catalog...</span>
        </div>
      ) : searchError ? (
        <div className="py-12 px-8 text-center bg-rose-50 dark:bg-rose-950/40 rounded-2xl border border-rose-300 dark:border-rose-800/80 space-y-3">
          <AlertTriangle className="w-6 h-6 text-rose-700 dark:text-rose-300 mx-auto" />
          <p className="text-xs text-rose-900 dark:text-rose-100 leading-relaxed max-w-md mx-auto">
            {searchError}
          </p>
          <button
            type="button"
            onClick={() => handleSearch(lastQuery)}
            className="px-4 py-1.5 rounded-xl bg-md-primary text-md-on-primary text-xs font-bold hover:opacity-90 transition-opacity cursor-pointer"
          >
            Try again
          </button>
        </div>
      ) : results.length === 0 ? (
        <div className="py-16 text-center text-md-on-surface-variant text-xs bg-md-surface-container-low rounded-2xl border border-md-outline-variant p-8">
          No Hugging Face models found matching "{lastQuery}". Try a different keyword above.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5">
          {results.map((hfModel) => {
            const rawModelId = hfModel.id;
            const isCurrentlyServing = activeVllmModelId === rawModelId;
            const isCurrentlyLoading = isVllmLoading && activeVllmModelId === rawModelId;
            const isCached = cachedModelIds?.has(rawModelId) ?? false;
            const estimate = estimateModelSize(rawModelId);
            const fit = judgeFit(estimate, availableVramGB);

            // Staging checks for 'select' mode
            const isHfInferenceStaged = selectedModelString === hfModel.model_string_hf && selectedHfMode === 'hf';
            const isVllmStaged = selectedModelString === hfModel.model_string_vllm && selectedHfMode === 'vllm';
            const isCardStaged = isHfInferenceStaged || isVllmStaged;

            return (
              <div
                key={hfModel.id}
                className={`p-4 rounded-2xl border transition-all flex flex-col justify-between bg-md-surface shadow-xs ${
                  isCurrentlyServing
                    ? 'border-emerald-500 ring-1 ring-emerald-500/30 bg-emerald-100/40 dark:bg-emerald-950/40'
                    : isCardStaged
                    ? 'border-md-primary ring-2 ring-md-primary/30 bg-md-primary-container/40'
                    : 'border-md-outline-variant hover:border-md-outline hover:bg-md-surface-container'
                }`}
              >
                <div className="space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <h5 className="font-bold text-xs text-md-on-surface truncate" title={hfModel.name}>
                        {hfModel.name}
                      </h5>
                      <span className="text-[10px] text-md-on-surface-variant font-mono block truncate">
                        {hfModel.id}
                      </span>
                    </div>

                    <div className="flex items-center space-x-2 text-[10px] text-md-on-surface-variant font-mono shrink-0">
                      <span className="flex items-center gap-0.5" title={`${hfModel.downloads} downloads`}>
                        <Download className="w-3 h-3 text-md-on-surface-variant" />
                        {hfModel.downloads > 1000 ? `${(hfModel.downloads / 1000).toFixed(0)}k` : hfModel.downloads}
                      </span>
                      <span className="flex items-center gap-0.5" title={`${hfModel.likes} likes`}>
                        <Heart className="w-3 h-3 text-rose-500 fill-rose-500" />
                        {hfModel.likes}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5 flex-wrap">
                    {estimate && (
                      <span
                        className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border inline-flex items-center gap-1 ${
                          fit === 'too-large'
                            ? 'bg-rose-100 dark:bg-rose-950/80 text-rose-800 dark:text-rose-200 border-rose-300 dark:border-rose-800/80'
                            : fit === 'tight'
                            ? 'bg-amber-100 dark:bg-amber-950/80 text-amber-800 dark:text-amber-200 border-amber-300 dark:border-amber-800/80'
                            : 'bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant'
                        }`}
                        title={
                          fit === 'too-large'
                            ? `Needs roughly ${estimate.requiredGB} GB; this machine has ${availableVramGB.toFixed(0)} GB of VRAM.`
                            : fit === 'cpu-only'
                            ? 'No GPU detected on this machine.'
                            : `Roughly ${estimate.requiredGB} GB of memory to serve.`
                        }
                      >
                        {(fit === 'too-large' || fit === 'tight') && (
                          <AlertTriangle className="w-2.5 h-2.5" />
                        )}
                        ~{estimate.weightsGB} GB
                      </span>
                    )}
                    {isCached && (
                      <span
                        className="text-[10px] font-semibold px-1.5 py-0.5 rounded border inline-flex items-center gap-1 bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-200 border-emerald-300 dark:border-emerald-800/80"
                        title="Weights are already on this machine"
                      >
                        <HardDrive className="w-2.5 h-2.5" /> Downloaded
                      </span>
                    )}
                  </div>

                  <div className="flex items-center justify-between text-[10px] text-md-on-surface-variant">
                    <span className="font-mono bg-md-surface-container-high px-1.5 py-0.5 rounded border border-md-outline-variant text-md-on-surface">
                      {hfModel.pipeline_tag || 'text-generation'}
                    </span>
                    <a
                      href={`https://huggingface.co/${hfModel.id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-md-primary hover:underline flex items-center gap-0.5 font-semibold"
                    >
                      <span>HF Hub</span>
                      <ExternalLink className="w-2.5 h-2.5" />
                    </a>
                  </div>
                </div>

                {/* Card Actions */}
                <div className="pt-3 mt-3 border-t border-md-outline-variant">
                  {mode === 'deploy' ? (
                    /* Direct deploy action on Local Models page */
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[10px] text-md-on-surface-variant">
                        {isCurrentlyServing
                          ? 'Serving now'
                          : isVllmLoading
                          ? 'Another model is starting'
                          : isCached
                          ? 'Starts without downloading'
                          : 'Downloads on first start'}
                      </span>

                      {isCurrentlyServing ? (
                        <span className="inline-flex items-center gap-1 text-[11px] font-bold text-emerald-800 dark:text-emerald-200">
                          <CheckCircle2 className="w-3.5 h-3.5 stroke-[2.5]" /> Active
                        </span>
                      ) : isCurrentlyLoading ? (
                        <span className="inline-flex items-center gap-1 text-[11px] font-bold text-amber-800 dark:text-amber-200">
                          <Loader2 className="w-3.5 h-3.5 animate-spin" /> Starting...
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => onDeployVLLM?.(hfModel.id)}
                          disabled={isVllmLoading}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-md-primary hover:opacity-90 disabled:opacity-40 text-md-on-primary text-xs font-semibold transition-opacity shadow-2xs cursor-pointer"
                        >
                          <Play className="w-3 h-3 fill-current" />
                          <span>Start Model</span>
                        </button>
                      )}
                    </div>
                  ) : (
                    /* Staging options for ModelSelectorModal */
                    <div className="space-y-1.5">
                      <div className="text-[10px] font-semibold text-md-on-surface-variant uppercase tracking-wider">
                        Choose Execution Mode:
                      </div>

                      <div className="grid grid-cols-2 gap-1.5">
                        <button
                          type="button"
                          onClick={() => onSelectModel?.(hfModel, 'hf')}
                          className={`px-2.5 py-1.5 rounded-lg text-[11px] font-medium border text-left flex items-center justify-between transition-colors cursor-pointer ${
                            isHfInferenceStaged
                              ? 'bg-md-primary text-md-on-primary border-md-primary font-bold shadow-xs'
                              : 'bg-md-surface-container-low hover:bg-md-surface-container-high border-md-outline-variant text-md-on-surface'
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
                              ? 'bg-md-primary text-md-on-primary border-md-primary font-bold shadow-xs'
                              : 'bg-md-tertiary-container hover:opacity-90 border-md-outline-variant text-md-on-tertiary-container'
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

