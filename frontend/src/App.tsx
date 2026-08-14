import React, { useState, useEffect, useCallback } from 'react';
import { Conversation, AgentConfig, NavigationTab } from './types';
import { api } from './api/client';
import { Sidebar } from './components/Sidebar';
import { ChatView } from './components/ChatView';
import { AgentsView } from './components/AgentsView';
import { SkillsView } from './components/SkillsView';
import { ToolsView } from './components/ToolsView';
import { TasksMonitor } from './components/TasksMonitor';
import { SettingsView } from './components/SettingsView';
import { ModelsView } from './components/ModelsView';
import { GlobalVLLMStatusWidget } from './components/GlobalVLLMStatusWidget';
import { useDialog } from './context/DialogContext';
import { parseCurrentUrl, navigateTo } from './utils/router';

export const App: React.FC = () => {
  const initialRoute = parseCurrentUrl();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(
    initialRoute.tab === 'chat' ? initialRoute.conversationId || null : null
  );
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [currentTab, setCurrentTab] = useState<NavigationTab>(initialRoute.tab);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(initialRoute.itemId || null);
  // Agent preselected in the composer when arriving from a "chat with this agent" link.
  const [draftAgentId, setDraftAgentId] = useState<string | null>(null);
  // Settings owns its own form state; the shell only needs to know whether leaving
  // the page would throw work away.
  const [hasUnsavedSettings, setHasUnsavedSettings] = useState(false);
  // Live turn state for the open conversation. The stored status is only re-read when
  // a turn finishes, so it cannot answer "is this one working right now".
  const [activeTurnConversationId, setActiveTurnConversationId] = useState<string | null>(null);
  const dialog = useDialog();

  const handleActivityChange = useCallback(
    (isActive: boolean) => {
      setActiveTurnConversationId(isActive ? activeConversationId : null);
    },
    [activeConversationId]
  );

  /**
   * Refreshes the conversation and agent lists.
   *
   * Deliberately does not touch which conversation is open. This runs after every
   * turn, and re-deriving the selection from the URL here meant any moment where the
   * URL lagged the state -- which a double navigation can cause -- was turned into a
   * jump to a different conversation the next time a message was sent.
   */
  const loadInitialData = useCallback(async () => {
    try {
      const [convs, ags] = await Promise.all([
        api.listConversations(),
        api.listAgents(),
      ]);
      setConversations(convs);
      setAgents(ags);
      return convs;
    } catch (err) {
      console.error('Failed to load initial data:', err);
      return [];
    }
  }, []);

  const handlePopState = useCallback(() => {
    const route = parseCurrentUrl();
    setCurrentTab(route.tab);
    if (route.tab === 'chat') {
      setActiveConversationId(route.conversationId || null);
    } else {
      setSelectedItemId(route.itemId || null);
    }
  }, []);

  // The conversation list is refreshed on navigation and whenever a turn reports a
  // change over SSE (including LLM-generated titles), so no background poll is needed.
  useEffect(() => {
    window.addEventListener('popstate', handlePopState);

    // Resolving the opening route happens exactly once. Landing on /chat with no id
    // opens the most recent conversation; after that the selection belongs to the
    // user, and no later refresh may move it.
    loadInitialData().then((convs) => {
      const route = parseCurrentUrl();
      if (route.tab === 'chat' && !route.conversationId && convs.length > 0) {
        setActiveConversationId(convs[0].id);
        navigateTo('chat', convs[0].id, true);
      }
    });

    return () => {
      window.removeEventListener('popstate', handlePopState);
    };
  }, [handlePopState, loadInitialData]);

  /**
   * Asks before navigating away from Settings with unsaved credentials.
   *
   * Every route out of Settings has to ask, not just the tab buttons: selecting a
   * conversation in the sidebar leaves the page just as completely.
   */
  const confirmLeavingSettings = async (): Promise<boolean> => {
    if (currentTab !== 'settings' || !hasUnsavedSettings) return true;

    const leave = await dialog.confirm({
      title: 'Discard unsaved settings?',
      message: 'You have changes on this page that have not been saved yet.',
      confirmText: 'Discard changes',
      cancelText: 'Stay here',
      variant: 'danger',
    });
    if (leave) setHasUnsavedSettings(false);
    return leave;
  };

  const handleSelectTab = async (tab: NavigationTab) => {
    if (tab !== 'settings' && !(await confirmLeavingSettings())) return;

    // Re-selecting the current tab is a no-op. Re-navigating here would rewrite the
    // URL from state that may not have flushed yet, which is how selecting a
    // conversation could leave the address bar pointing at the previous one.
    if (tab === currentTab) return;

    setCurrentTab(tab);

    if (tab === 'chat') {
      navigateTo('chat', activeConversationId);
      return;
    }

    // A selected item belongs to the tab it was selected in. One piece of state is
    // shared across agents, skills and tools, so carrying it across a tab switch
    // produced URLs like /tools/coding_best_practices -- a skill id under the tools
    // tab, naming a tool that does not exist.
    setSelectedItemId(null);
    navigateTo(tab, null);
  };

  const handleSelectConversation = async (id: string | null) => {
    if (!(await confirmLeavingSettings())) return;
    setActiveConversationId(id);
    setCurrentTab('chat');
    navigateTo('chat', id);
  };

  /** Opens a fresh conversation composer with a specific agent preselected. */
  const handleStartAgentChat = (agentId: string) => {
    setDraftAgentId(agentId);
    setActiveConversationId(null);
    setCurrentTab('chat');
    navigateTo('chat', null);
  };

  const handleCreateConversation = async (data: {
    title?: string;
    agent_id: string;
    initial_message?: string;
  }) => {
    try {
      const newConv = await api.createConversation(data);
      setConversations((prev) => [newConv, ...prev]);
      setDraftAgentId(null);
      setActiveConversationId(newConv.id);
      setCurrentTab('chat');
      navigateTo('chat', newConv.id);
    } catch (err) {
      // Creation genuinely fails now when Docker is down -- every conversation
      // needs its container -- so the reason must reach the user, not the console.
      dialog.alert({
        title: 'Could not start the conversation',
        message: String(err),
        variant: 'danger',
      });
      // Rethrown so the composer knows to keep the drafted prompt.
      throw err;
    }
  };

  /** Adds a conversation created elsewhere (a branch) to the list and opens it. */
  const handleOpenConversation = (conversation: Conversation) => {
    setConversations((prev) =>
      prev.some((item) => item.id === conversation.id) ? prev : [conversation, ...prev]
    );
    setActiveConversationId(conversation.id);
    setCurrentTab('chat');
    navigateTo('chat', conversation.id);
  };

  const handleDeleteConversation = async (id: string) => {
    try {
      await api.deleteConversation(id);
      const updated = conversations.filter((c) => c.id !== id);
      setConversations(updated);
      if (activeConversationId === id) {
        const nextId = updated[0]?.id || null;
        setActiveConversationId(nextId);
        navigateTo('chat', nextId);
      }
    } catch (err) {
      console.error('Failed to delete conversation:', err);
    }
  };

  const handleSelectItem = (tab: NavigationTab, id: string | null) => {
    setSelectedItemId(id);
    navigateTo(tab, id);
  };

  return (
    <div className="flex h-full w-full overflow-hidden bg-md-surface text-md-on-surface font-['Plus_Jakarta_Sans',sans-serif]">
      {/* Navigation Sidebar */}
      <Sidebar
        conversations={conversations}
        activeConversationId={activeConversationId}
        onSelectConversation={handleSelectConversation}
        onDeleteConversation={handleDeleteConversation}
        agents={agents}
        currentTab={currentTab}
        onSelectTab={handleSelectTab}
        activeTurnConversationId={activeTurnConversationId}
      />

      {/* Main Content Pane */}
      <main className="flex-1 flex overflow-hidden">
        {currentTab === 'chat' && (
          <ChatView 
            conversationId={activeConversationId} 
            agents={agents}
            initialDraftAgentId={draftAgentId}
            onCreateConversation={handleCreateConversation}
            onRefreshConversations={loadInitialData}
            onOpenConversation={handleOpenConversation}
            onSelectConversation={handleSelectConversation}
            onActivityChange={handleActivityChange}
          />
        )}

        {currentTab === 'agents' && (
          <AgentsView
            agents={agents}
            selectedId={selectedItemId}
            onSelectId={(id) => handleSelectItem('agents', id)}
            onRefresh={loadInitialData}
            onStartAgentChat={handleStartAgentChat}
          />
        )}

        {currentTab === 'skills' && (
          <SkillsView 
            selectedId={selectedItemId}
            onSelectId={(id) => handleSelectItem('skills', id)}
            onStartAgentChat={handleStartAgentChat}
          />
        )}

        {currentTab === 'tools' && (
          <ToolsView 
            selectedId={selectedItemId}
            onSelectId={(id) => handleSelectItem('tools', id)}
            onStartAgentChat={handleStartAgentChat}
          />
        )}

        {currentTab === 'tasks' && (
          <TasksMonitor />
        )}

        {currentTab === 'models' && (
          <ModelsView />
        )}

        {currentTab === 'settings' && (
          <SettingsView onDirtyChange={setHasUnsavedSettings} />
        )}
      </main>

      {/* Persistent Global vLLM Server Status & Download Progress Widget */}
      <GlobalVLLMStatusWidget />
    </div>
  );
};
