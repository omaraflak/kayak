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
  const dialog = useDialog();

  const loadInitialData = async () => {
    try {
      const [convs, ags] = await Promise.all([
        api.listConversations(),
        api.listAgents(),
      ]);
      setConversations(convs);
      setAgents(ags);

      const route = parseCurrentUrl();
      if (route.tab === 'chat') {
        if (route.conversationId) {
          setActiveConversationId(route.conversationId);
        } else if (convs.length > 0 && !activeConversationId) {
          // If at root and has conversations, select first
          setActiveConversationId(convs[0].id);
          navigateTo('chat', convs[0].id, true);
        }
      }
    } catch (err) {
      console.error('Failed to load initial data:', err);
    }
  };

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
    loadInitialData();

    return () => {
      window.removeEventListener('popstate', handlePopState);
    };
  }, [handlePopState]);

  const handleSelectTab = async (tab: NavigationTab) => {
    // Leaving Settings with an unsaved credential would silently discard it.
    if (currentTab === 'settings' && tab !== 'settings' && hasUnsavedSettings) {
      const leave = await dialog.confirm({
        title: 'Discard unsaved settings?',
        message: 'You have changes on this page that have not been saved yet.',
        confirmText: 'Discard changes',
        cancelText: 'Stay here',
        variant: 'danger',
      });
      if (!leave) return;
      setHasUnsavedSettings(false);
    }

    // Re-selecting the current tab keeps whatever is open in it.
    if (tab === currentTab) {
      navigateTo(tab, tab === 'chat' ? activeConversationId : selectedItemId);
      return;
    }

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

  const handleSelectConversation = (id: string | null) => {
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
    isolated_container: boolean;
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
      console.error('Failed to create conversation:', err);
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
          <SettingsView
            onDirtyChange={setHasUnsavedSettings}
            onOpenLocalModels={() => handleSelectTab('models')}
          />
        )}
      </main>

      {/* Persistent Global vLLM Server Status & Download Progress Widget */}
      <GlobalVLLMStatusWidget />
    </div>
  );
};
