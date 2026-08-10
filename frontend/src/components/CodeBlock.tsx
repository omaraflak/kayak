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
    <div className={`my-3 rounded-xl border border-zinc-200 bg-white overflow-hidden shadow-xs ${className}`}>
      {showHeader && (
        <div className="flex items-center justify-between px-3.5 py-1.5 bg-zinc-50 border-b border-zinc-200 select-none">
          <span className="text-[10px] font-bold uppercase tracking-wider text-zinc-500 font-mono">
            {rawLang || 'code'}
          </span>
          <button
            type="button"
            onClick={handleCopy}
            className="flex items-center gap-1 text-[11px] text-zinc-500 hover:text-zinc-900 transition-colors p-1 rounded hover:bg-zinc-200/50"
            title="Copy code to clipboard"
          >
            {copied ? (
              <>
                <Check className="w-3 h-3 text-emerald-600" />
                <span className="text-[10px] text-emerald-600 font-medium">Copied</span>
              </>
            ) : (
              <>
                <Copy className="w-3 h-3" />
                <span className="text-[10px] font-medium">Copy</span>
              </>
            )}
          </button>
        </div>
      )}
      <div className="p-3.5 overflow-x-auto bg-zinc-50/50">
        <pre className="font-mono text-[11.5px] leading-relaxed text-zinc-800 m-0">
          <code
            className={`language-${rawLang}`}
            dangerouslySetInnerHTML={{ __html: highlightedHtml }}
          />
        </pre>
      </div>
    </div>
  );
};
