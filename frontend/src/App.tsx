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

  useEffect(() => {
    window.addEventListener('popstate', handlePopState);
    loadInitialData();

    const interval = setInterval(async () => {
      try {
        const convs = await api.listConversations();
        setConversations(convs);
      } catch {}
    }, 5000);

    return () => {
      window.removeEventListener('popstate', handlePopState);
      clearInterval(interval);
    };
  }, [handlePopState]);

  const handleSelectTab = (tab: NavigationTab) => {
    setCurrentTab(tab);
    if (tab === 'chat') {
      navigateTo('chat', activeConversationId);
    } else {
      navigateTo(tab, selectedItemId);
    }
  };

  const handleSelectConversation = (id: string | null) => {
    setActiveConversationId(id);
    setCurrentTab('chat');
    navigateTo('chat', id);
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
      setActiveConversationId(newConv.id);
      setCurrentTab('chat');
      navigateTo('chat', newConv.id);
    } catch (err) {
      console.error('Failed to create conversation:', err);
    }
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
    <div className="flex h-full w-full overflow-hidden bg-zinc-50 text-zinc-900 font-['Plus_Jakarta_Sans',sans-serif]">
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
            onCreateConversation={handleCreateConversation}
            onRefreshConversations={loadInitialData}
          />
        )}

        {currentTab === 'agents' && (
          <AgentsView 
            agents={agents} 
            selectedId={selectedItemId}
            onSelectId={(id) => handleSelectItem('agents', id)}
            onRefresh={loadInitialData} 
          />
        )}

        {currentTab === 'skills' && (
          <SkillsView 
            selectedId={selectedItemId}
            onSelectId={(id) => handleSelectItem('skills', id)}
            onRefreshConversations={loadInitialData} 
          />
        )}

        {currentTab === 'tools' && (
          <ToolsView 
            selectedId={selectedItemId}
            onSelectId={(id) => handleSelectItem('tools', id)}
            onRefreshConversations={loadInitialData} 
          />
        )}

        {currentTab === 'tasks' && (
          <TasksMonitor />
        )}

        {currentTab === 'models' && (
          <ModelsView />
        )}

        {currentTab === 'settings' && (
          <SettingsView />
        )}
      </main>

      {/* Persistent Global vLLM Server Status & Download Progress Widget */}
      <GlobalVLLMStatusWidget />
    </div>
  );
};
