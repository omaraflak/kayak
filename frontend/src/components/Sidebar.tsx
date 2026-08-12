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
}

export const Sidebar: React.FC<SidebarProps> = ({
  conversations,
  activeConversationId,
  onSelectConversation,
  onDeleteConversation,
  agents,
  currentTab,
  onSelectTab,
}) => {
  const dialog = useDialog();

  return (
    <div className="w-72 bg-white dark:bg-zinc-900 border-r border-zinc-200 dark:border-zinc-800 flex flex-col h-full shrink-0 select-none transition-colors">
      {/* Brand Header */}
      <div className="p-4 border-b border-zinc-200 dark:border-zinc-800 flex items-center justify-between">
        <div className="flex items-center space-x-2.5">
          <div className="w-8 h-8 rounded-lg bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 flex items-center justify-center text-lg shadow-sm">
            🛶
          </div>
          <div>
            <h1 className="font-bold text-sm text-zinc-900 dark:text-zinc-100 tracking-tight">
              Kayak
            </h1>
          </div>
        </div>
      </div>

      {/* Navigation Tabs */}
      <div className="p-2 border-b border-zinc-200 dark:border-zinc-800 space-y-0.5">
        <button
          onClick={() => onSelectTab('chat')}
          className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
            currentTab === 'chat'
              ? 'bg-indigo-50/90 dark:bg-indigo-950/80 text-indigo-950 dark:text-indigo-200 border border-indigo-200 dark:border-indigo-800/80 shadow-xs'
              : 'text-zinc-700 dark:text-zinc-300 hover:text-zinc-950 dark:hover:text-zinc-100 hover:bg-zinc-100/70 dark:hover:bg-zinc-800/60'
          }`}
        >
          <MessageSquare className={`w-4 h-4 ${currentTab === 'chat' ? 'text-indigo-600 dark:text-indigo-400' : 'text-zinc-500 dark:text-zinc-400'}`} />
          <span>Conversations</span>
        </button>

        <button
          onClick={() => onSelectTab('agents')}
          className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
            currentTab === 'agents'
              ? 'bg-indigo-50/90 dark:bg-indigo-950/80 text-indigo-950 dark:text-indigo-200 border border-indigo-200 dark:border-indigo-800/80 shadow-xs'
              : 'text-zinc-700 dark:text-zinc-300 hover:text-zinc-950 dark:hover:text-zinc-100 hover:bg-zinc-100/70 dark:hover:bg-zinc-800/60'
          }`}
        >
          <Bot className={`w-4 h-4 ${currentTab === 'agents' ? 'text-indigo-600 dark:text-indigo-400' : 'text-zinc-500 dark:text-zinc-400'}`} />
          <span>Agent Profiles</span>
        </button>

        <button
          onClick={() => onSelectTab('skills')}
          className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
            currentTab === 'skills'
              ? 'bg-indigo-50/90 dark:bg-indigo-950/80 text-indigo-950 dark:text-indigo-200 border border-indigo-200 dark:border-indigo-800/80 shadow-xs'
              : 'text-zinc-700 dark:text-zinc-300 hover:text-zinc-950 dark:hover:text-zinc-100 hover:bg-zinc-100/70 dark:hover:bg-zinc-800/60'
          }`}
        >
          <Sparkles className={`w-4 h-4 ${currentTab === 'skills' ? 'text-indigo-600 dark:text-indigo-400' : 'text-zinc-500 dark:text-zinc-400'}`} />
          <span>Skills Directory</span>
        </button>

        <button
          onClick={() => onSelectTab('tools')}
          className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
            currentTab === 'tools'
              ? 'bg-indigo-50/90 dark:bg-indigo-950/80 text-indigo-950 dark:text-indigo-200 border border-indigo-200 dark:border-indigo-800/80 shadow-xs'
              : 'text-zinc-700 dark:text-zinc-300 hover:text-zinc-950 dark:hover:text-zinc-100 hover:bg-zinc-100/70 dark:hover:bg-zinc-800/60'
          }`}
        >
          <Wrench className={`w-4 h-4 ${currentTab === 'tools' ? 'text-indigo-600 dark:text-indigo-400' : 'text-zinc-500 dark:text-zinc-400'}`} />
          <span>Custom Tools</span>
        </button>

        <button
          onClick={() => onSelectTab('tasks')}
          className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
            currentTab === 'tasks'
              ? 'bg-indigo-50/90 dark:bg-indigo-950/80 text-indigo-950 dark:text-indigo-200 border border-indigo-200 dark:border-indigo-800/80 shadow-xs'
              : 'text-zinc-700 dark:text-zinc-300 hover:text-zinc-950 dark:hover:text-zinc-100 hover:bg-zinc-100/70 dark:hover:bg-zinc-800/60'
          }`}
        >
          <Activity className={`w-4 h-4 ${currentTab === 'tasks' ? 'text-indigo-600 dark:text-indigo-400' : 'text-zinc-500 dark:text-zinc-400'}`} />
          <span>Background Tasks</span>
        </button>

        <button
          onClick={() => onSelectTab('models')}
          className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
            currentTab === 'models'
              ? 'bg-indigo-50/90 dark:bg-indigo-950/80 text-indigo-950 dark:text-indigo-200 border border-indigo-200 dark:border-indigo-800/80 shadow-xs'
              : 'text-zinc-700 dark:text-zinc-300 hover:text-zinc-950 dark:hover:text-zinc-100 hover:bg-zinc-100/70 dark:hover:bg-zinc-800/60'
          }`}
        >
          <Cpu className={`w-4 h-4 ${currentTab === 'models' ? 'text-indigo-600 dark:text-indigo-400' : 'text-zinc-500 dark:text-zinc-400'}`} />
          <span>Local Models</span>
        </button>

        <button
          onClick={() => onSelectTab('settings')}
          className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
            currentTab === 'settings'
              ? 'bg-indigo-50/90 dark:bg-indigo-950/80 text-indigo-950 dark:text-indigo-200 border border-indigo-200 dark:border-indigo-800/80 shadow-xs'
              : 'text-zinc-700 dark:text-zinc-300 hover:text-zinc-950 dark:hover:text-zinc-100 hover:bg-zinc-100/70 dark:hover:bg-zinc-800/60'
          }`}
        >
          <Settings className={`w-4 h-4 ${currentTab === 'settings' ? 'text-indigo-600 dark:text-indigo-400' : 'text-zinc-500 dark:text-zinc-400'}`} />
          <span>Platform Settings</span>
        </button>
      </div>

      {/* Conversations Section Header */}
      <div className="px-3 pt-3 pb-1 flex items-center justify-between">
        <span className="text-[11px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
          Chats ({conversations.length})
        </span>
        <button
          onClick={() => {
            onSelectConversation(null); // Enter draft mode
            onSelectTab('chat');
          }}
          className="p-1.5 rounded-lg text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-950/80 transition-colors flex items-center gap-1 text-xs font-bold cursor-pointer"
          title="New Conversation"
        >
          <Plus className="w-4 h-4" />
          <span>New</span>
        </button>
      </div>

      {/* Conversation List */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {conversations.length === 0 ? (
          <div className="text-center py-8 px-4 text-zinc-500 dark:text-zinc-400 text-xs">
            No active conversations.<br />Click + to draft a message.
          </div>
        ) : (
          conversations.map((conversation) => {
            const isActive = currentTab === 'chat' && activeConversationId === conversation.id;
            const isRunning = conversation.status === 'running';
            return (
              <div
                key={conversation.id}
                onClick={() => {
                  onSelectConversation(conversation.id);
                  onSelectTab('chat');
                }}
                className={`group relative flex items-center justify-between px-3 py-2.5 rounded-xl text-xs cursor-pointer transition-all border ${
                  isActive
                    ? 'bg-indigo-50/90 dark:bg-indigo-950/80 border-indigo-600 dark:border-indigo-400 text-zinc-950 dark:text-zinc-100 font-medium shadow-xs ring-1 ring-indigo-500/20'
                    : 'text-zinc-700 dark:text-zinc-300 hover:text-zinc-950 dark:hover:text-zinc-100 hover:bg-zinc-100/70 dark:hover:bg-zinc-800/60 border-transparent'
                }`}
              >
                <div className="flex items-center space-x-2.5 truncate min-w-0 pr-2">
                  <div className="shrink-0">
                    {conversation.isolated_container ? (
                      <span className="text-xs" title="Isolated Docker Sandbox">🐳</span>
                    ) : (
                      <Cpu className={`w-3.5 h-3.5 ${isActive ? 'text-indigo-600 dark:text-indigo-400' : 'text-zinc-500 dark:text-zinc-400'}`} />
                    )}
                  </div>
                  <div className="truncate">
                    <div className="truncate text-xs font-semibold text-zinc-900 dark:text-zinc-100">{conversation.title}</div>
                    <div className="text-[10px] text-zinc-500 dark:text-zinc-400 flex items-center gap-1.5 mt-0.5 font-mono">
                      <span>{conversation.agent_id}</span>
                      {isRunning && (
                        <span className="inline-flex items-center gap-1 text-[10px] text-teal-700 dark:text-teal-300 font-bold">
                          <span className="w-1.5 h-1.5 rounded-full bg-teal-500 animate-pulse"></span>
                          <span>active</span>
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
                  className="opacity-0 group-hover:opacity-100 p-1 text-zinc-400 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-zinc-200/60 dark:hover:bg-zinc-800 rounded transition-opacity cursor-pointer"
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
