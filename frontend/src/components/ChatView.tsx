import React, { useState, useEffect } from 'react';
import { Conversation, AgentConfig } from '../types';
import { api } from '../api/client';
import { ChatPane } from './ChatPane';
import { 
  Bot, 
  Cpu, 
  Check
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

  const activeAgent = conversationId 
    ? agents.find((agent) => agent.id === conversation?.agent_id)
    : agents.find((agent) => agent.id === draftAgentId);

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
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="flex-1 overflow-y-auto px-8 py-8 space-y-6 max-w-2xl w-full mx-auto">
            <div className="text-center space-y-2 pt-4">
              <div className="w-14 h-14 rounded-2xl bg-zinc-100 border border-zinc-200 flex items-center justify-center text-3xl mx-auto shadow-sm">
                🛶
              </div>
              <h3 className="text-lg font-bold text-zinc-900 tracking-tight">
                How can Kayak help you today?
              </h3>
              <p className="text-xs text-zinc-500 max-w-md mx-auto leading-relaxed">
                Select an agent profile and execution environment below. Type your initial prompt to begin.
              </p>
            </div>

            {/* Agent Profile Selection Cards */}
            <div className="bg-white border border-zinc-200 rounded-2xl p-5 shadow-xs space-y-3">
              <label className="block text-xs font-bold uppercase tracking-wider text-zinc-700 flex items-center gap-1.5">
                <Bot className="w-4 h-4 text-indigo-600" /> Choose Agent Profile
              </label>
              <div className="grid grid-cols-2 gap-3">
                {agents.map((agent) => {
                  const isSelected = draftAgentId === agent.id;
                  return (
                    <div
                      key={agent.id}
                      onClick={() => setDraftAgentId(agent.id)}
                      className={`p-3.5 rounded-xl border cursor-pointer transition-all flex flex-col justify-between ${
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

              {/* Execution Sandbox Isolation Toggle */}
              <div className="pt-3 border-t border-zinc-100 flex items-center justify-between">
                <div>
                  <div className="text-xs font-semibold text-zinc-900 flex items-center gap-1.5">
                    <span>🐳 Docker Sandbox Isolation</span>
                  </div>
                  <div className="text-[11px] text-zinc-500">
                    Run agent shell commands and code in an isolated container instead of host
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

          {/* Unified Composer for draft initial message */}
          <div className="max-w-4xl w-full mx-auto pb-4 px-4">
            <ChatPane
              conversationId={null}
              showHeader={false}
              agentId={draftAgentId}
              agentName={activeAgent?.name || 'Kayak Agent'}
              agentModel={activeAgent?.model}
              placeholder="Type your initial prompt to start this conversation... (Enter to send)"
              onSendMessage={async (content) => {
                onCreateConversation({
                  agent_id: draftAgentId,
                  isolated_container: draftUseContainer,
                  initial_message: content,
                });
              }}
              onRefreshConversations={onRefreshConversations}
            />
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
