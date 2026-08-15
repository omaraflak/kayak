import React, { useState } from 'react';
import { ChevronDown, ChevronUp, Brain, Loader2, Sparkles } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { CodeProps, ElementProps } from '../../ui/markdownProps';
import { useStickToBottom } from '../../hooks/useStickToBottom';

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
  // Reasoning scrolls inside its own box, so following the transcript is not enough:
  // without this the box shows its first lines while the model keeps writing below.
  const thoughtsRef = useStickToBottom([content], isThinking);

  // If there is no content and not actively thinking, do not render
  if (!content && !isThinking) return null;

  const thinkingMarkdownComponents = {
    h1({ children, ...props }: ElementProps<'h1'>) {
      return <h1 className="text-xs font-bold text-md-on-surface my-1" {...props}>{children}</h1>;
    },
    h2({ children, ...props }: ElementProps<'h2'>) {
      return <h2 className="text-xs font-semibold text-md-on-surface my-1" {...props}>{children}</h2>;
    },
    h3({ children, ...props }: ElementProps<'h3'>) {
      return <h3 className="text-xs font-medium text-md-on-surface my-0.5" {...props}>{children}</h3>;
    },
    p({ children, ...props }: ElementProps<'p'>) {
      return <p className="text-md-on-surface-variant text-[12.5px] leading-relaxed mb-1.5 last:mb-0 italic" {...props}>{children}</p>;
    },
    ul({ children, ...props }: ElementProps<'ul'>) {
      return <ul className="list-disc pl-4 my-1 text-md-on-surface-variant text-[12.5px] space-y-0.5 italic" {...props}>{children}</ul>;
    },
    ol({ children, ...props }: ElementProps<'ol'>) {
      return <ol className="list-decimal pl-4 my-1 text-md-on-surface-variant text-[12.5px] space-y-0.5 italic" {...props}>{children}</ol>;
    },
    li({ children, ...props }: ElementProps<'li'>) {
      return <li className="text-md-on-surface-variant leading-relaxed" {...props}>{children}</li>;
    },
    strong({ children, ...props }: ElementProps<'strong'>) {
      return <strong className="font-semibold text-md-on-surface" {...props}>{children}</strong>;
    },
    code({ children, ...props }: CodeProps) {
      const codeStr = String(children).replace(/\n$/, '');
      return (
        <code className="px-1 py-0.2 rounded bg-md-surface-container-high text-md-on-surface font-mono text-[11px] border border-md-outline-variant" {...props}>
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
        className={`group inline-flex items-center gap-2 px-3 py-1.5 rounded-xl border text-xs transition-all shadow-2xs cursor-pointer ${
          isExpanded
            ? 'bg-md-tertiary-container border-md-outline-variant text-md-on-tertiary-container font-medium'
            : 'bg-md-surface-container-low hover:bg-md-surface-container-high border-md-outline-variant text-md-on-surface-variant hover:text-md-on-surface'
        }`}
      >
        <div className="flex items-center gap-1.5">
          {isThinking ? (
            <Loader2 className="w-3.5 h-3.5 text-md-primary animate-spin" />
          ) : (
            <Brain className="w-3.5 h-3.5 text-md-primary" />
          )}
          <span>
            {isThinking ? 'Thinking...' : 'Thought Process'}
          </span>
        </div>

        {isThinking && (
          <span className="inline-flex items-center gap-1 text-[10px] text-md-on-tertiary-container font-medium bg-md-tertiary-container border border-md-outline-variant px-1.5 py-0.2 rounded-full animate-pulse">
            <span>reasoning</span>
          </span>
        )}

        <div className="text-md-on-surface-variant group-hover:text-md-on-surface transition-colors ml-0.5">
          {isExpanded ? (
            <ChevronUp className="w-3.5 h-3.5" />
          ) : (
            <ChevronDown className="w-3.5 h-3.5" />
          )}
        </div>
      </button>

      {/* Expandable Thinking Content Drawer */}
      {isExpanded && (
        <div className="mt-2 pl-3 border-l-2 border-md-primary/40 animate-fade-in">
          <div
            ref={thoughtsRef}
            className="bg-md-surface-container-low border border-md-outline-variant rounded-xl p-3.5 max-h-[360px] overflow-y-auto shadow-2xs"
          >
            {content ? (
              <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkMath]}
                rehypePlugins={[rehypeKatex]}
                components={thinkingMarkdownComponents}
              >
                {content}
              </ReactMarkdown>
            ) : (
              <p className="text-xs text-md-on-surface-variant italic flex items-center gap-1.5">
                <Loader2 className="w-3 h-3 animate-spin" /> Gathering thoughts...
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
