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
            ? 'bg-md-primary-container border-md-primary text-md-on-primary-container font-semibold'
            : 'bg-md-surface-container-low hover:bg-md-surface-container-high border-md-outline-variant text-md-on-surface'
        }`}
      >
        <div className="flex items-center gap-1.5">
          <Wrench className="w-3.5 h-3.5 text-md-primary" />
          <span>
            {totalCount} {totalCount === 1 ? 'tool call' : 'tool calls'} executed
          </span>
        </div>

        {hasErrors ? (
          <span className="inline-flex items-center gap-1 text-[10px] text-md-error font-semibold bg-md-error-container border border-md-outline-variant px-1.5 py-0.5 rounded-full">
            <AlertCircle className="w-3 h-3" />
            <span>failed</span>
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-[10px] text-emerald-800 dark:text-emerald-200 font-semibold bg-emerald-100 dark:bg-emerald-950/80 border border-emerald-300 dark:border-emerald-800/80 px-1.5 py-0.5 rounded-full">
            <CheckCircle2 className="w-3 h-3 stroke-[2.5]" />
            <span>completed</span>
          </span>
        )}

        <div className="text-md-on-surface-variant group-hover:text-md-on-surface transition-colors ml-1">
          {isExpanded ? (
            <ChevronUp className="w-3.5 h-3.5" />
          ) : (
            <ChevronDown className="w-3.5 h-3.5" />
          )}
        </div>
      </button>

      {/* Expandable Tool Call Details Drawer */}
      {isExpanded && (
        <div className="mt-2.5 space-y-2 pl-2 border-l-2 border-md-primary/40 animate-fade-in">
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
