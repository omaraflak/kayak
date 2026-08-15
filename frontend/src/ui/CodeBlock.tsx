import React, { useState } from 'react';
import Prism from 'prismjs';
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-yaml';
import 'prismjs/components/prism-typescript';
import 'prismjs/components/prism-javascript';
import 'prismjs/components/prism-sql';
import 'prismjs/components/prism-markdown';
import { Copy, Check } from 'lucide-react';

interface CodeBlockProps {
  code: string;
  language?: string;
  className?: string;
  showHeader?: boolean;
}

export const CodeBlock: React.FC<CodeBlockProps> = ({
  code,
  language = 'text',
  className = '',
  showHeader = true,
}) => {
  const [copied, setCopied] = useState(false);

  const cleanCode = (code || '').trimEnd();
  const rawLang = (language || 'text').toLowerCase().replace(/^language-/, '');

  let highlightedHtml = '';
  try {
    const grammar = Prism.languages[rawLang] || Prism.languages.text || Prism.languages.javascript;
    highlightedHtml = Prism.highlight(cleanCode, grammar, rawLang);
  } catch (e) {
    highlightedHtml = cleanCode;
  }

  const handleCopy = () => {
    navigator.clipboard.writeText(cleanCode);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className={`my-3 rounded-xl border border-md-outline-variant bg-md-surface-container-lowest overflow-hidden shadow-xs ${className}`}>
      {showHeader && (
        <div className="flex items-center justify-between px-3.5 py-2 bg-md-surface-container-high border-b border-md-outline-variant select-none">
          <span className="text-[11px] font-bold uppercase tracking-wider text-md-on-surface-variant font-mono">
            {rawLang || 'code'}
          </span>
          <button
            type="button"
            onClick={handleCopy}
            className="flex items-center gap-1.5 text-xs text-md-on-surface-variant hover:text-md-on-surface transition-colors px-2 py-1 rounded-lg hover:bg-md-surface-container-highest cursor-pointer font-medium"
            title="Copy code to clipboard"
          >
            {copied ? (
              <>
                <Check className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400 stroke-[2.5]" />
                <span className="text-[11px] text-emerald-600 dark:text-emerald-400 font-bold">Copied</span>
              </>
            ) : (
              <>
                <Copy className="w-3.5 h-3.5" />
                <span className="text-[11px]">Copy</span>
              </>
            )}
          </button>
        </div>
      )}
      <div className="p-4 overflow-x-auto bg-md-surface-container-lowest">
        <pre className="font-mono text-[12px] leading-relaxed text-md-on-surface m-0">
          <code
            className={`language-${rawLang}`}
            dangerouslySetInnerHTML={{ __html: highlightedHtml }}
          />
        </pre>
      </div>
    </div>
  );
};
