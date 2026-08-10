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
  argumentsStr: string;
  output?: string;
  isExecuting?: boolean;
  isError?: boolean;
}

export const ToolCallCard: React.FC<ToolCallCardProps> = ({
  name,
  argumentsStr,
  output,
  isExecuting = false,
  isError = false,
}) => {
  const [isOpen, setIsOpen] = useState(false);

  const getToolIcon = (toolName: string) => {
    switch (toolName) {
      case 'run_command':
        return <Terminal className="w-3.5 h-3.5 text-zinc-700" />;
      case 'read_file':
      case 'write_file':
        return <FileText className="w-3.5 h-3.5 text-zinc-700" />;
      case 'edit_file':
        return <Edit3 className="w-3.5 h-3.5 text-zinc-700" />;
      case 'list_directory':
        return <Folder className="w-3.5 h-3.5 text-zinc-700" />;
      case 'web_search':
      case 'fetch_url':
        return <Globe className="w-3.5 h-3.5 text-zinc-700" />;
      case 'start_background_task':
      case 'get_task_status':
      case 'stop_task':
        return <Clock className="w-3.5 h-3.5 text-zinc-700" />;
      case 'spawn_subagent':
      case 'get_subagent_result':
        return <Bot className="w-3.5 h-3.5 text-zinc-700" />;
      default:
        return <Wrench className="w-3.5 h-3.5 text-zinc-700" />;
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
    <div className="my-2 border border-zinc-200 rounded-xl bg-white overflow-hidden shadow-xs text-xs">
      {/* Header Bar */}
      <div
        onClick={() => setIsOpen(!isOpen)}
        className="px-3 py-2 flex items-center justify-between cursor-pointer hover:bg-zinc-50 transition-colors select-none"
      >
        <div className="flex items-center space-x-2 min-w-0">
          <span className="p-1 rounded bg-zinc-100 border border-zinc-200/80">{getToolIcon(name)}</span>
          <span className="font-semibold font-mono text-zinc-900">{name}</span>
          <span className="text-zinc-400 font-mono text-[11px] truncate max-w-xs">
            {argumentsStr.length > 55 ? argumentsStr.slice(0, 55) + '...' : argumentsStr}
          </span>
        </div>

        <div className="flex items-center space-x-2 shrink-0">
          {isExecuting ? (
            <div className="flex items-center space-x-1 text-zinc-700">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              <span className="text-[10px] uppercase font-bold tracking-wider">Running</span>
            </div>
          ) : isError ? (
            <div className="flex items-center space-x-1 text-rose-600">
              <AlertCircle className="w-3.5 h-3.5" />
              <span className="text-[10px] uppercase font-bold tracking-wider">Failed</span>
            </div>
          ) : output ? (
            <div className="flex items-center space-x-1 text-emerald-600">
              <CheckCircle2 className="w-3.5 h-3.5" />
              <span className="text-[10px] uppercase font-bold tracking-wider">Completed</span>
            </div>
          ) : null}

          <div className="text-zinc-400">
            {isOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
          </div>
        </div>
      </div>

      {/* Expanded Accordion Body */}
      {isOpen && (
        <div className="p-3 border-t border-zinc-100 bg-zinc-50/60 space-y-2">
          <div>
            <div className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider mb-1">
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
              <div className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider mb-1">
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
