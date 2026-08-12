import React, { useState, useEffect, useMemo } from 'react';
import { AgentConfig, Skill, ToolDefinition } from '../types';
import { api } from '../api/client';
import { Bot, Plus, Trash2, Save, Sparkles, Wrench, Check, Sliders, RotateCcw, Cpu } from 'lucide-react';
import { useDialog } from '../context/DialogContext';
import { ModelSelectorModal } from './ModelSelectorModal';

interface AgentsViewProps {
  agents: AgentConfig[];
  selectedId?: string | null;
  onSelectId?: (id: string | null) => void;
  onRefresh: () => void;
}

export const AgentsView: React.FC<AgentsViewProps> = ({ 
  agents, 
  selectedId, 
  onSelectId, 
  onRefresh 
}) => {
  const dialog = useDialog();
  const [selectedAgent, setSelectedAgent] = useState<AgentConfig | null>(agents[0] || null);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [tools, setTools] = useState<ToolDefinition[]>([]);
  const [isSaving, setIsSaving] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [isModelModalOpen, setIsModelModalOpen] = useState(false);

  // Form State
  const [id, setId] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [model, setModel] = useState('gemini/gemini-3.6-flash');
  const [temperature, setTemperature] = useState(0.7);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [allowedTools, setAllowedTools] = useState<string[]>([]);
  const [allowedSkills, setAllowedSkills] = useState<string[]>([]);
  const [preloadedSkills, setPreloadedSkills] = useState<string[]>([]);

  useEffect(() => {
    loadPrerequisites();
  }, []);

  useEffect(() => {
    if (selectedId) {
      if (selectedId === 'new') {
        setIsCreating(true);
        setSelectedAgent(null);
        setId('new_agent');
        setName('New Custom Agent');
        setDescription('Description for this agent profile.');
        setModel('gemini/gemini-3.6-flash');
        setTemperature(0.7);
        setSystemPrompt('');
        setAllowedTools([]);
        setAllowedSkills([]);
        setPreloadedSkills([]);
      } else {
        const found = agents.find((a) => a.id === selectedId);
        if (found) {
          setIsCreating(false);
          setSelectedAgent(found);
        }
      }
    } else if (agents.length > 0 && !selectedAgent && !isCreating) {
      setSelectedAgent(agents[0]);
      onSelectId?.(agents[0].id);
    }
  }, [selectedId, agents]);

  useEffect(() => {
    if (selectedAgent && !isCreating) {
      setId(selectedAgent.id);
      setName(selectedAgent.name);
      setDescription(selectedAgent.description);
      setModel(selectedAgent.model);
      setTemperature(selectedAgent.temperature);
      setSystemPrompt(selectedAgent.system_prompt);
      setAllowedTools(selectedAgent.allowed_tools || []);
      setAllowedSkills(selectedAgent.allowed_skills || []);
      setPreloadedSkills(selectedAgent.preloaded_skills || []);
    }
  }, [selectedAgent, isCreating]);

  const loadPrerequisites = async () => {
    try {
      const [skillsData, toolsData] = await Promise.all([
        api.listSkills(),
        api.listTools(),
      ]);
      setSkills(skillsData);
      setTools(toolsData);
    } catch (error) {
      console.error('Failed to load skills/tools metadata:', error);
    }
  };

  const handleStartCreate = () => {
    setIsCreating(true);
    setSelectedAgent(null);
    setId('new_agent');
    setName('New Custom Agent');
    setDescription('Description for this agent profile.');
    setModel('gemini/gemini-3.6-flash');
    setTemperature(0.7);
    setSystemPrompt('You are a helpful AI assistant.');
    setAllowedTools(tools.map((tool) => tool.name));
    setAllowedSkills([]);
    setPreloadedSkills([]);
  };

  const isDirty = useMemo(() => {
    if (isCreating) return true;
    if (!selectedAgent) return false;
    const sortedAllowedTools = [...allowedTools].sort();
    const origAllowedTools = [...(selectedAgent.allowed_tools || [])].sort();
    const sortedPreloadedSkills = [...preloadedSkills].sort();
    const origPreloadedSkills = [...(selectedAgent.preloaded_skills || [])].sort();

    return (
      id !== selectedAgent.id ||
      name !== selectedAgent.name ||
      description !== selectedAgent.description ||
      model !== selectedAgent.model ||
      temperature !== selectedAgent.temperature ||
      systemPrompt !== selectedAgent.system_prompt ||
      JSON.stringify(sortedAllowedTools) !== JSON.stringify(origAllowedTools) ||
      JSON.stringify(sortedPreloadedSkills) !== JSON.stringify(origPreloadedSkills)
    );
  }, [
    isCreating,
    selectedAgent,
    id,
    name,
    description,
    model,
    temperature,
    systemPrompt,
    allowedTools,
    preloadedSkills,
  ]);

  const handleReset = () => {
    if (selectedAgent) {
      setId(selectedAgent.id);
      setName(selectedAgent.name);
      setDescription(selectedAgent.description);
      setModel(selectedAgent.model);
      setTemperature(selectedAgent.temperature);
      setSystemPrompt(selectedAgent.system_prompt);
      setAllowedTools(selectedAgent.allowed_tools || []);
      setAllowedSkills(selectedAgent.allowed_skills || []);
      setPreloadedSkills(selectedAgent.preloaded_skills || []);
    }
  };

  const handleSave = async (event?: React.FormEvent) => {
    if (event) event.preventDefault();
    if (!isDirty || isSaving) return;
    setIsSaving(true);
    try {
      const agentData: AgentConfig = {
        id,
        name,
        description,
        model,
        temperature,
        system_prompt: systemPrompt,
        allowed_tools: allowedTools,
        allowed_skills: allowedSkills,
        preloaded_skills: preloadedSkills,
        tool_permissions: {},
      };
      await api.saveAgent(agentData);
      setIsCreating(false);
      onRefresh();
      setSelectedAgent(agentData);
    } catch (error) {
      console.error('Failed to save agent:', error);
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async (agentId: string) => {
    const confirmed = await dialog.confirm({
      title: 'Delete Agent Profile',
      message: `Are you sure you want to delete the "${agentId}" agent profile configuration? This cannot be undone.`,
      confirmText: 'Delete',
      variant: 'danger',
    });
    if (confirmed) {
      await api.deleteAgent(agentId);
      onRefresh();
      if (selectedAgent?.id === agentId) {
        setSelectedAgent(agents[0] || null);
      }
    }
  };

  const toggleTool = (toolName: string) => {
    setAllowedTools((prev) =>
      prev.includes(toolName)
        ? prev.filter((existingTool) => existingTool !== toolName)
        : [...prev, toolName]
    );
  };

  const togglePreloadedSkill = (skillName: string) => {
    setPreloadedSkills((prev) =>
      prev.includes(skillName)
        ? prev.filter((existingSkill) => existingSkill !== skillName)
        : [...prev, skillName]
    );
  };

  const getProviderIcon = (modelStr: string) => {
    if (modelStr.startsWith('gemini/')) return '✨';
    if (modelStr.startsWith('openai/')) return '🧠';
    if (modelStr.startsWith('anthropic/')) return '⚡';
    if (modelStr.startsWith('ollama/')) return '🦙';
    if (modelStr.startsWith('vllm/')) return '🚀';
    if (modelStr.startsWith('huggingface/') || modelStr.startsWith('hf/')) return '🤗';
    return '🤖';
  };

  return (
    <div className="flex-1 flex h-full min-h-0 bg-md-surface overflow-hidden transition-colors">
      {/* Model Selection Modal */}
      <ModelSelectorModal
        isOpen={isModelModalOpen}
        onClose={() => setIsModelModalOpen(false)}
        selectedModel={model}
        onSelectModel={(newModel) => setModel(newModel)}
      />

      {/* Agents List Sidebar */}
      <div className="w-80 border-r border-md-outline-variant bg-md-surface-container-low flex flex-col shrink-0 transition-colors">
        <div className="p-4 border-b border-md-outline-variant flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Bot className="w-4 h-4 text-md-primary" />
            <h2 className="font-bold text-xs text-md-on-surface uppercase tracking-wider">Agent Profiles</h2>
          </div>
          <button
            onClick={() => {
              handleStartCreate();
              onSelectId?.('new');
            }}
            className="p-1.5 rounded-lg bg-md-primary text-md-on-primary hover:opacity-90 text-xs flex items-center gap-1 font-semibold transition-opacity shadow-xs cursor-pointer"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>New</span>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
          {agents.map((agent) => {
            const isSelected = !isCreating && selectedAgent?.id === agent.id;
            return (
              <div
                key={agent.id}
                onClick={() => {
                  setIsCreating(false);
                  setSelectedAgent(agent);
                  onSelectId?.(agent.id);
                }}
                className={`p-3.5 rounded-xl cursor-pointer transition-all border ${
                  isSelected
                    ? 'bg-md-primary-container text-md-on-primary-container border-md-primary shadow-xs ring-1 ring-md-primary/40 font-medium'
                    : 'bg-md-surface border-md-outline-variant text-md-on-surface hover:bg-md-surface-container hover:border-md-outline shadow-xs'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-semibold text-xs text-md-on-surface">{agent.name}</span>
                  <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${
                    isSelected
                      ? 'bg-md-surface-container-high text-md-on-surface border-md-outline-variant'
                      : 'bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant'
                  }`}>
                    {agent.id}
                  </span>
                </div>
                <p className="text-[11px] text-md-on-surface-variant line-clamp-2 mb-2 leading-relaxed">
                  {agent.description}
                </p>
                <div className="text-[10px] text-md-on-surface-variant font-mono flex items-center gap-1">
                  <span>{getProviderIcon(agent.model)} {agent.model}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Right Column: Rigid Docked Top Header + Scrollable Form */}
      <div className="flex-1 flex flex-col h-full min-h-0 overflow-hidden bg-md-surface">
        {/* Top Header rigidly docked with 0 space above */}
        <div className="h-16 border-b border-md-outline-variant px-8 flex items-center justify-between bg-md-surface-container-low shrink-0 transition-colors">
          <div>
            <h1 className="text-base font-bold text-md-on-surface flex items-center gap-2">
              <Bot className="w-5 h-5 text-md-primary" />
              {isCreating ? 'Create Agent Profile' : `Agent: ${name}`}
              {isDirty && !isCreating && (
                <span className="text-[11px] font-medium text-amber-800 dark:text-amber-200 bg-amber-100 dark:bg-amber-950/80 border border-amber-300 dark:border-amber-800/80 px-2 py-0.5 rounded-full flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse"></span>
                  Unsaved changes
                </span>
              )}
            </h1>
            <p className="text-xs text-md-on-surface-variant mt-0.5">
              Configure model strings, temperature sampling, system instructions, tool access, and preloaded skills.
            </p>
          </div>

          <div className="flex items-center space-x-3">
            {isDirty && !isCreating && (
              <button
                type="button"
                onClick={handleReset}
                className="px-3.5 py-2 rounded-xl text-xs font-medium text-md-on-surface hover:bg-md-surface-container-high flex items-center gap-1.5 transition-colors border border-md-outline-variant cursor-pointer"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>Discard</span>
              </button>
            )}

            {!isCreating && selectedAgent && (
              <button
                type="button"
                onClick={() => handleDelete(selectedAgent.id)}
                className="px-3 py-2 rounded-xl text-xs font-medium text-md-error bg-md-error-container hover:opacity-90 border border-md-outline-variant transition-opacity flex items-center gap-1 cursor-pointer"
                title="Delete Profile"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            )}

            <button
              onClick={() => handleSave()}
              disabled={!isDirty || isSaving}
              className="px-5 py-2.5 rounded-xl text-xs font-bold text-md-on-primary bg-md-primary hover:opacity-90 disabled:opacity-40 disabled:hover:opacity-40 disabled:cursor-not-allowed flex items-center gap-2 shadow-sm transition-opacity focus:ring-2 focus:ring-md-primary cursor-pointer"
            >
              <Save className="w-4 h-4" />
              <span>{isSaving ? 'Saving...' : 'Save Profile'}</span>
            </button>
          </div>
        </div>

        {/* Scrollable Form Content */}
        <div className="flex-1 overflow-y-auto p-8 bg-md-surface">
          <form onSubmit={handleSave} className="space-y-6 max-w-5xl mx-auto pb-12">
            {/* Core Info */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-md-on-surface mb-1.5">
                  Agent Identifier (ID)
                </label>
                <input
                  type="text"
                  value={id}
                  disabled={!isCreating}
                  onChange={(event) => setId(event.target.value)}
                  placeholder="e.g. autonomous_coder"
                  className="w-full bg-md-surface-container-lowest border border-md-outline-variant rounded-xl px-3.5 py-2.5 text-xs text-md-on-surface font-mono focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary disabled:bg-md-surface-container-high disabled:text-md-on-surface-variant transition-colors"
                  required
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-md-on-surface mb-1.5">
                  Display Name
                </label>
                <input
                  type="text"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="e.g. Autonomous Coder"
                  className="w-full bg-md-surface-container-lowest border border-md-outline-variant rounded-xl px-3.5 py-2.5 text-xs text-md-on-surface focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary transition-colors"
                  required
                />
              </div>
            </div>

            <div>
              <label className="block text-xs font-semibold text-md-on-surface mb-1.5">
                Description
              </label>
              <input
                type="text"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Short description of this agent's purpose and expertise."
                className="w-full bg-md-surface-container-lowest border border-md-outline-variant rounded-xl px-3.5 py-2.5 text-xs text-md-on-surface focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary transition-colors"
              />
            </div>

            {/* Visual Interactive Model Selector Card & Sampling Temperature */}
            <div className="p-5 bg-md-surface border border-md-outline-variant rounded-2xl shadow-xs space-y-4 transition-colors">
              <h3 className="text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
                <Sliders className="w-3.5 h-3.5 text-md-primary" /> LLM Model & Sampling Configuration
              </h3>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-start">
                {/* Model Selector Card */}
                <div>
                  <label className="block text-xs font-semibold text-md-on-surface mb-1.5">
                    Selected LLM Model
                  </label>
                  <div className="p-4 bg-md-surface-container-lowest border border-md-outline-variant rounded-xl flex items-center justify-between shadow-xs transition-colors">
                    <div className="flex items-center space-x-3 overflow-hidden mr-2">
                      <div className="w-10 h-10 rounded-xl bg-md-surface-container-high border border-md-outline-variant flex items-center justify-center text-xl shrink-0 shadow-2xs">
                        {getProviderIcon(model)}
                      </div>
                      <div className="truncate min-w-0">
                        <div className="text-xs font-bold text-md-on-surface font-mono truncate">
                          {model}
                        </div>
                        <div className="text-[11px] text-md-on-surface-variant mt-0.5 flex items-center gap-1.5">
                          {model.startsWith('vllm/') || model.startsWith('ollama/') ? (
                            <span className="text-[10px] px-1.5 py-0.2 rounded-full bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-200 border border-emerald-300 dark:border-emerald-800/80 font-semibold">
                              Local Server
                            </span>
                          ) : model.startsWith('huggingface/') || model.startsWith('hf/') ? (
                            <span className="text-[10px] px-1.5 py-0.2 rounded-full bg-amber-100 dark:bg-amber-950/80 text-amber-800 dark:text-amber-200 border border-amber-300 dark:border-amber-800/80 font-semibold">
                              Hugging Face
                            </span>
                          ) : (
                            <span className="text-[10px] px-1.5 py-0.2 rounded-full bg-md-surface-container-high text-md-on-surface-variant border border-md-outline-variant">
                              Cloud Provider
                            </span>
                          )}
                          <span>· LiteLLM Engine</span>
                        </div>
                      </div>
                    </div>

                    <button
                      type="button"
                      onClick={() => setIsModelModalOpen(true)}
                      className="px-3.5 py-2 rounded-xl bg-md-primary hover:opacity-90 text-md-on-primary text-xs font-bold transition-opacity shadow-xs flex items-center gap-1.5 shrink-0 cursor-pointer"
                    >
                      <Cpu className="w-3.5 h-3.5" />
                      <span>Change</span>
                    </button>
                  </div>
                </div>

                {/* Sampling Temperature */}
                <div>
                  <div className="flex justify-between items-center mb-1.5">
                    <label className="text-xs font-semibold text-md-on-surface">
                      Temperature Sampling
                    </label>
                    <span className="font-mono text-xs text-md-primary font-bold">{temperature}</span>
                  </div>
                  <input
                    type="range"
                    min="0.0"
                    max="1.0"
                    step="0.05"
                    value={temperature}
                    onChange={(event) => setTemperature(parseFloat(event.target.value))}
                    className="w-full h-2 bg-md-surface-container-high rounded-lg appearance-none cursor-pointer accent-md-primary mt-2"
                  />
                  <div className="flex justify-between text-[10px] text-md-on-surface-variant mt-1.5">
                    <span>0.0 (Deterministic / Analytical)</span>
                    <span>1.0 (Creative / Exploratory)</span>
                  </div>
                </div>
              </div>
            </div>

            {/* System Prompt */}
            <div>
              <label className="block text-xs font-semibold text-md-on-surface mb-1.5">
                System Prompt Instructions
              </label>
              <textarea
                rows={6}
                value={systemPrompt}
                onChange={(event) => setSystemPrompt(event.target.value)}
                placeholder="Define agent personality, behavior, constraints, and instructions..."
                className="w-full bg-md-surface-container-lowest border border-md-outline-variant rounded-xl p-3.5 text-xs text-md-on-surface font-mono placeholder:text-md-on-surface-variant/70 focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary leading-relaxed transition-colors"
              />
            </div>

            {/* Allowed Tools - Rendered as a clean vertical list */}
            <div className="space-y-2">
              <label className="block text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
                <Wrench className="w-3.5 h-3.5 text-md-primary" /> Permitted Tools ({allowedTools.length} enabled)
              </label>
              <div className="space-y-2">
                {tools.map((tool) => {
                  const isChecked = allowedTools.includes(tool.name);
                  return (
                    <div
                      key={tool.name}
                      onClick={() => toggleTool(tool.name)}
                      className={`p-3.5 rounded-xl border cursor-pointer flex items-center justify-between transition-all ${
                        isChecked
                          ? 'bg-md-primary-container border-md-primary text-md-on-primary-container shadow-xs ring-1 ring-md-primary/40'
                          : 'bg-md-surface border-md-outline-variant text-md-on-surface hover:border-md-outline hover:bg-md-surface-container shadow-xs'
                      }`}
                    >
                      <div className="pr-4">
                        <div className="flex items-center space-x-2">
                          <span className="font-semibold text-xs font-mono text-md-on-surface">{tool.name}</span>
                          {tool.is_builtin ? (
                            <span className="text-[9px] font-semibold uppercase px-1.5 py-0.2 rounded bg-md-surface-container-high text-md-on-surface-variant border border-md-outline-variant">
                              Builtin
                            </span>
                          ) : (
                            <span className="text-[9px] font-semibold uppercase px-1.5 py-0.2 rounded bg-md-tertiary-container text-md-on-tertiary-container border border-md-outline-variant font-medium">
                              Custom
                            </span>
                          )}
                        </div>
                        <div className="text-[11px] text-md-on-surface-variant mt-0.5 leading-normal">{tool.description}</div>
                      </div>
                      <div className={`w-5 h-5 rounded-md flex items-center justify-center border shrink-0 transition-colors ${
                        isChecked ? 'bg-md-primary border-md-primary text-md-on-primary' : 'border-md-outline-variant bg-md-surface-container-lowest'
                      }`}>
                        {isChecked && <Check className="w-3.5 h-3.5 stroke-[3]" />}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Preloaded Skills - Rendered as a clean vertical list */}
            <div className="space-y-2">
              <label className="block text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
                <Sparkles className="w-3.5 h-3.5 text-md-primary" /> Preloaded Skills ({preloadedSkills.length} active)
              </label>
              <p className="text-[11px] text-md-on-surface-variant">
                Preloaded skills inject full instructions into context at startup. Other skills are loaded on demand via load_skill.
              </p>
              <div className="space-y-2">
                {skills.map((skill) => {
                  const isChecked = preloadedSkills.includes(skill.name);
                  return (
                    <div
                      key={skill.name}
                      onClick={() => togglePreloadedSkill(skill.name)}
                      className={`p-3.5 rounded-xl border cursor-pointer flex items-center justify-between transition-all ${
                        isChecked
                          ? 'bg-md-primary-container border-md-primary text-md-on-primary-container shadow-xs ring-1 ring-md-primary/40'
                          : 'bg-md-surface border-md-outline-variant text-md-on-surface hover:border-md-outline hover:bg-md-surface-container shadow-xs'
                      }`}
                    >
                      <div className="pr-4">
                        <div className="font-semibold text-xs text-md-on-surface">{skill.name}</div>
                        <div className="text-[11px] text-md-on-surface-variant mt-0.5 leading-normal">{skill.description}</div>
                      </div>
                      <div className={`w-5 h-5 rounded-md flex items-center justify-center border shrink-0 transition-colors ${
                        isChecked ? 'bg-md-primary border-md-primary text-md-on-primary' : 'border-md-outline-variant bg-md-surface-container-lowest'
                      }`}>
                        {isChecked && <Check className="w-3.5 h-3.5 stroke-[3]" />}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
};
