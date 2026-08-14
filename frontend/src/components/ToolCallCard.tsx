import React, { useState } from 'react';
import { 
  ChevronDown, 
  ChevronRight, 
  Terminal, 
  FileText, 
  Edit3, 
  Folder, 
  Globe, 
  Clock, 
  Bot, 
  CheckCircle2, 
  AlertCircle,
  Loader2,
  Wrench
} from 'lucide-react';
import { CodeBlock } from './CodeBlock';

interface ToolCallCardProps {
  name: string;
  argumentsStr?: string;
  output?: string;
  isExecuting?: boolean;
  isError?: boolean;
}

export const ToolCallCard: React.FC<ToolCallCardProps> = ({
  name,
  argumentsStr: rawArguments,
  output,
  isExecuting = false,
  isError = false,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  // Defensive: an event that arrives without arguments must degrade to an empty
  // preview, not crash the transcript. One undefined here blanked the whole page.
  const argumentsStr = rawArguments ?? '';

  const getToolIcon = (toolName: string) => {
    switch (toolName) {
      case 'run_command':
        return <Terminal className="w-3.5 h-3.5 text-md-on-surface-variant" />;
      case 'read_file':
      case 'write_file':
        return <FileText className="w-3.5 h-3.5 text-md-on-surface-variant" />;
      case 'edit_file':
        return <Edit3 className="w-3.5 h-3.5 text-md-on-surface-variant" />;
      case 'list_directory':
        return <Folder className="w-3.5 h-3.5 text-md-on-surface-variant" />;
      case 'web_search':
      case 'fetch_url':
        return <Globe className="w-3.5 h-3.5 text-md-on-surface-variant" />;
      case 'start_background_task':
      case 'get_task_status':
      case 'stop_task':
        return <Clock className="w-3.5 h-3.5 text-md-on-surface-variant" />;
      case 'spawn_subagent':
      case 'get_subagent_result':
        return <Bot className="w-3.5 h-3.5 text-md-on-surface-variant" />;
      default:
        return <Wrench className="w-3.5 h-3.5 text-md-on-surface-variant" />;
    }
  };

  let formattedArgs = argumentsStr;
  let isJsonArgs = false;
  try {
    const parsed = JSON.parse(argumentsStr);
    formattedArgs = JSON.stringify(parsed, null, 2);
    isJsonArgs = true;
  } catch (e) {
    // Keep raw string
  }

  return (
    <div className="my-2 border border-md-outline-variant rounded-xl bg-md-surface-container-low overflow-hidden shadow-2xs text-xs transition-colors">
      {/* Header Bar */}
      <div
        onClick={() => setIsOpen(!isOpen)}
        className="px-3.5 py-2.5 flex items-center justify-between cursor-pointer hover:bg-md-surface-container-high transition-colors select-none"
      >
        <div className="flex items-center space-x-2.5 min-w-0">
          <span className="p-1.5 rounded-lg bg-md-surface-container-high border border-md-outline-variant shrink-0">{getToolIcon(name)}</span>
          <span className="font-semibold font-mono text-md-on-surface">{name}</span>
          <span className="text-md-on-surface-variant font-mono text-[11px] truncate max-w-xs">
            {argumentsStr.length > 55 ? argumentsStr.slice(0, 55) + '...' : argumentsStr}
          </span>
        </div>

        <div className="flex items-center space-x-2.5 shrink-0">
          {isExecuting ? (
            <div className="inline-flex items-center gap-1 text-[10px] uppercase font-bold tracking-wider text-md-primary bg-md-primary-container border border-md-outline-variant px-2 py-0.5 rounded-full">
              <Loader2 className="w-3 h-3 animate-spin" />
              <span>Running</span>
            </div>
          ) : isError ? (
            <div className="inline-flex items-center gap-1 text-[10px] uppercase font-bold tracking-wider text-md-error bg-md-error-container border border-md-outline-variant px-2 py-0.5 rounded-full">
              <AlertCircle className="w-3 h-3" />
              <span>Failed</span>
            </div>
          ) : output ? (
            <div className="inline-flex items-center gap-1 text-[10px] uppercase font-bold tracking-wider text-emerald-800 dark:text-emerald-200 bg-emerald-100 dark:bg-emerald-950/80 border border-emerald-300 dark:border-emerald-800/80 px-2 py-0.5 rounded-full">
              <CheckCircle2 className="w-3 h-3 stroke-[2.5]" />
              <span>Completed</span>
            </div>
          ) : null}

          <div className="text-md-on-surface-variant">
            {isOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
          </div>
        </div>
      </div>

      {/* Expanded Accordion Body */}
      {isOpen && (
        <div className="p-3.5 border-t border-md-outline-variant bg-md-surface-container-lowest space-y-2.5">
          <div>
            <div className="text-[10px] font-bold text-md-on-surface-variant uppercase tracking-wider mb-1">
              Input Arguments
            </div>
            <CodeBlock
              code={formattedArgs || '{}'}
              language={isJsonArgs ? 'json' : 'text'}
              showHeader={false}
            />
          </div>

          {output && (
            <div>
              <div className="text-[10px] font-bold text-md-on-surface-variant uppercase tracking-wider mb-1">
                Execution Output
              </div>
              <CodeBlock
                code={output}
                language={output.trim().startsWith('{') || output.trim().startsWith('[') ? 'json' : 'text'}
                showHeader={false}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
};
