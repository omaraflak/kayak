import React, { useState } from 'react';
import { ChevronDown, ChevronUp, Wrench, CheckCircle2, AlertCircle } from 'lucide-react';
import { ToolCallCard } from './ToolCallCard';
import { ToolCallItem } from '../types';

interface ToolExecutionItem {
  id: string;
  name: string;
  argumentsStr: string;
  output?: string;
  isError?: boolean;
}

interface ToolCallsAccordionProps {
  toolCalls: ToolExecutionItem[];
  defaultExpanded?: boolean;
}

export const ToolCallsAccordion: React.FC<ToolCallsAccordionProps> = ({
  toolCalls,
  defaultExpanded = false,
}) => {
  const [isExpanded, setIsExpanded] = useState<boolean>(defaultExpanded);

  if (!toolCalls || toolCalls.length === 0) return null;

  const totalCount = toolCalls.length;
  const hasErrors = toolCalls.some((t) => t.isError);

  return (
    <div className="my-3 font-sans">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className={`group inline-flex items-center gap-2 px-3 py-1.5 rounded-xl border text-xs transition-all shadow-2xs cursor-pointer ${
          isExpanded
            ? 'bg-zinc-100 dark:bg-zinc-800 border-zinc-300 dark:border-zinc-700 text-zinc-900 dark:text-zinc-100 font-semibold'
            : 'bg-white dark:bg-zinc-900 hover:bg-zinc-100/80 dark:hover:bg-zinc-800 border-zinc-200 dark:border-zinc-800 text-zinc-700 dark:text-zinc-300 hover:text-zinc-900 dark:hover:text-zinc-100'
        }`}
      >
        <div className="flex items-center gap-1.5">
          <Wrench className="w-3.5 h-3.5 text-indigo-600 dark:text-indigo-400" />
          <span>
            {totalCount} {totalCount === 1 ? 'tool call' : 'tool calls'} executed
          </span>
        </div>

        {hasErrors ? (
          <span className="inline-flex items-center gap-1 text-[10px] text-rose-700 dark:text-rose-300 font-semibold bg-rose-50 dark:bg-rose-950/80 border border-rose-200 dark:border-rose-800/80 px-1.5 py-0.5 rounded-full">
            <AlertCircle className="w-3 h-3" />
            <span>failed</span>
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-[10px] text-emerald-700 dark:text-emerald-300 font-semibold bg-emerald-50 dark:bg-emerald-950/80 border border-emerald-200 dark:border-emerald-800/80 px-1.5 py-0.5 rounded-full">
            <CheckCircle2 className="w-3 h-3" />
            <span>completed</span>
          </span>
        )}

        <div className="text-zinc-400 dark:text-zinc-500 group-hover:text-zinc-700 dark:group-hover:text-zinc-200 transition-colors ml-1">
          {isExpanded ? (
            <ChevronUp className="w-3.5 h-3.5" />
          ) : (
            <ChevronDown className="w-3.5 h-3.5" />
          )}
        </div>
      </button>

      {/* Expandable Tool Call Details Drawer */}
      {isExpanded && (
        <div className="mt-2.5 space-y-2 pl-2 border-l-2 border-indigo-200/60 dark:border-indigo-800/60 animate-fade-in">
          {toolCalls.map((toolCall, index) => (
            <ToolCallCard
              key={toolCall.id || index}
              name={toolCall.name}
              argumentsStr={toolCall.argumentsStr}
              output={toolCall.output}
              isError={toolCall.isError}
            />
          ))}
        </div>
      )}
    </div>
  );
};
