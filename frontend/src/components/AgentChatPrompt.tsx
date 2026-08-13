import React from 'react';
import { MessageSquarePlus, Sparkles } from 'lucide-react';

/**
 * Offer to hand authoring off to the specialist agent in a normal conversation.
 *
 * Creating a tool or skill by describing it is just a chat, so rather than embedding
 * a second chat surface inside the editor, this points at the real one with the right
 * agent already selected.
 */
interface AgentChatPromptProps {
  agentId: string;
  agentLabel: string;
  description: string;
  onStartAgentChat?: (agentId: string) => void;
}

export const AgentChatPrompt: React.FC<AgentChatPromptProps> = ({
  agentId,
  agentLabel,
  description,
  onStartAgentChat,
}) => {
  if (!onStartAgentChat) return null;

  return (
    <div className="px-8 py-2.5 border-b border-md-outline-variant bg-md-primary-container/30 flex items-center justify-between gap-4 shrink-0">
      <div className="flex items-center gap-2 min-w-0">
        <Sparkles className="w-4 h-4 text-md-primary shrink-0" />
        <p className="text-[11px] text-md-on-surface leading-relaxed truncate">
          {description}
        </p>
      </div>
      <button
        type="button"
        onClick={() => onStartAgentChat(agentId)}
        className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold text-md-on-primary bg-md-primary hover:opacity-90 transition-opacity shadow-xs cursor-pointer"
      >
        <MessageSquarePlus className="w-3.5 h-3.5" />
        <span>Chat with {agentLabel}</span>
      </button>
    </div>
  );
};
