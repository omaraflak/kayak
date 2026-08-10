import React, { useState, useEffect, useMemo } from 'react';
import { AgentConfig, Skill, ToolDefinition } from '../types';
import { api } from '../api/client';
import { Bot, Plus, Trash2, Save, Sparkles, Wrench, Check, Sliders, RotateCcw } from 'lucide-react';
import { useDialog } from '../context/DialogContext';

interface AgentsViewProps {
  agents: AgentConfig[];
  onRefresh: () => void;
}

const COMMON_MODELS = [
  'gemini/gemini-3.6-flash',
  'openai/gpt-4o',
  'openai/gpt-4o-mini',
  'anthropic/claude-3-5-sonnet-20241022',
  'ollama/llama3',
  'ollama/mistral',
  'vllm/meta-llama/Meta-Llama-3-8B-Instruct',
];

export const AgentsView: React.FC<AgentsViewProps> = ({ agents, onRefresh }) => {
  const dialog = useDialog();
  const [selectedAgent, setSelectedAgent] = useState<AgentConfig | null>(agents[0] || null);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [tools, setTools] = useState<ToolDefinition[]>([]);
  const [isSaving, setIsSaving] = useState(false);
  const [isCreating, setIsCreating] = useState(false);

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

  return (
    <div className="flex-1 flex h-screen bg-zinc-50 overflow-hidden">
      {/* Agents List Sidebar */}
      <div className="w-80 border-r border-zinc-200 bg-white flex flex-col shrink-0">
        <div className="p-4 border-b border-zinc-200 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Bot className="w-4 h-4 text-zinc-700" />
            <h2 className="font-bold text-xs text-zinc-900 uppercase tracking-wider">Agent Profiles</h2>
          </div>
          <button
            onClick={handleStartCreate}
            className="p-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs flex items-center gap-1 font-semibold transition-colors shadow-xs"
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
                }}
                className={`p-3.5 rounded-xl cursor-pointer transition-all border ${
                  isSelected
                    ? 'bg-indigo-50/80 border border-indigo-600 text-zinc-950 font-medium shadow-xs ring-1 ring-indigo-500/20'
                    : 'bg-white border-zinc-200 text-zinc-700 hover:bg-zinc-50 hover:border-zinc-300 shadow-xs'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-semibold text-xs text-zinc-900">{agent.name}</span>
                  <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${
                    isSelected
                      ? 'bg-indigo-100 text-indigo-800 border-indigo-200'
                      : 'bg-zinc-100 text-zinc-600 border-zinc-200'
                  }`}>
                    {agent.id}
                  </span>
                </div>
                <p className="text-[11px] text-zinc-500 line-clamp-2 mb-2 leading-relaxed">
                  {agent.description}
                </p>
                <div className="text-[10px] text-zinc-500 font-mono flex items-center gap-1">
                  <span>{agent.model}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Right Column: Rigid Docked Top Header + Scrollable Form */}
      <div className="flex-1 flex flex-col h-screen overflow-hidden">
        {/* Top Header rigidly docked with 0 space above */}
        <div className="h-16 border-b border-zinc-200 px-8 flex items-center justify-between bg-white shrink-0">
          <div>
            <h1 className="text-base font-bold text-zinc-900 flex items-center gap-2">
              <Bot className="w-5 h-5 text-indigo-600" />
              {isCreating ? 'Create Agent Profile' : `Agent: ${name}`}
              {isDirty && !isCreating && (
                <span className="text-[11px] font-medium text-amber-600 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded-full flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse"></span>
                  Unsaved changes
                </span>
              )}
            </h1>
            <p className="text-xs text-zinc-500 mt-0.5">
              Configure model strings, temperature sampling, system instructions, tool access, and preloaded skills.
            </p>
          </div>

          <div className="flex items-center space-x-3">
            {isDirty && !isCreating && (
              <button
                type="button"
                onClick={handleReset}
                className="px-3.5 py-2 rounded-xl text-xs font-medium text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100 flex items-center gap-1.5 transition-colors border border-zinc-200"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>Discard</span>
              </button>
            )}

            {!isCreating && selectedAgent && (
              <button
                type="button"
                onClick={() => handleDelete(selectedAgent.id)}
                className="px-3 py-2 rounded-xl text-xs font-medium text-rose-600 hover:bg-rose-50 border border-rose-200 transition-colors flex items-center gap-1"
                title="Delete Profile"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            )}

            <button
              onClick={() => handleSave()}
              disabled={!isDirty || isSaving}
              className="px-5 py-2.5 rounded-xl text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 disabled:hover:bg-indigo-600 disabled:cursor-not-allowed flex items-center gap-2 shadow-sm transition-all focus:ring-2 focus:ring-indigo-500 focus:ring-offset-1"
            >
              <Save className="w-4 h-4" />
              <span>{isSaving ? 'Saving...' : 'Save Profile'}</span>
            </button>
          </div>
        </div>

        {/* Scrollable Form Content */}
        <div className="flex-1 overflow-y-auto p-8">
          <form onSubmit={handleSave} className="space-y-6 max-w-5xl mx-auto pb-12">
            {/* Core Info */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-zinc-700 mb-1.5">
                  Agent Identifier (ID)
                </label>
                <input
                  type="text"
                  value={id}
                  disabled={!isCreating}
                  onChange={(event) => setId(event.target.value)}
                  placeholder="e.g. autonomous_coder"
                  className="w-full bg-white border border-zinc-300 rounded-xl px-3.5 py-2.5 text-xs text-zinc-900 font-mono focus:outline-none focus:border-indigo-600 focus:ring-1 focus:ring-indigo-600 disabled:bg-zinc-100 disabled:text-zinc-500"
                  required
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-zinc-700 mb-1.5">
                  Display Name
                </label>
                <input
                  type="text"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="e.g. Autonomous Coder"
                  className="w-full bg-white border border-zinc-300 rounded-xl px-3.5 py-2.5 text-xs text-zinc-900 focus:outline-none focus:border-indigo-600 focus:ring-1 focus:ring-indigo-600"
                  required
                />
              </div>
            </div>

            <div>
              <label className="block text-xs font-semibold text-zinc-700 mb-1.5">
                Description
              </label>
              <input
                type="text"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Short description of this agent's purpose and expertise."
                className="w-full bg-white border border-zinc-300 rounded-xl px-3.5 py-2.5 text-xs text-zinc-900 focus:outline-none focus:border-indigo-600 focus:ring-1 focus:ring-indigo-600"
              />
            </div>

            {/* Swappable Model Selection & Temperature */}
            <div className="p-5 bg-white border border-zinc-200 rounded-2xl shadow-xs space-y-4">
              <h3 className="text-xs font-bold uppercase tracking-wider text-zinc-700 flex items-center gap-1.5">
                <Sliders className="w-3.5 h-3.5 text-indigo-600" /> LLM Model & Sampling Configuration
              </h3>

              <div className="grid grid-cols-2 gap-6">
                <div>
                  <label className="block text-xs font-semibold text-zinc-700 mb-1.5">
                    Model String (LiteLLM Format)
                  </label>
                  <div className="space-y-2">
                    <input
                      type="text"
                      value={model}
                      onChange={(event) => setModel(event.target.value)}
                      placeholder="e.g. gemini/gemini-3.6-flash, openai/gpt-4o, ollama/llama3"
                      className="w-full bg-zinc-50 border border-zinc-300 rounded-xl px-3.5 py-2.5 text-xs text-zinc-900 font-mono focus:bg-white focus:outline-none focus:border-indigo-600 focus:ring-1 focus:ring-indigo-600"
                      required
                    />
                    <div className="flex flex-wrap gap-1.5">
                      {COMMON_MODELS.map((modelName) => (
                        <button
                          key={modelName}
                          type="button"
                          onClick={() => setModel(modelName)}
                          className={`text-[10px] px-2 py-0.5 rounded font-mono border transition-colors ${
                            model === modelName
                              ? 'bg-indigo-600 text-white border-indigo-600 font-semibold'
                              : 'bg-zinc-100 text-zinc-700 border-zinc-200 hover:bg-zinc-200'
                          }`}
                        >
                          {modelName.split('/')[1] || modelName}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>

                <div>
                  <div className="flex justify-between items-center mb-1.5">
                    <label className="text-xs font-semibold text-zinc-700">
                      Temperature Sampling
                    </label>
                    <span className="font-mono text-xs text-indigo-600 font-bold">{temperature}</span>
                  </div>
                  <input
                    type="range"
                    min="0.0"
                    max="1.0"
                    step="0.05"
                    value={temperature}
                    onChange={(event) => setTemperature(parseFloat(event.target.value))}
                    className="w-full h-2 bg-zinc-200 rounded-lg appearance-none cursor-pointer accent-indigo-600 mt-2"
                  />
                  <div className="flex justify-between text-[10px] text-zinc-500 mt-1.5">
                    <span>0.0 (Deterministic / Analytical)</span>
                    <span>1.0 (Creative / Exploratory)</span>
                  </div>
                </div>
              </div>
            </div>

            {/* System Prompt */}
            <div>
              <label className="block text-xs font-semibold text-zinc-700 mb-1.5">
                System Prompt Instructions
              </label>
              <textarea
                rows={6}
                value={systemPrompt}
                onChange={(event) => setSystemPrompt(event.target.value)}
                placeholder="Define agent personality, behavior, constraints, and instructions..."
                className="w-full bg-white border border-zinc-300 rounded-xl p-3.5 text-xs text-zinc-900 font-mono focus:outline-none focus:border-indigo-600 focus:ring-1 focus:ring-indigo-600 leading-relaxed"
              />
            </div>

            {/* Allowed Tools - Rendered as a clean vertical list */}
            <div className="space-y-2">
              <label className="block text-xs font-bold uppercase tracking-wider text-zinc-700 flex items-center gap-1.5">
                <Wrench className="w-3.5 h-3.5 text-indigo-600" /> Permitted Tools ({allowedTools.length} enabled)
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
                          ? 'bg-indigo-50/70 border border-indigo-600 text-zinc-950 shadow-xs ring-1 ring-indigo-500/20'
                          : 'bg-white border-zinc-200 text-zinc-600 hover:border-zinc-300 hover:bg-zinc-50/70 shadow-xs'
                      }`}
                    >
                      <div className="pr-4">
                        <div className="flex items-center space-x-2">
                          <span className="font-semibold text-xs font-mono text-zinc-900">{tool.name}</span>
                          {tool.is_builtin ? (
                            <span className="text-[9px] font-semibold uppercase px-1.5 py-0.2 rounded bg-zinc-100 text-zinc-600 border border-zinc-200">
                              Builtin
                            </span>
                          ) : (
                            <span className="text-[9px] font-semibold uppercase px-1.5 py-0.2 rounded bg-indigo-100 text-indigo-700 border border-indigo-200 font-medium">
                              Custom
                            </span>
                          )}
                        </div>
                        <div className="text-[11px] text-zinc-500 mt-0.5 leading-normal">{tool.description}</div>
                      </div>
                      <div className={`w-5 h-5 rounded-md flex items-center justify-center border shrink-0 transition-colors ${
                        isChecked ? 'bg-indigo-600 border-indigo-600 text-white' : 'border-zinc-300 bg-white'
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
              <label className="block text-xs font-bold uppercase tracking-wider text-zinc-700 flex items-center gap-1.5">
                <Sparkles className="w-3.5 h-3.5 text-indigo-600" /> Preloaded Skills ({preloadedSkills.length} active)
              </label>
              <p className="text-[11px] text-zinc-500">
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
                          ? 'bg-indigo-50/70 border border-indigo-600 text-zinc-950 shadow-xs ring-1 ring-indigo-500/20'
                          : 'bg-white border-zinc-200 text-zinc-600 hover:border-zinc-300 hover:bg-zinc-50/70 shadow-xs'
                      }`}
                    >
                      <div className="pr-4">
                        <div className="font-semibold text-xs text-zinc-900">{skill.name}</div>
                        <div className="text-[11px] text-zinc-500 mt-0.5 leading-normal">{skill.description}</div>
                      </div>
                      <div className={`w-5 h-5 rounded-md flex items-center justify-center border shrink-0 transition-colors ${
                        isChecked ? 'bg-indigo-600 border-indigo-600 text-white' : 'border-zinc-300 bg-white'
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
