import React from 'react';
import { Conversation, AgentConfig, NavigationTab } from '../types';
import { 
  MessageSquare, 
  Plus, 
  Trash2, 
  Bot, 
  Sparkles, 
  Wrench, 
  Cpu, 
  Activity,
  Loader2,
  Settings
} from 'lucide-react';
import { useDialog } from '../context/DialogContext';

interface SidebarProps {
  conversations: Conversation[];
  activeConversationId: string | null;
  onSelectConversation: (conversationId: string | null) => void;
  onDeleteConversation: (conversationId: string) => void;
  agents: AgentConfig[];
  currentTab: NavigationTab;
  onSelectTab: (tab: NavigationTab) => void;
  /**
   * Conversation with a turn in flight right now, observed live rather than read from
   * the stored status, which only changes on the server and is re-read once a turn has
   * already ended.
   */
  activeTurnConversationId?: string | null;
}

export const Sidebar: React.FC<SidebarProps> = ({
  conversations,
  activeConversationId,
  onSelectConversation,
  onDeleteConversation,
  agents,
  currentTab,
  onSelectTab,
  activeTurnConversationId,
}) => {
  const dialog = useDialog();

  return (
    <div className="w-72 bg-md-surface-container-low border-r border-md-outline-variant flex flex-col h-full shrink-0 select-none transition-colors">
      {/* Brand Header */}
      <div className="p-4 border-b border-md-outline-variant flex items-center justify-between">
        <div className="flex items-center space-x-2.5">
          <div className="w-8 h-8 rounded-lg bg-md-surface-container-high border border-md-outline-variant flex items-center justify-center text-lg shadow-xs">
            🛶
          </div>
          <div>
            <h1 className="font-bold text-sm text-md-on-surface tracking-tight">
              Kayak
            </h1>
          </div>
        </div>
      </div>

      {/* Navigation Tabs */}
      <div className="p-2 border-b border-md-outline-variant space-y-0.5">
        <button
          onClick={() => onSelectTab('chat')}
          className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
            currentTab === 'chat'
              ? 'bg-md-primary-container text-md-on-primary-container border border-md-primary/40 shadow-xs'
              : 'text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high'
          }`}
        >
          <MessageSquare className={`w-4 h-4 ${currentTab === 'chat' ? 'text-md-primary' : 'text-md-on-surface-variant'}`} />
          <span>Conversations</span>
        </button>

        <button
          onClick={() => onSelectTab('agents')}
          className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
            currentTab === 'agents'
              ? 'bg-md-primary-container text-md-on-primary-container border border-md-primary/40 shadow-xs'
              : 'text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high'
          }`}
        >
          <Bot className={`w-4 h-4 ${currentTab === 'agents' ? 'text-md-primary' : 'text-md-on-surface-variant'}`} />
          <span>Agent Profiles</span>
        </button>

        <button
          onClick={() => onSelectTab('skills')}
          className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
            currentTab === 'skills'
              ? 'bg-md-primary-container text-md-on-primary-container border border-md-primary/40 shadow-xs'
              : 'text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high'
          }`}
        >
          <Sparkles className={`w-4 h-4 ${currentTab === 'skills' ? 'text-md-primary' : 'text-md-on-surface-variant'}`} />
          <span>Skills Directory</span>
        </button>

        <button
          onClick={() => onSelectTab('tools')}
          className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
            currentTab === 'tools'
              ? 'bg-md-primary-container text-md-on-primary-container border border-md-primary/40 shadow-xs'
              : 'text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high'
          }`}
        >
          <Wrench className={`w-4 h-4 ${currentTab === 'tools' ? 'text-md-primary' : 'text-md-on-surface-variant'}`} />
          <span>Custom Tools</span>
        </button>

        <button
          onClick={() => onSelectTab('tasks')}
          className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
            currentTab === 'tasks'
              ? 'bg-md-primary-container text-md-on-primary-container border border-md-primary/40 shadow-xs'
              : 'text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high'
          }`}
        >
          <Activity className={`w-4 h-4 ${currentTab === 'tasks' ? 'text-md-primary' : 'text-md-on-surface-variant'}`} />
          <span>Background Tasks</span>
        </button>

        <button
          onClick={() => onSelectTab('models')}
          className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
            currentTab === 'models'
              ? 'bg-md-primary-container text-md-on-primary-container border border-md-primary/40 shadow-xs'
              : 'text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high'
          }`}
        >
          <Cpu className={`w-4 h-4 ${currentTab === 'models' ? 'text-md-primary' : 'text-md-on-surface-variant'}`} />
          <span>Local Models</span>
        </button>

        <button
          onClick={() => onSelectTab('settings')}
          className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
            currentTab === 'settings'
              ? 'bg-md-primary-container text-md-on-primary-container border border-md-primary/40 shadow-xs'
              : 'text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high'
          }`}
        >
          <Settings className={`w-4 h-4 ${currentTab === 'settings' ? 'text-md-primary' : 'text-md-on-surface-variant'}`} />
          <span>Platform Settings</span>
        </button>
      </div>

      {/* Conversations Section Header */}
      <div className="px-3 pt-3 pb-1 flex items-center justify-between">
        <span className="text-[11px] font-bold uppercase tracking-wider text-md-on-surface-variant">
          Chats ({conversations.length})
        </span>
        <button
          onClick={() => {
            onSelectConversation(null); // Enter draft mode; this also opens the chat tab.
          }}
          className="p-1.5 rounded-lg text-md-primary hover:bg-md-surface-container-high transition-colors flex items-center gap-1 text-xs font-bold cursor-pointer"
          title="New Conversation"
        >
          <Plus className="w-4 h-4" />
          <span>New</span>
        </button>
      </div>

      {/* Conversation List */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {conversations.length === 0 ? (
          <div className="text-center py-8 px-4 text-md-on-surface-variant text-xs">
            No active conversations.<br />Click + to draft a message.
          </div>
        ) : (
          conversations.map((conversation) => {
            const isActive = currentTab === 'chat' && activeConversationId === conversation.id;
            // For the conversation on screen the live signal is authoritative in both
            // directions: it knows a turn has begun before the stored status is re-read,
            // and knows it has ended before the refreshed list arrives. Any other
            // conversation can only be judged by what was last stored.
            const isRunning =
              activeConversationId === conversation.id
                ? activeTurnConversationId === conversation.id
                : conversation.status === 'running';
            return (
              <div
                key={conversation.id}
                // Selecting a conversation already switches to the chat tab and sets
                // the URL. Also calling onSelectTab here re-navigated using the
                // previously selected id, because that state has not flushed yet
                // within the same handler.
                onClick={() => onSelectConversation(conversation.id)}
                className={`group relative flex items-center justify-between px-3 py-2.5 rounded-xl text-xs cursor-pointer transition-all border ${
                  isActive
                    ? 'bg-md-primary-container text-md-on-primary-container border-md-primary/40 shadow-xs font-medium'
                    : 'text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high border-transparent'
                }`}
              >
                <div className="flex items-center space-x-2.5 truncate min-w-0 pr-2">
                  <div className="shrink-0">
                    {conversation.isolated_container ? (
                      <span className="text-xs" title="Isolated Docker Sandbox">🐳</span>
                    ) : (
                      <Cpu className={`w-3.5 h-3.5 ${isActive ? 'text-md-primary' : 'text-md-on-surface-variant'}`} />
                    )}
                  </div>
                  <div className="truncate">
                    <div className="truncate text-xs font-semibold text-md-on-surface">{conversation.title}</div>
                    <div className="text-[10px] text-md-on-surface-variant flex items-center gap-1.5 mt-0.5 font-mono">
                      <span>{conversation.agent_id}</span>
                      {isRunning && (
                        <span
                          className="inline-flex items-center gap-1 text-[10px] text-md-primary font-bold"
                          title="The agent is working on this conversation"
                        >
                          <Loader2 className="w-3 h-3 animate-spin" />
                          <span>working</span>
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                <button
                  onClick={async (event) => {
                    event.stopPropagation();
                    const confirmed = await dialog.confirm({
                      title: 'Delete Conversation',
                      message: `Are you sure you want to delete "${conversation.title}"? All chat history and messages will be permanently deleted.`,
                      confirmText: 'Delete',
                      variant: 'danger',
                    });
                    if (confirmed) {
                      onDeleteConversation(conversation.id);
                    }
                  }}
                  className="opacity-0 group-hover:opacity-100 p-1 text-md-on-surface-variant hover:text-md-error hover:bg-md-surface-container-highest rounded transition-opacity cursor-pointer"
                  title="Delete Conversation"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
