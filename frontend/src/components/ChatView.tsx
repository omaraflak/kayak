import React, { useState, useEffect } from 'react';
import { Conversation, AgentConfig } from '../types';
import { api } from '../api/client';
import { ChatPane } from './ChatPane';
import { 
  Bot, 
  Cpu, 
  Check,
  Sparkles,
  Send
} from 'lucide-react';

interface ChatViewProps {
  conversationId: string | null;
  agents: AgentConfig[];
  onCreateConversation: (data: { title?: string; agent_id: string; isolated_container: boolean; initial_message?: string }) => void;
  onRefreshConversations?: () => void;
}

export const ChatView: React.FC<ChatViewProps> = ({ 
  conversationId, 
  agents,
  onCreateConversation,
  onRefreshConversations
}) => {
  const [conversation, setConversation] = useState<Conversation | null>(null);

  // Draft mode state (when conversationId is null)
  const [draftAgentId, setDraftAgentId] = useState<string>(agents[0]?.id || 'general');
  const [draftUseContainer, setDraftUseContainer] = useState<boolean>(false);
  const [draftInput, setDraftInput] = useState<string>('');

  const loadConversationData = async () => {
    if (!conversationId) {
      setConversation(null);
      return;
    }
    try {
      const data = await api.getConversation(conversationId);
      setConversation(data.conversation);
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
  };

  useEffect(() => {
    loadConversationData();
  }, [conversationId]);

  useEffect(() => {
    if (agents.length > 0 && !agents.some((agent) => agent.id === draftAgentId)) {
      setDraftAgentId(agents[0].id);
    }
  }, [agents]);

  const activeAgent = conversationId 
    ? agents.find((agent) => agent.id === conversation?.agent_id)
    : agents.find((agent) => agent.id === draftAgentId);

  const handleDraftSend = (event?: React.FormEvent) => {
    if (event) event.preventDefault();
    const text = draftInput.trim();
    if (!text) return;

    setDraftInput('');
    onCreateConversation({
      agent_id: draftAgentId,
      isolated_container: draftUseContainer,
      initial_message: text,
    });
  };

  return (
    <div className="flex-1 flex flex-col h-screen bg-zinc-50 overflow-hidden">
      {/* Top Header Bar */}
      <div className="h-16 border-b border-zinc-200 px-8 flex items-center justify-between bg-white shrink-0">
        <div className="flex items-center space-x-3 truncate">
          <h2 className="font-bold text-sm text-zinc-900 truncate max-w-md">
            {conversationId ? (conversation?.title || 'Conversation') : 'New Conversation'}
          </h2>

          {conversationId ? (
            conversation?.isolated_container ? (
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-50 text-blue-700 border border-blue-200 shrink-0">
                <span>🐳</span> Docker Sandbox
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-zinc-100 text-zinc-600 border border-zinc-200 shrink-0">
                <Cpu className="w-3.5 h-3.5 text-zinc-500" /> Host Workspace
              </span>
            )
          ) : null}

          {activeAgent && (
            <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium bg-zinc-100 text-zinc-800 border border-zinc-200 font-mono shrink-0">
              <Bot className="w-3.5 h-3.5 text-zinc-500" /> {activeAgent.name} ({activeAgent.model})
            </span>
          )}
        </div>
      </div>

      {/* Main Area: Draft Setup Widget vs Active Chat Pane */}
      {!conversationId ? (
        <div className="flex-1 flex flex-col justify-between overflow-hidden">
          {/* Centered Agent Profile Selection Widget */}
          <div className="flex-1 flex flex-col justify-center items-center px-6 py-4 overflow-hidden">
            <div className="w-full max-w-2xl flex flex-col space-y-4 max-h-full">
              {/* Header Title */}
              <div className="text-center space-y-1.5 shrink-0">
                <div className="w-12 h-12 rounded-2xl bg-white border border-zinc-200 flex items-center justify-center text-2xl mx-auto shadow-xs">
                  🛶
                </div>
                <h3 className="text-base font-bold text-zinc-900 tracking-tight">
                  How can Kayak help you today?
                </h3>
                <p className="text-xs text-zinc-500 leading-relaxed">
                  Select an agent profile and execution environment below. Type your initial prompt to begin.
                </p>
              </div>

              {/* Agent Profile Selection Widget Container */}
              <div className="bg-white border border-zinc-200 rounded-2xl p-5 shadow-xs flex flex-col space-y-3 overflow-hidden">
                <label className="block text-xs font-bold uppercase tracking-wider text-zinc-700 flex items-center gap-1.5 shrink-0">
                  <Bot className="w-4 h-4 text-indigo-600" /> Choose Agent Profile
                </label>

                {/* Inner Cards Grid: Scrollable with Clean Scrollbar */}
                <div className="overflow-y-auto max-h-[260px] pr-1.5 space-y-2">
                  <div className="grid grid-cols-2 gap-2.5">
                    {agents.map((agent) => {
                      const isSelected = draftAgentId === agent.id;
                      return (
                        <div
                          key={agent.id}
                          onClick={() => setDraftAgentId(agent.id)}
                          className={`p-3 rounded-xl border cursor-pointer transition-all flex flex-col justify-between ${
                            isSelected
                              ? 'bg-indigo-50/80 border border-indigo-600 text-zinc-950 shadow-xs ring-1 ring-indigo-500/20'
                              : 'bg-white border-zinc-200 text-zinc-700 hover:border-zinc-300 hover:bg-zinc-50/70 shadow-xs'
                          }`}
                        >
                          <div>
                            <div className="flex items-center justify-between mb-1">
                              <span className="font-semibold text-xs text-zinc-900">{agent.name}</span>
                              <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${
                                isSelected 
                                  ? 'bg-indigo-100 text-indigo-800 border-indigo-200' 
                                  : 'bg-zinc-100 text-zinc-600 border-zinc-200'
                              }`}>
                                {agent.model.split('/')[1] || agent.model}
                              </span>
                            </div>
                            <p className="text-[11px] text-zinc-500 line-clamp-2 leading-relaxed">
                              {agent.description}
                            </p>
                          </div>

                          <div className="mt-2.5 pt-2 border-t border-zinc-100 flex items-center justify-between text-[10px]">
                            <span className="text-zinc-400 font-mono">
                              {agent.allowed_tools?.length || 0} tools · {agent.preloaded_skills?.length || 0} skills
                            </span>
                            {isSelected && (
                              <span className="inline-flex items-center gap-1 text-indigo-600 font-bold">
                                <Check className="w-3.5 h-3.5 stroke-[3]" /> Selected
                              </span>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Execution Sandbox Isolation Toggle */}
                <div className="pt-3 border-t border-zinc-100 flex items-center justify-between shrink-0">
                  <div>
                    <div className="text-xs font-semibold text-zinc-900 flex items-center gap-1.5">
                      <span>🐳 Docker Sandbox Isolation</span>
                    </div>
                    <div className="text-[11px] text-zinc-500">
                      Run shell commands and code in an isolated container instead of host
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setDraftUseContainer(!draftUseContainer)}
                    className={`w-11 h-6 flex items-center rounded-full p-1 cursor-pointer transition-colors duration-200 ease-in-out ${
                      draftUseContainer ? 'bg-indigo-600' : 'bg-zinc-200'
                    }`}
                  >
                    <div
                      className={`bg-white w-4 h-4 rounded-full shadow-md transform transition-transform duration-200 ease-in-out ${
                        draftUseContainer ? 'translate-x-5' : 'translate-x-0'
                      }`}
                    />
                  </button>
                </div>
              </div>
            </div>
          </div>

          {/* Bottom Docked Message Composer */}
          <div className="p-4 bg-white border-t border-zinc-200 shrink-0">
            <form
              onSubmit={handleDraftSend}
              className="max-w-4xl mx-auto relative bg-zinc-50 border border-zinc-300 rounded-xl overflow-hidden focus-within:bg-white focus-within:border-indigo-600 focus-within:ring-1 focus-within:ring-indigo-600 shadow-xs transition-all"
            >
              <textarea
                value={draftInput}
                onChange={(event) => setDraftInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    handleDraftSend();
                  }
                }}
                rows={2}
                placeholder={`Message ${activeAgent?.name || 'Kayak Agent'}... (Enter to send, Shift+Enter for new line)`}
                className="w-full bg-transparent px-3.5 py-2.5 text-xs text-zinc-900 placeholder-zinc-400 focus:outline-none resize-none leading-relaxed"
              />

              <div className="flex items-center justify-between px-3.5 py-2 bg-zinc-100/50 border-t border-zinc-200">
                <div className="flex items-center space-x-2 text-[11px] text-zinc-500">
                  <Sparkles className="w-3.5 h-3.5 text-indigo-600" />
                  <span className="font-semibold text-zinc-700">{activeAgent?.name}</span>
                  {activeAgent?.model && (
                    <span className="font-mono text-[10px] text-zinc-400">({activeAgent.model})</span>
                  )}
                </div>

                <button
                  type="submit"
                  disabled={!draftInput.trim()}
                  className="inline-flex items-center space-x-1 px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 disabled:hover:bg-indigo-600 text-white transition-colors shadow-xs"
                >
                  <span>Send</span>
                  <Send className="w-3 h-3" />
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : (
        <div className="flex-1 flex flex-col overflow-hidden">
          <ChatPane
            conversationId={conversationId}
            showHeader={false}
            agentId={conversation?.agent_id || 'general'}
            agentName={activeAgent?.name || 'Kayak Agent'}
            agentModel={activeAgent?.model}
            placeholder="Ask anything, execute code, run tasks, type LaTeX formulas... (Enter to send)"
            onRefreshConversations={onRefreshConversations}
          />
        </div>
      )}
    </div>
  );
};
