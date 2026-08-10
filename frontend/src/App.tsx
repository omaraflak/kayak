import React, { useState, useEffect } from 'react';
import { Conversation, AgentConfig, NavigationTab } from './types';
import { api } from './api/client';
import { Sidebar } from './components/Sidebar';
import { ChatView } from './components/ChatView';
import { AgentsView } from './components/AgentsView';
import { SkillsView } from './components/SkillsView';
import { ToolsView } from './components/ToolsView';
import { TasksMonitor } from './components/TasksMonitor';
import { SettingsView } from './components/SettingsView';

export const App: React.FC = () => {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [agents, setAgents] = useState<AgentConfig[]>([]);
  const [currentTab, setCurrentTab] = useState<NavigationTab>('chat');

  const loadInitialData = async () => {
    try {
      const [convs, ags] = await Promise.all([
        api.listConversations(),
        api.listAgents(),
      ]);
      setConversations(convs);
      setAgents(ags);

      if (convs.length > 0 && !activeConversationId) {
        setActiveConversationId(convs[0].id);
      }
    } catch (err) {
      console.error('Failed to load initial data:', err);
    }
  };

  useEffect(() => {
    loadInitialData();
    // Periodic refresh every 5 seconds to update conversation statuses across tabs
    const interval = setInterval(async () => {
      try {
        const convs = await api.listConversations();
        setConversations(convs);
      } catch {}
    }, 5000);
    return () => clearInterval(interval);
  }, []);

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
        setActiveConversationId(updated[0]?.id || null);
      }
    } catch (err) {
      console.error('Failed to delete conversation:', err);
    }
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-zinc-50 text-zinc-900 font-['Plus_Jakarta_Sans',sans-serif]">
      {/* Navigation Sidebar */}
      <Sidebar
        conversations={conversations}
        activeConversationId={activeConversationId}
        onSelectConversation={(id) => {
          setActiveConversationId(id);
          setCurrentTab('chat');
        }}
        onDeleteConversation={handleDeleteConversation}
        agents={agents}
        currentTab={currentTab}
        onSelectTab={setCurrentTab}
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
          <AgentsView agents={agents} onRefresh={loadInitialData} />
        )}

        {currentTab === 'skills' && (
          <SkillsView onRefreshConversations={loadInitialData} />
        )}

        {currentTab === 'tools' && (
          <ToolsView onRefreshConversations={loadInitialData} />
        )}

        {currentTab === 'tasks' && (
          <TasksMonitor />
        )}

        {currentTab === 'settings' && (
          <SettingsView />
        )}
      </main>
    </div>
  );
};
