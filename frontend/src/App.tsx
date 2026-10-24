import React, { useState, useEffect, useCallback } from 'react';
import { Conversation, AgentConfig, Modality, MODALITIES, NavigationTab } from './types';
import { api, errorMessage } from './api/client';
import { Sidebar } from './features/conversations/Sidebar';
import { ChatView } from './features/chat/ChatView';
import { AgentsView } from './features/agents/AgentsView';
import { MemoriesView } from './features/memories/MemoriesView';
import { SkillsView } from './features/skills/SkillsView';
import { ToolsView } from './features/tools/ToolsView';
import { SettingsView } from './features/settings/SettingsView';
import { ModelsView } from './features/models/ModelsView';
import { AudioView } from './features/audio/AudioView';
import { GlobalVLLMStatusWidget } from './features/models/GlobalVLLMStatusWidget';
import { useDialog } from './context/DialogContext';
import { useConversationActivity } from './context/ConversationActivityContext';
import { ErrorBoundary } from './ui/ErrorBoundary';
import { checkForNewShell } from './utils/appShellGuard';
import { parseCurrentUrl, navigateTo } from './utils/router';
import { Loader2 } from 'lucide-react';

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
  // An empty list and a list that could not be loaded must not look the same.
  const [loadFailed, setLoadFailed] = useState(false);
  const dialog = useDialog();
  const { isOnline, reconnections } = useConversationActivity();
  // Shown only once the server has been gone a moment: a reconnect after a
  // second's blip should not flash a warning.
  const [showOffline, setShowOffline] = useState(false);

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
      setLoadFailed(false);
      return convs;
    } catch (err) {
      console.error('Failed to load initial data:', err);
      setLoadFailed(true);
      return [];
    }
  }, []);

  useEffect(() => {
    if (isOnline) {
      setShowOffline(false);
      return;
    }
    const timer = setTimeout(() => setShowOffline(true), 2000);
    return () => clearTimeout(timer);
  }, [isOnline]);

  // Back after the server restarted -- an update, the Mac waking up. Whatever was
  // loaded before may be stale, and an update may have brought a newer app.
  useEffect(() => {
    if (reconnections === 0) return;
    loadInitialData();
    checkForNewShell();
  }, [reconnections, loadInitialData]);

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
    // Audio has no state without one of its two halves, so selecting it lands on
    // one rather than on a bare /audio that renders the same page under a
    // different address.
    if (tab === 'audio') {
      setSelectedItemId('synthesize');
      navigateTo('audio', 'synthesize');
      return;
    }

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
        title: "Couldn't start the conversation",
        message: errorMessage(err, "Couldn't start the conversation. Try again."),
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
      dialog.alert({
        title: "Couldn't delete the conversation",
        message: errorMessage(err, "Couldn't delete this conversation. Try again."),
        variant: 'danger',
      });
    }
  };

  const handleSelectItem = (tab: NavigationTab, id: string | null) => {
    setSelectedItemId(id);
    navigateTo(tab, id);
  };

  /** Opens Local Models with one runtime's catalogue already selected. */
  const handleOpenModels = async (modality: Modality) => {
    if (!(await confirmLeavingSettings())) return;
    setCurrentTab('models');
    handleSelectItem('models', modality);
  };

  /** Opens an agent's profile from another tab, e.g. from a sub-agent transcript. */
  const handleOpenAgentProfile = (agentId: string) => {
    setCurrentTab('agents');
    setSelectedItemId(agentId);
    navigateTo('agents', agentId);
  };

  return (
    <div className="flex h-full w-full overflow-hidden bg-md-surface text-md-on-surface">
      {/* Navigation Sidebar */}
      <Sidebar
        conversations={conversations}
        activeConversationId={activeConversationId}
        onSelectConversation={handleSelectConversation}
        onDeleteConversation={handleDeleteConversation}
        currentTab={currentTab}
        onSelectTab={handleSelectTab}
        agents={agents}
        loadFailed={loadFailed}
        onRetryLoad={loadInitialData}
      />

      {/* Main Content Pane */}
      <main className="flex-1 flex flex-col overflow-hidden relative">
        {showOffline && (
          <div className="shrink-0 flex items-center justify-center gap-2 px-4 py-2 text-xs font-medium bg-amber-100 dark:bg-amber-950/80 text-amber-900 dark:text-amber-100 border-b border-amber-300 dark:border-amber-800/80">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            Reconnecting to Kayak… This happens after an update or when your Mac wakes
            up, and clears by itself.
          </div>
        )}
        {/* A page that crashes takes only itself down; leaving it for another
            page, or another conversation, starts it afresh. */}
        <ErrorBoundary resetKey={`${currentTab}:${activeConversationId}:${selectedItemId}`}>
          <div className="flex-1 flex overflow-hidden min-h-0">
            {currentTab === 'chat' && (
              <ChatView 
                conversationId={activeConversationId} 
                agents={agents}
                initialDraftAgentId={draftAgentId}
                onCreateConversation={handleCreateConversation}
                onRefreshConversations={loadInitialData}
                onOpenConversation={handleOpenConversation}
                onSelectConversation={handleSelectConversation}
                onOpenAgent={handleOpenAgentProfile}
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

            {currentTab === 'memories' && <MemoriesView />}

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

            {currentTab === 'models' && (
              <ModelsView
                // Anything the URL does not name as a runtime is the text catalogue,
                // so a bare /models or a stale link still lands somewhere real.
                modality={MODALITIES.includes(selectedItemId as Modality)
                  ? (selectedItemId as Modality)
                  : 'text'}
                onSelectModality={(next) => handleSelectItem('models', next)}
              />
            )}

            {currentTab === 'audio' && (
              <AudioView
                // Audio knows which runtime it is missing, so it opens that catalogue
                // rather than dropping the user on text to find the tab themselves.
                onOpenModels={(needed) => handleOpenModels(needed)}
                // Anything but 'transcribe' is the synthesize half, so a bare /audio
                // or a stale link lands somewhere real rather than on nothing.
                mode={selectedItemId === 'transcribe' ? 'transcribe' : 'synthesize'}
                onSelectMode={(mode) => handleSelectItem('audio', mode)}
              />
            )}

            {currentTab === 'settings' && (
              <SettingsView onDirtyChange={setHasUnsavedSettings} />
            )}
          </div>
        </ErrorBoundary>
      </main>

      {/* Narrates a text model deployment from any page. */}
      <GlobalVLLMStatusWidget onOpenModels={() => handleOpenModels('text')} />
    </div>
  );
};
