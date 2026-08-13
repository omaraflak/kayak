import React from 'react';

/**
 * A textarea that grows with its content, up to a ceiling, then scrolls.
 *
 * The sizing is done in CSS rather than by measuring: an invisible mirror holding the
 * same text shares a grid cell with the textarea, so the cell -- and therefore the
 * textarea -- is always exactly as tall as the text needs. Measuring `scrollHeight`
 * from an effect is the usual approach and it was not dependable here: it reported
 * hundreds of pixels for a single line, and because such an effect only re-runs on
 * input, one bad reading stuck permanently.
 *
 * Both composers use this, so the draft screen and the conversation cannot drift apart.
 */

interface AutoGrowTextareaProps
  extends Omit<React.TextareaHTMLAttributes<HTMLTextAreaElement>, 'value' | 'onChange' | 'style'> {
  value: string;
  onChange: (value: string) => void;
  /** Tailwind max-height class applied to the growing area. */
  maxHeightClassName?: string;
  /** Tailwind min-height class setting the resting size. */
  minHeightClassName?: string;
  textareaRef?: React.RefObject<HTMLTextAreaElement>;
}

const SHARED_TEXT_CLASSES = 'px-4 py-3 text-[15px] leading-relaxed';

export const AutoGrowTextarea: React.FC<AutoGrowTextareaProps> = ({
  value,
  onChange,
  className = '',
  maxHeightClassName = 'max-h-60',
  minHeightClassName = 'min-h-[5.25rem]',
  textareaRef,
  ...textareaProps
}) => (
  <div className={`grid ${maxHeightClassName} overflow-y-auto`}>
    {/*
      The mirror sizes the grid cell. The trailing newline keeps a row reserved while
      the user is starting a new line, so the box grows as they press Enter rather
      than a character later.
    */}
    <div
      aria-hidden="true"
      className={`invisible col-start-1 row-start-1 whitespace-pre-wrap break-words ${SHARED_TEXT_CLASSES} ${minHeightClassName}`}
    >
      {`${value}\n`}
    </div>

    <textarea
      {...textareaProps}
      ref={textareaRef}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className={`col-start-1 row-start-1 w-full bg-transparent resize-none overflow-hidden focus:outline-none ${SHARED_TEXT_CLASSES} text-md-on-surface placeholder:text-md-on-surface-variant/70 disabled:cursor-not-allowed ${className}`}
    />
  </div>
);
