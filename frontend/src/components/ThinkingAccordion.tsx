import React, { useState } from 'react';
import { ChevronDown, ChevronUp, Brain, Loader2, Sparkles } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';

interface ThinkingAccordionProps {
  content?: string | null;
  isThinking?: boolean;
  defaultExpanded?: boolean;
}

export const ThinkingAccordion: React.FC<ThinkingAccordionProps> = ({
  content,
  isThinking = false,
  defaultExpanded = false,
}) => {
  const [isExpanded, setIsExpanded] = useState<boolean>(defaultExpanded || isThinking);

  // If there is no content and not actively thinking, do not render
  if (!content && !isThinking) return null;

  const thinkingMarkdownComponents = {
    h1({ children, ...props }: any) {
      return <h1 className="text-xs font-bold text-zinc-600 dark:text-zinc-300 my-1" {...props}>{children}</h1>;
    },
    h2({ children, ...props }: any) {
      return <h2 className="text-xs font-semibold text-zinc-600 dark:text-zinc-300 my-1" {...props}>{children}</h2>;
    },
    h3({ children, ...props }: any) {
      return <h3 className="text-xs font-medium text-zinc-600 dark:text-zinc-300 my-0.5" {...props}>{children}</h3>;
    },
    p({ children, ...props }: any) {
      return <p className="text-zinc-500 dark:text-zinc-400 text-[12.5px] leading-relaxed mb-1.5 last:mb-0 italic" {...props}>{children}</p>;
    },
    ul({ children, ...props }: any) {
      return <ul className="list-disc pl-4 my-1 text-zinc-500 dark:text-zinc-400 text-[12.5px] space-y-0.5 italic" {...props}>{children}</ul>;
    },
    ol({ children, ...props }: any) {
      return <ol className="list-decimal pl-4 my-1 text-zinc-500 dark:text-zinc-400 text-[12.5px] space-y-0.5 italic" {...props}>{children}</ol>;
    },
    li({ children, ...props }: any) {
      return <li className="text-zinc-500 dark:text-zinc-400 leading-relaxed" {...props}>{children}</li>;
    },
    strong({ children, ...props }: any) {
      return <strong className="font-semibold text-zinc-600 dark:text-zinc-300" {...props}>{children}</strong>;
    },
    code({ node, inline, className, children, ...props }: any) {
      const codeStr = String(children).replace(/\n$/, '');
      return (
        <code className="px-1 py-0.2 rounded bg-zinc-200/70 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 font-mono text-[11px]" {...props}>
          {codeStr}
        </code>
      );
    }
  };

  return (
    <div className="my-2.5 font-sans">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className={`group inline-flex items-center gap-2 px-3 py-1.5 rounded-xl border text-xs transition-all shadow-2xs ${
          isExpanded
            ? 'bg-purple-50/80 dark:bg-purple-950/40 border-purple-200 dark:border-purple-900/60 text-purple-950 dark:text-purple-300 font-medium'
            : 'bg-white dark:bg-zinc-900 hover:bg-zinc-50 dark:hover:bg-zinc-800/80 border-zinc-200 dark:border-zinc-800 text-zinc-500 dark:text-zinc-400 hover:text-zinc-800 dark:hover:text-zinc-200'
        }`}
      >
        <div className="flex items-center gap-1.5">
          {isThinking ? (
            <Loader2 className="w-3.5 h-3.5 text-purple-600 dark:text-purple-400 animate-spin" />
          ) : (
            <Brain className="w-3.5 h-3.5 text-purple-600 dark:text-purple-400" />
          )}
          <span>
            {isThinking ? 'Thinking...' : 'Thought Process'}
          </span>
        </div>

        {isThinking && (
          <span className="inline-flex items-center gap-1 text-[10px] text-purple-700 dark:text-purple-300 font-medium bg-purple-100/80 dark:bg-purple-900/60 border border-purple-200 dark:border-purple-800 px-1.5 py-0.2 rounded-full animate-pulse">
            <span>reasoning</span>
          </span>
        )}

        <div className="text-zinc-400 dark:text-zinc-500 group-hover:text-zinc-700 dark:group-hover:text-zinc-300 transition-colors ml-0.5">
          {isExpanded ? (
            <ChevronUp className="w-3.5 h-3.5" />
          ) : (
            <ChevronDown className="w-3.5 h-3.5" />
          )}
        </div>
      </button>

      {/* Expandable Thinking Content Drawer */}
      {isExpanded && (
        <div className="mt-2 pl-3 border-l-2 border-purple-200/80 dark:border-purple-800/60 animate-fade-in">
          <div className="bg-purple-50/30 dark:bg-purple-950/20 border border-purple-100 dark:border-purple-900/40 rounded-xl p-3.5 max-h-[360px] overflow-y-auto shadow-2xs">
            {content ? (
              <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkMath]}
                rehypePlugins={[rehypeKatex]}
                components={thinkingMarkdownComponents}
              >
                {content}
              </ReactMarkdown>
            ) : (
              <p className="text-xs text-purple-500/80 dark:text-purple-400/80 italic flex items-center gap-1.5">
                <Loader2 className="w-3 h-3 animate-spin" /> Gathering thoughts...
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
