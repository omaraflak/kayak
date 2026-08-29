import React, { useState } from 'react';
import { Check, Copy, GitBranch, Loader2, RefreshCw, Square, Undo2, Volume2 } from 'lucide-react';

/**
 * Actions offered under an agent message.
 *
 * They stay invisible until the message is hovered or focused, so a long transcript
 * reads as prose rather than as a list of controls. Focus-within matters as much as
 * hover: a hover-only affordance is unreachable from the keyboard.
 */

export type MessageAction = 'revert' | 'retry' | 'branch';

interface MessageActionsProps {
  /** Retry is only meaningful on the turn at the end of the transcript. */
  canRetry: boolean;
  /** Disabled while a turn is in flight, since all three rewrite history. */
  disabled: boolean;
  pendingAction: MessageAction | null;
  content: string;
  onAction: (action: MessageAction) => void;
  /**
   * Reads this message aloud. Absent when no speech model is running, since a
   * control that cannot work is worse than one that is not offered.
   */
  onSpeak?: () => void;
  /** True while this message's audio is playing, so the control offers to stop. */
  isPlaying?: boolean;
  /** True while its audio is still being made and there is nothing to play yet. */
  isPreparing?: boolean;
}

export const MessageActions: React.FC<MessageActionsProps> = ({
  canRetry,
  disabled,
  pendingAction,
  content,
  onAction,
  onSpeak,
  isPlaying,
  isPreparing,
}) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard access can be denied; nothing else to do */
    }
  };

  return (
    <div
      className="flex items-center gap-1 pt-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity"
      // Kept in the tree rather than unmounted so the buttons stay tabbable.
    >
      <ActionButton
        icon={copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
        label={copied ? 'Copied' : 'Copy'}
        title="Copy this response"
        onClick={handleCopy}
      />

      {onSpeak && (
        <ActionButton
          icon={
            isPlaying ? (
              <Square className="w-3 h-3 fill-current" />
            ) : isPreparing ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Volume2 className="w-3 h-3" />
            )
          }
          label={isPlaying ? 'Stop' : isPreparing ? 'Preparing' : 'Speak'}
          title={isPlaying ? 'Stop playing this message' : 'Read this message aloud'}
          onClick={onSpeak}
          disabled={isPreparing}
          danger={isPlaying}
        />
      )}

      <ActionButton
        icon={
          pendingAction === 'revert' ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <Undo2 className="w-3 h-3" />
          )
        }
        label="Revert"
        title="Remove this reply and everything after it, and put the prompt back in the composer"
        disabled={disabled}
        onClick={() => onAction('revert')}
      />

      {canRetry && (
        <ActionButton
          icon={
            pendingAction === 'retry' ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <RefreshCw className="w-3 h-3" />
            )
          }
          label="Retry"
          title="Generate this reply again from the same history"
          disabled={disabled}
          onClick={() => onAction('retry')}
        />
      )}

      <ActionButton
        icon={
          pendingAction === 'branch' ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <GitBranch className="w-3 h-3" />
          )
        }
        label="Branch"
        title="Continue from here in a new conversation, leaving this one untouched"
        disabled={disabled}
        onClick={() => onAction('branch')}
      />
    </div>
  );
};

const ActionButton: React.FC<{
  icon: React.ReactNode;
  label: string;
  title: string;
  disabled?: boolean;
  /** Draws the control as a stop rather than a start. */
  danger?: boolean;
  onClick: () => void;
}> = ({ icon, label, title, disabled, danger, onClick }) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    title={title}
    className={`inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-semibold disabled:opacity-30 disabled:cursor-not-allowed transition-colors cursor-pointer ${
      danger
        ? 'text-md-on-error bg-md-error hover:opacity-90'
        : 'text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high'
    }`}
  >
    {icon}
    <span>{label}</span>
  </button>
);
