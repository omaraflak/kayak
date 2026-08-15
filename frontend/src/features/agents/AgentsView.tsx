import React, { useEffect, useMemo, useState } from 'react';
import { AgentConfig, Skill, ToolDefinition } from '../../types';
import { api } from '../../api/client';
import { AgentEditor } from './AgentEditor';
import { getProviderIcon } from './agentDisplay';
import {
  AlertTriangle,
  Bot,
  Copy,
  MessageSquarePlus,
  Network,
  Pencil,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
  Wrench,
} from 'lucide-react';
import { useDialog } from '../../context/DialogContext';

/**
 * Agent profile library: read a profile, then edit it.
 *
 * Structured like the tools and skills views so all three management surfaces behave
 * identically. Landing directly in a live form (as this page previously did) makes it
 * easy to change a profile while only meaning to look at one.
 */

interface AgentsViewProps {
  agents: AgentConfig[];
  selectedId?: string | null;
  onSelectId?: (id: string | null) => void;
  onRefresh: () => void;
  onStartAgentChat?: (agentId: string) => void;
}

export const AgentsView: React.FC<AgentsViewProps> = ({
  agents,
  selectedId,
  onSelectId,
  onRefresh,
  onStartAgentChat,
}) => {
  const dialog = useDialog();
  const [selectedAgent, setSelectedAgent] = useState<AgentConfig | null>(agents[0] || null);
  const [isCreating, setIsCreating] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [duplicateOf, setDuplicateOf] = useState<AgentConfig | null>(null);
  const [tools, setTools] = useState<ToolDefinition[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [isReloading, setIsReloading] = useState(false);

  const loadMetadata = async () => {
    try {
      const [toolsData, skillsData] = await Promise.all([api.listTools(), api.listSkills()]);
      setTools(toolsData);
      setSkills(skillsData);
    } catch (error) {
      console.error('Failed to load tools/skills metadata:', error);
    }
  };

  useEffect(() => {
    loadMetadata();
  }, []);

  useEffect(() => {
    if (selectedId) {
      if (selectedId === 'new') {
        setIsCreating(true);
        setIsEditing(false);
        setSelectedAgent(null);
      } else {
        const found = agents.find((candidate) => candidate.id === selectedId);
        if (found) {
          setIsCreating(false);
          if (found.id !== selectedAgent?.id) setIsEditing(false);
          setSelectedAgent(found);
        }
      }
    } else if (agents.length > 0 && !selectedAgent && !isCreating) {
      setSelectedAgent(agents[0]);
      onSelectId?.(agents[0].id);
    }
  }, [selectedId, agents]);

  const handleReload = async () => {
    setIsReloading(true);
    try {
      await Promise.all([loadMetadata(), onRefresh()]);
    } finally {
      setIsReloading(false);
    }
  };

  const handleDelete = async (agent: AgentConfig) => {
    const confirmed = await dialog.confirm({
      title: 'Delete Agent Profile',
      message: `Delete the "${agent.id}" profile? Conversations already using it will fall back to the general agent.`,
      confirmText: 'Delete',
      variant: 'danger',
    });
    if (!confirmed) return;

    await api.deleteAgent(agent.id);
    // Pick the next selection from a freshly fetched list; the `agents` prop is a
    // snapshot and still contains the agent we just deleted.
    const remaining = await api.listAgents();
    const next = remaining[0] ?? null;
    setSelectedAgent(next);
    setIsEditing(false);
    onSelectId?.(next?.id ?? null);
    onRefresh();
  };

  const startEditing = (agent: AgentConfig | null, duplicate: AgentConfig | null = null) => {
    setDuplicateOf(duplicate);
    if (duplicate) {
      setIsCreating(true);
      setIsEditing(false);
      onSelectId?.('new');
    } else if (agent) {
      setIsCreating(false);
      setIsEditing(true);
    }
  };

  const toolsByName = useMemo(
    () => new Map(tools.map((tool) => [tool.name, tool])),
    [tools]
  );

  const capabilitySummary = useMemo(() => {
    if (!selectedAgent) return { auto: [], ask: [], missing: [] as string[] };
    const auto: ToolDefinition[] = [];
    const ask: ToolDefinition[] = [];
    const missing: string[] = [];
    for (const toolName of selectedAgent.allowed_tools ?? []) {
      const tool = toolsByName.get(toolName);
      if (!tool) {
        missing.push(toolName);
        continue;
      }
      if (selectedAgent.tool_permissions?.[toolName] === 'denied') continue;
      if (selectedAgent.tool_permissions?.[toolName] === 'ask_user') ask.push(tool);
      else auto.push(tool);
    }
    return { auto, ask, missing };
  }, [selectedAgent, toolsByName]);

  const renderToolChip = (tool: ToolDefinition, gated: boolean) => (
    <span
      key={tool.name}
      className={`text-[11px] font-mono px-2 py-1 rounded-lg border inline-flex items-center gap-1.5 ${
        gated
          ? 'bg-amber-100 dark:bg-amber-950/60 text-amber-900 dark:text-amber-100 border-amber-300 dark:border-amber-800/80'
          : 'bg-md-surface-container-high text-md-on-surface border-md-outline-variant'
      }`}
    >
      {tool.risk === 'high' && <AlertTriangle className="w-3 h-3 shrink-0" />}
      {tool.name}
    </span>
  );

  return (
    <div className="flex-1 flex h-full min-h-0 bg-md-surface overflow-hidden transition-colors">
      {/* Agents List Sidebar */}
      <div className="w-80 border-r border-md-outline-variant bg-md-surface-container-low flex flex-col shrink-0 transition-colors">
        <div className="p-4 border-b border-md-outline-variant flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Bot className="w-4 h-4 text-md-primary" />
            <h2 className="font-bold text-xs text-md-on-surface uppercase tracking-wider">
              Agents ({agents.length})
            </h2>
          </div>

          <div className="flex items-center space-x-1.5">
            <button
              onClick={handleReload}
              disabled={isReloading}
              className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high transition-colors cursor-pointer"
              title="Reload agents from disk"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isReloading ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={() => {
                setDuplicateOf(null);
                setIsCreating(true);
                setIsEditing(false);
                setSelectedAgent(null);
                onSelectId?.('new');
              }}
              className="p-1.5 rounded-lg bg-md-primary text-md-on-primary hover:opacity-90 text-xs flex items-center gap-1 font-semibold transition-opacity shadow-xs cursor-pointer"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>New</span>
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
          {agents.map((agent) => {
            const isSelected = selectedAgent?.id === agent.id;
            return (
              <div
                key={agent.id}
                onClick={() => {
                  setIsCreating(false);
                  setIsEditing(false);
                  setSelectedAgent(agent);
                  onSelectId?.(agent.id);
                }}
                className={`p-3.5 rounded-xl cursor-pointer transition-all border ${
                  isSelected
                    ? 'bg-md-primary-container text-md-on-primary-container border-md-primary shadow-xs ring-1 ring-md-primary/40 font-medium'
                    : 'bg-md-surface border-md-outline-variant text-md-on-surface hover:bg-md-surface-container hover:border-md-outline shadow-xs'
                }`}
              >
                <div className="flex items-center justify-between mb-1 gap-2">
                  <span className="font-semibold text-xs text-md-on-surface truncate">{agent.name}</span>
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded border bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant shrink-0">
                    {agent.id}
                  </span>
                </div>
                <p className="text-[11px] text-md-on-surface-variant line-clamp-2 mb-2 leading-relaxed">
                  {agent.description}
                </p>
                <div className="text-[10px] text-md-on-surface-variant font-mono truncate">
                  {getProviderIcon(agent.model)} {agent.model}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Main Workspace Pane: editor (create or edit) vs read-only profile */}
      {isCreating || isEditing ? (
        <div className="flex-1 flex flex-col h-full min-h-0 overflow-hidden bg-md-surface">
          <AgentEditor
            agent={isCreating ? null : selectedAgent}
            existingAgents={agents}
            duplicateOf={isCreating ? duplicateOf : null}
            onSaved={async (agentId) => {
              setIsCreating(false);
              setIsEditing(false);
              setDuplicateOf(null);
              const refreshed = await api.listAgents();
              setSelectedAgent(refreshed.find((a) => a.id === agentId) ?? null);
              onSelectId?.(agentId);
              onRefresh();
            }}
            onCancel={() => {
              setIsCreating(false);
              setIsEditing(false);
              setDuplicateOf(null);
              onSelectId?.(selectedAgent?.id ?? null);
            }}
          />
        </div>
      ) : (
      <div className="flex-1 flex flex-col h-full min-h-0 overflow-hidden bg-md-surface">
        <div className="h-16 border-b border-md-outline-variant px-8 flex items-center justify-between bg-md-surface-container-low shrink-0 transition-colors">
          <div className="min-w-0">
            <div className="flex items-center space-x-2.5">
              <h1 className="text-base font-bold text-md-on-surface truncate">
                {selectedAgent ? selectedAgent.name : 'Select Agent'}
              </h1>
              {selectedAgent && (
                <span className="text-xs px-2.5 py-0.5 rounded-full font-mono border bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant shrink-0">
                  {selectedAgent.id}
                </span>
              )}
            </div>
            <p className="text-xs text-md-on-surface-variant mt-0.5 truncate">
              {selectedAgent?.description || 'Select an agent to review how it is configured.'}
            </p>
          </div>

          {selectedAgent && (
            <div className="flex items-center gap-2 shrink-0">
              {onStartAgentChat && (
                <button
                  onClick={() => onStartAgentChat(selectedAgent.id)}
                  className="px-3.5 py-2 rounded-xl text-xs font-semibold text-md-on-surface bg-md-surface-container-high border border-md-outline-variant hover:opacity-90 transition-opacity flex items-center gap-1.5 cursor-pointer"
                >
                  <MessageSquarePlus className="w-3.5 h-3.5" />
                  <span>Test in chat</span>
                </button>
              )}
              <button
                onClick={() => startEditing(null, selectedAgent)}
                className="px-3.5 py-2 rounded-xl text-xs font-semibold text-md-on-surface bg-md-surface-container-high border border-md-outline-variant hover:opacity-90 transition-opacity flex items-center gap-1.5 cursor-pointer"
              >
                <Copy className="w-3.5 h-3.5" />
                <span>Duplicate</span>
              </button>
              <button
                onClick={() => startEditing(selectedAgent)}
                className="px-3.5 py-2 rounded-xl text-xs font-semibold text-md-on-primary bg-md-primary hover:opacity-90 transition-opacity flex items-center gap-1.5 shadow-xs cursor-pointer"
              >
                <Pencil className="w-3.5 h-3.5" />
                <span>Edit Agent</span>
              </button>
              <button
                onClick={() => handleDelete(selectedAgent)}
                className="px-3.5 py-2 rounded-xl text-xs font-semibold text-md-error bg-md-error-container hover:opacity-90 border border-md-outline-variant transition-opacity flex items-center gap-1.5 cursor-pointer"
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span>Delete Agent</span>
              </button>
            </div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto p-8 w-full bg-md-surface">
          {selectedAgent ? (
            <div className="max-w-5xl mx-auto space-y-6 pb-12">
              {/* Model */}
              <div className="p-4 bg-md-surface-container-lowest border border-md-outline-variant rounded-xl flex items-center gap-4">
                <div className="w-11 h-11 rounded-xl bg-md-surface-container-high border border-md-outline-variant flex items-center justify-center text-xl shrink-0">
                  {getProviderIcon(selectedAgent.model)}
                </div>
                <div className="min-w-0">
                  <div className="text-xs font-bold text-md-on-surface font-mono truncate">
                    {selectedAgent.model}
                  </div>
                  <div className="text-[11px] text-md-on-surface-variant mt-0.5">
                    Temperature {selectedAgent.temperature.toFixed(2)} ·{' '}
                    {selectedAgent.model.startsWith('vllm/') ? 'Local server' : 'Cloud provider'}
                  </div>
                </div>
              </div>

              {/* System prompt */}
              <div className="space-y-2">
                <h3 className="text-xs font-bold uppercase tracking-wider text-md-on-surface">
                  System Prompt
                </h3>
                <pre className="bg-md-surface-container-lowest p-4 rounded-xl border border-md-outline-variant text-[12px] text-md-on-surface font-mono whitespace-pre-wrap leading-relaxed overflow-x-auto">
                  {selectedAgent.system_prompt || '(no system prompt set)'}
                </pre>
              </div>

              {/* Capabilities */}
              <div className="space-y-3">
                <h3 className="text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
                  <Wrench className="w-4 h-4 text-md-primary" /> Tool Access
                </h3>

                {capabilitySummary.auto.length === 0 &&
                capabilitySummary.ask.length === 0 &&
                capabilitySummary.missing.length === 0 ? (
                  <p className="text-xs text-md-on-surface-variant">
                    This agent has no tools. It can only reply in text.
                  </p>
                ) : (
                  <div className="space-y-3">
                    {capabilitySummary.auto.length > 0 && (
                      <div className="space-y-1.5">
                        <p className="text-[11px] font-semibold text-md-on-surface-variant uppercase tracking-wider">
                          Runs automatically ({capabilitySummary.auto.length})
                        </p>
                        <div className="flex flex-wrap gap-1.5">
                          {capabilitySummary.auto.map((tool) => renderToolChip(tool, false))}
                        </div>
                      </div>
                    )}

                    {capabilitySummary.ask.length > 0 && (
                      <div className="space-y-1.5">
                        <p className="text-[11px] font-semibold text-md-on-surface-variant uppercase tracking-wider">
                          Asks first ({capabilitySummary.ask.length})
                        </p>
                        <div className="flex flex-wrap gap-1.5">
                          {capabilitySummary.ask.map((tool) => renderToolChip(tool, true))}
                        </div>
                      </div>
                    )}

                    {capabilitySummary.missing.length > 0 && (
                      <div className="space-y-1.5">
                        <p className="text-[11px] font-semibold text-md-error uppercase tracking-wider">
                          Not installed ({capabilitySummary.missing.length})
                        </p>
                        <p className="text-[11px] text-md-on-surface-variant">
                          These tools are granted but no longer exist:{' '}
                          <span className="font-mono">{capabilitySummary.missing.join(', ')}</span>
                        </p>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Skills */}
              <div className="space-y-2">
                <h3 className="text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
                  <Sparkles className="w-4 h-4 text-md-primary" /> Skills
                </h3>
                {selectedAgent.preloaded_skills?.length ? (
                  <div className="flex flex-wrap gap-1.5">
                    {selectedAgent.preloaded_skills.map((skillName) => (
                      <span
                        key={skillName}
                        className="text-[11px] font-mono px-2 py-1 rounded-lg border bg-md-primary-container text-md-on-primary-container border-md-outline-variant"
                      >
                        {skillName}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-md-on-surface-variant">
                    No skills preloaded.
                  </p>
                )}
                <p className="text-[11px] text-md-on-surface-variant leading-relaxed">
                  {selectedAgent.allowed_skills?.length
                    ? `On-demand skills restricted to: ${selectedAgent.allowed_skills.join(', ')}`
                    : `All ${skills.length} installed skills are discoverable on demand.`}
                </p>
              </div>

              {/* Sub-agents this profile may start. Delegation widens what an agent
                  can do, so the grant is stated as plainly as its tool access. */}
              <div className="space-y-2">
                <h3 className="text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
                  <Network className="w-4 h-4 text-md-primary" /> Sub-agents
                </h3>
                {(() => {
                  const allowed =
                    selectedAgent.allowed_subagents ?? [selectedAgent.id];
                  if (allowed.length === 0) {
                    return (
                      <p className="text-xs text-md-on-surface-variant">
                        This agent cannot start any sub-agents.
                      </p>
                    );
                  }
                  return (
                    <>
                      <div className="flex flex-wrap gap-1.5">
                        {allowed.map((agentId) => {
                          const profile = agents.find((a) => a.id === agentId);
                          return (
                            <span
                              key={agentId}
                              className={`text-[11px] font-mono px-2 py-1 rounded-lg border ${
                                profile
                                  ? 'bg-md-surface-container-high text-md-on-surface border-md-outline-variant'
                                  : 'bg-md-error-container text-md-error border-md-outline-variant'
                              }`}
                              title={profile ? profile.name : 'This profile no longer exists'}
                            >
                              {agentId}
                              {agentId === selectedAgent.id ? ' (itself)' : ''}
                            </span>
                          );
                        })}
                      </div>
                      {selectedAgent.allowed_subagents == null && (
                        <p className="text-[11px] text-md-on-surface-variant leading-relaxed">
                          Default policy: an agent may only start sub-agents with its
                          own profile unless granted others in the editor.
                        </p>
                      )}
                    </>
                  );
                })()}
              </div>
            </div>
          ) : (
            <div className="text-center py-20 text-md-on-surface-variant text-sm">
              Select an agent from the list to review its configuration.
            </div>
          )}
        </div>
      </div>
      )}
    </div>
  );
};
