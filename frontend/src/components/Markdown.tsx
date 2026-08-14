import React, { useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { CodeBlock } from './CodeBlock';

/**
 * Shared markdown renderer.
 *
 * Every element gets an explicit class. Tailwind's preflight strips the browser's
 * default heading and list styling, so unstyled markdown renders as an undifferentiated
 * wall of text -- which is what the skill preview did, since it relied on `prose`
 * classes from the typography plugin that this project does not install.
 *
 * Chat and the skill preview share this so the same document reads identically in
 * both places.
 */

type MarkdownVariant = 'surface' | 'on-primary';

/**
 * Body text scale. Every block element states its own size, so a font size set on
 * the wrapping element has no effect -- callers that want larger prose pick a scale
 * here instead.
 */
type MarkdownSize = 'default' | 'comfortable';

const SIZE_SCALE: Record<MarkdownSize, { body: string; quote: string; code: string }> = {
  default: { body: 'text-[15px]', quote: 'text-[14px]', code: 'text-[13px]' },
  comfortable: { body: 'text-[16px]', quote: 'text-[15px]', code: 'text-[14px]' },
};

interface MarkdownContentProps {
  children: string;
  /** 'on-primary' inverts text colors for rendering inside a filled bubble. */
  variant?: MarkdownVariant;
  /** 'comfortable' bumps the body scale for long-form reading in chat. */
  size?: MarkdownSize;
  /**
   * Rewrites image and link URLs before rendering. Chat uses this to resolve
   * workspace-relative paths (`plot.png`, `/workspace/plot.png`) to the API
   * endpoint that serves them; without it such images are broken.
   */
  resolveUrl?: (url: string) => string;
  className?: string;
}

function buildComponents(
  variant: MarkdownVariant,
  size: MarkdownSize,
  resolveUrl?: (url: string) => string
) {
  const resolve = (url: unknown): string | undefined =>
    typeof url === 'string' && resolveUrl ? resolveUrl(url) : (url as string | undefined);
  const onPrimary = variant === 'on-primary';
  const body = onPrimary ? 'text-md-on-primary' : 'text-md-on-surface';
  const muted = onPrimary ? 'text-md-on-primary/80' : 'text-md-on-surface-variant';
  const scale = SIZE_SCALE[size];

  return {
    h1: ({ children, ...props }: any) => (
      <h1
        className={`text-xl font-bold ${body} mt-6 mb-3 first:mt-0 tracking-tight border-b border-md-outline-variant pb-1.5`}
        {...props}
      >
        {children}
      </h1>
    ),
    h2: ({ children, ...props }: any) => (
      <h2 className={`text-lg font-bold ${body} mt-5 mb-2 first:mt-0 tracking-tight`} {...props}>
        {children}
      </h2>
    ),
    h3: ({ children, ...props }: any) => (
      <h3 className={`text-base font-semibold ${body} mt-4 mb-1.5 first:mt-0`} {...props}>
        {children}
      </h3>
    ),
    h4: ({ children, ...props }: any) => (
      <h4 className={`text-sm font-semibold ${body} mt-3 mb-1 first:mt-0`} {...props}>
        {children}
      </h4>
    ),
    h5: ({ children, ...props }: any) => (
      <h5 className={`text-sm font-semibold ${muted} mt-3 mb-1 first:mt-0`} {...props}>
        {children}
      </h5>
    ),
    h6: ({ children, ...props }: any) => (
      <h6 className={`text-xs font-semibold uppercase tracking-wider ${muted} mt-3 mb-1 first:mt-0`} {...props}>
        {children}
      </h6>
    ),
    p: ({ children, ...props }: any) => (
      <p className={`mb-3.5 last:mb-0 ${body} ${scale.body} leading-relaxed font-normal`} {...props}>
        {children}
      </p>
    ),
    ul: ({ children, ...props }: any) => (
      <ul className={`list-disc pl-5 my-3 space-y-1.5 ${body} ${scale.body} leading-relaxed`} {...props}>
        {children}
      </ul>
    ),
    ol: ({ children, ...props }: any) => (
      <ol className={`list-decimal pl-5 my-3 space-y-1.5 ${body} ${scale.body} leading-relaxed`} {...props}>
        {children}
      </ol>
    ),
    li: ({ children, ...props }: any) => (
      <li className={`leading-relaxed ${scale.body} ${body} marker:text-md-on-surface-variant`} {...props}>
        {children}
      </li>
    ),
    strong: ({ children, ...props }: any) => (
      <strong className={`font-bold ${body}`} {...props}>
        {children}
      </strong>
    ),
    em: ({ children, ...props }: any) => (
      <em className={`italic ${body}`} {...props}>
        {children}
      </em>
    ),
    a: ({ children, href, ...props }: any) => (
      <a
        className={`${onPrimary ? 'text-md-on-primary' : 'text-md-primary'} underline underline-offset-2 hover:opacity-80 transition-opacity`}
        target="_blank"
        rel="noopener noreferrer"
        href={resolve(href)}
        {...props}
      >
        {children}
      </a>
    ),
    hr: (props: any) => <hr className="my-5 border-md-outline-variant" {...props} />,
    blockquote: ({ children, ...props }: any) => (
      <blockquote
        className={`border-l-[3px] border-md-primary pl-4 py-1.5 my-3.5 ${body} bg-md-primary-container/30 rounded-r-xl italic ${scale.quote} leading-relaxed`}
        {...props}
      >
        {children}
      </blockquote>
    ),
    table: ({ children, ...props }: any) => (
      <div className="my-4 overflow-x-auto rounded-xl border border-md-outline-variant shadow-xs">
        <table className="w-full text-left text-xs border-collapse" {...props}>
          {children}
        </table>
      </div>
    ),
    th: ({ children, ...props }: any) => (
      <th
        className="bg-md-surface-container-high p-3 font-bold text-md-on-surface border-b border-md-outline-variant"
        {...props}
      >
        {children}
      </th>
    ),
    td: ({ children, ...props }: any) => (
      <td className="p-3 border-b border-md-outline-variant text-md-on-surface" {...props}>
        {children}
      </td>
    ),
    img: ({ src, ...props }: any) => (
      <img
        className="my-3 max-w-full rounded-xl border border-md-outline-variant"
        src={resolve(src)}
        {...props}
      />
    ),
    code({ inline, className, children, ...props }: any) {
      const match = /language-(\w+)/.exec(className || '');
      const codeStr = String(children).replace(/\n$/, '');

      // Inline spans inside a filled bubble cannot use surface colors and would
      // otherwise disappear against the primary background.
      if (inline || (!match && !codeStr.includes('\n'))) {
        return (
          <code
            className={
              onPrimary
                ? `px-1.5 py-0.5 rounded bg-md-on-primary/20 border border-md-on-primary/30 text-md-on-primary font-mono ${scale.code}`
                : `px-1.5 py-0.5 rounded-md bg-md-surface-container-high border border-md-outline-variant text-md-on-surface font-mono ${scale.code}`
            }
            {...props}
          >
            {children}
          </code>
        );
      }

      return (
        <div className="my-3.5">
          <CodeBlock language={match ? match[1] : 'text'} code={codeStr} />
        </div>
      );
    },
  };
}

export const MarkdownContent: React.FC<MarkdownContentProps> = ({
  children,
  variant = 'surface',
  size = 'default',
  resolveUrl,
  className,
}) => {
  const components = useMemo(
    () => buildComponents(variant, size, resolveUrl),
    [variant, size, resolveUrl]
  );

  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={components}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
};
