import React from 'react';
import { Conversation, AgentConfig, NavigationTab } from '../../types';
import { KayakMark } from '../../ui/KayakMark';
import { 
  MessageSquare, 
  Plus, 
  Trash2, 
  Bot, 
  Sparkles, 
  Wrench,
  Cpu,
  AudioLines,
  Brain,
  Loader2,
  Settings,
  type LucideIcon,
} from 'lucide-react';
import { useConversationActivity } from '../../context/ConversationActivityContext';
import { useDialog } from '../../context/DialogContext';

interface SidebarProps {
  conversations: Conversation[];
  activeConversationId: string | null;
  onSelectConversation: (conversationId: string | null) => void;
  onDeleteConversation: (conversationId: string) => void;
  agents: AgentConfig[];
  currentTab: NavigationTab;
  onSelectTab: (tab: NavigationTab) => void;
}

/** One entry in the left-hand navigation. */
const NavItem: React.FC<{
  tab: NavigationTab;
  currentTab: NavigationTab;
  onSelect: (tab: NavigationTab) => void;
  icon: LucideIcon;
  label: string;
}> = ({ tab, currentTab, onSelect, icon: Icon, label }) => {
  const active = currentTab === tab;
  return (
    <button
      onClick={() => onSelect(tab)}
      className={`w-full flex items-center space-x-2.5 px-3 py-2 rounded-xl text-xs font-semibold transition-all cursor-pointer ${
        active
          ? 'bg-md-primary-container text-md-on-primary-container border border-md-primary/40 shadow-xs'
          : 'text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high'
      }`}
    >
      <Icon className={`w-4 h-4 ${active ? 'text-md-primary' : 'text-md-on-surface-variant'}`} />
      <span>{label}</span>
    </button>
  );
};

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
  // Read from the shared stream rather than passed down: the sidebar outlives the
  // chat pane, which is exactly the case the prop could not cover.
  const { runningIds } = useConversationActivity();

  // Sub-agent sessions are excluded: they are work an agent delegated to itself, not
  // conversations you hold. One task can spawn several, which buried the user's own
  // chats under machine-generated ones. They are reached from the task that started
  // them, in the container drawer of the conversation that delegated the work.
  const ownConversations = conversations.filter(
    (conversation) => !conversation.parent_conversation_id
  );

  return (
    <div className="w-72 bg-md-surface-container-low border-r border-md-outline-variant flex flex-col h-full shrink-0 select-none transition-colors">
      {/* Brand Header */}
      <div className="p-4 border-b border-md-outline-variant flex items-center justify-between">
        <div className="flex items-center space-x-2.5">
          <KayakMark className="w-8 h-8 shrink-0 shadow-xs rounded-lg" />
          <div>
            <h1 className="font-bold text-sm text-md-on-surface tracking-tight">
              Kayak
            </h1>
          </div>
        </div>
      </div>

      {/* Navigation.

          Split in two: the places you make something, then the places you set it
          up. The list had grown to seven entries of equal weight, and the two
          that get used constantly sat among five that are configured once. */}
      <div className="p-2 border-b border-md-outline-variant space-y-0.5">
        <NavItem
          tab="chat"
          currentTab={currentTab}
          onSelect={onSelectTab}
          icon={MessageSquare}
          label="Conversations"
        />
        <NavItem
          tab="audio"
          currentTab={currentTab}
          onSelect={onSelectTab}
          icon={AudioLines}
          label="Audio"
        />

        <div className="pt-3 pb-1 px-3">
          <span className="text-[11px] font-bold uppercase tracking-wider text-md-on-surface-variant">
            Configure
          </span>
        </div>

        <NavItem
          tab="agents"
          currentTab={currentTab}
          onSelect={onSelectTab}
          icon={Bot}
          label="Agent Profiles"
        />
        <NavItem
          tab="memories"
          currentTab={currentTab}
          onSelect={onSelectTab}
          icon={Brain}
          label="Memories"
        />
        <NavItem
          tab="skills"
          currentTab={currentTab}
          onSelect={onSelectTab}
          icon={Sparkles}
          label="Skills Directory"
        />
        <NavItem
          tab="tools"
          currentTab={currentTab}
          onSelect={onSelectTab}
          icon={Wrench}
          label="Custom Tools"
        />
        <NavItem
          tab="models"
          currentTab={currentTab}
          onSelect={onSelectTab}
          icon={Cpu}
          label="Local Models"
        />
        <NavItem
          tab="settings"
          currentTab={currentTab}
          onSelect={onSelectTab}
          icon={Settings}
          label="Platform Settings"
        />
      </div>

      <div className="px-3 pt-3 pb-1 flex items-center justify-between">
        <span className="text-[11px] font-bold uppercase tracking-wider text-md-on-surface-variant">
          Chats ({ownConversations.length})
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
        {ownConversations.length === 0 ? (
          <div className="text-center py-8 px-4 text-md-on-surface-variant text-xs">
            No active conversations.<br />Click + to draft a message.
          </div>
        ) : (
          ownConversations.map((conversation) => {
            const isActive = currentTab === 'chat' && activeConversationId === conversation.id;
            // The shared activity stream is authoritative for every conversation and
            // on every page. The stored status cannot be used here: it is only re-read
            // when the list happens to be refreshed.
            const isRunning = runningIds.has(conversation.id);
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
                <div className="flex items-center truncate min-w-0 pr-2">
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
