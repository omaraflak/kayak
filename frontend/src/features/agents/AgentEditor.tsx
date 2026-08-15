import React, { useEffect, useMemo, useState } from 'react';
import { AgentConfig, Skill, ToolCategoryInfo, ToolDefinition } from '../../types';
import { api, errorMessage } from '../../api/client';
import { CapabilityPicker } from './CapabilityPicker';
import { ToolAccess, deriveToolAccess, serializeToolAccess } from './agentAccess';
import { ModelSelectorModal } from '../models/ModelSelectorModal';
import { getProviderIcon } from './agentDisplay';
import { Bot, Check, Cpu, Loader2, Network, RotateCcw, Save, Sliders, Sparkles } from 'lucide-react';
import { useDialog } from '../../context/DialogContext';

/**
 * Editor for an agent profile.
 *
 * The form is the complete profile: every persisted field is represented here, so
 * saving can never drop one. The previous version rendered a subset and wrote
 * `tool_permissions: {}` unconditionally, which silently erased approval gates the
 * moment anyone adjusted an unrelated setting.
 */

const AGENT_ID_PATTERN = /^[a-z0-9_-]+$/;

interface AgentEditorProps {
  /** Agent being edited, or null when creating. */
  agent: AgentConfig | null;
  /** Existing agents, used to reject an id that would overwrite one of them. */
  existingAgents: AgentConfig[];
  /** Seed values when duplicating an existing profile. */
  duplicateOf?: AgentConfig | null;
  onSaved: (agentId: string) => void;
  onCancel: () => void;
}

export const AgentEditor: React.FC<AgentEditorProps> = ({
  agent,
  existingAgents,
  duplicateOf,
  onSaved,
  onCancel,
}) => {
  const dialog = useDialog();
  const isNew = agent === null;
  const seed = agent ?? duplicateOf ?? null;

  const [tools, setTools] = useState<ToolDefinition[]>([]);
  const [categories, setCategories] = useState<ToolCategoryInfo[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [isModelModalOpen, setIsModelModalOpen] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [id, setId] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [model, setModel] = useState('gemini/gemini-3.6-flash');
  const [temperature, setTemperature] = useState(0.7);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [toolAccess, setToolAccess] = useState<Record<string, ToolAccess>>({});
  const [preloadedSkills, setPreloadedSkills] = useState<string[]>([]);
  const [allowedSkills, setAllowedSkills] = useState<string[]>([]);
  // Sub-agent policy. "Itself" is tracked separately from other profiles because a
  // new agent's id is still being typed while this is edited.
  const [allowSelfSubagent, setAllowSelfSubagent] = useState(true);
  const [otherSubagents, setOtherSubagents] = useState<string[]>([]);

  // Tools and skills are re-read when the editor opens: they change from other tabs
  // and from agents installing them, and a stale list would silently drop grants.
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.listTools(), api.listToolCategories(), api.listSkills()])
      .then(([toolsData, categoriesData, skillsData]) => {
        if (cancelled) return;
        setTools(toolsData);
        setCategories(categoriesData);
        setSkills(skillsData);
        setToolAccess(deriveToolAccess(seed, toolsData));
      })
      .catch((err) => console.error('Failed to load tools/skills metadata:', err));
    return () => {
      cancelled = true;
    };
  }, [seed]);

  useEffect(() => {
    // A duplicate starts from the source profile but must not reuse its id.
    setId(agent?.id ?? (duplicateOf ? `${duplicateOf.id}_copy` : ''));
    setName(agent?.name ?? (duplicateOf ? `${duplicateOf.name} (copy)` : ''));
    setDescription(seed?.description ?? '');
    setModel(seed?.model ?? 'gemini/gemini-3.6-flash');
    setTemperature(seed?.temperature ?? 0.7);
    setSystemPrompt(seed?.system_prompt ?? '');
    setPreloadedSkills(seed?.preloaded_skills ?? []);
    setAllowedSkills(seed?.allowed_skills ?? []);
    // Null means the default policy: only the agent's own profile.
    const seedSubagents = seed?.allowed_subagents ?? null;
    setAllowSelfSubagent(seedSubagents === null || (!!seed && seedSubagents.includes(seed.id)));
    setOtherSubagents(
      seedSubagents === null
        ? []
        : seedSubagents.filter((subagentId) => subagentId !== seed?.id)
    );
    setError(null);
  }, [agent, duplicateOf, seed]);

  const idError = useMemo(() => {
    if (!isNew) return null;
    const trimmed = id.trim();
    if (!trimmed) return 'An identifier is required.';
    if (!AGENT_ID_PATTERN.test(trimmed))
      return 'Use lowercase letters, numbers, underscores, and hyphens only.';
    if (existingAgents.some((candidate) => candidate.id === trimmed))
      return `An agent called "${trimmed}" already exists. Saving would overwrite it.`;
    return null;
  }, [isNew, id, existingAgents]);

  const canSave = Boolean(name.trim()) && !idError;

  const handleReset = () => {
    if (!seed) return;
    setId(seed.id);
    setName(seed.name);
    setDescription(seed.description);
    setModel(seed.model);
    setTemperature(seed.temperature);
    setSystemPrompt(seed.system_prompt);
    setPreloadedSkills(seed.preloaded_skills ?? []);
    setAllowedSkills(seed.allowed_skills ?? []);
    const seedSubagents = seed.allowed_subagents ?? null;
    setAllowSelfSubagent(seedSubagents === null || seedSubagents.includes(seed.id));
    setOtherSubagents(
      seedSubagents === null
        ? []
        : seedSubagents.filter((subagentId) => subagentId !== seed.id)
    );
    setToolAccess(deriveToolAccess(seed, tools));
    setError(null);
  };

  const handleSave = async () => {
    if (!canSave || isSaving) return;
    setIsSaving(true);
    setError(null);
    try {
      const { allowed_tools, tool_permissions } = serializeToolAccess(toolAccess);
      const finalId = id.trim();
      const payload: AgentConfig = {
        id: finalId,
        name: name.trim(),
        description: description.trim(),
        model,
        temperature,
        system_prompt: systemPrompt,
        allowed_tools,
        // Preserved rather than recomputed: this field has no editor yet, and
        // dropping it would quietly widen which skills the agent can load.
        allowed_skills: allowedSkills,
        preloaded_skills: preloadedSkills,
        tool_permissions,
        allowed_subagents: [
          ...(allowSelfSubagent ? [finalId] : []),
          ...otherSubagents.filter((subagentId) => subagentId !== finalId),
        ],
      };
      await api.saveAgent(payload);
      onSaved(payload.id);
    } catch (err) {
      const reason = errorMessage(err) || 'Could not save this agent profile.';
      setError(reason);
      dialog.alert({
        title: 'Save Failed',
        message: reason,
        variant: 'danger',
      });
    } finally {
      setIsSaving(false);
    }
  };

  const togglePreloadedSkill = (skillName: string) => {
    setPreloadedSkills((prev) =>
      prev.includes(skillName)
        ? prev.filter((existing) => existing !== skillName)
        : [...prev, skillName]
    );
  };

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 bg-md-surface overflow-hidden transition-colors">
      <ModelSelectorModal
        isOpen={isModelModalOpen}
        onClose={() => setIsModelModalOpen(false)}
        selectedModel={model}
        onSelectModel={(newModel) => setModel(newModel)}
      />

      <div className="h-16 border-b border-md-outline-variant px-8 flex items-center justify-between bg-md-surface-container-low shrink-0 transition-colors">
        <div className="flex items-center space-x-3 min-w-0">
          <Bot className="w-5 h-5 text-md-primary shrink-0" />
          <div className="min-w-0">
            <h2 className="font-bold text-sm text-md-on-surface truncate">
              {isNew ? (duplicateOf ? `Duplicate of ${duplicateOf.name}` : 'New Agent') : `Editing ${agent?.name}`}
            </h2>
            <p className="text-[11px] text-md-on-surface-variant truncate">
              Saved to <code className="font-mono">data/agents/{id || '<id>'}.yaml</code>
            </p>
          </div>
        </div>

        <div className="flex items-center space-x-2 shrink-0">
          <button
            onClick={onCancel}
            className="px-3.5 py-2 rounded-xl text-xs font-semibold text-md-on-surface bg-md-surface-container-high border border-md-outline-variant hover:opacity-90 transition-opacity cursor-pointer"
          >
            Cancel
          </button>
          {!isNew && (
            <button
              onClick={handleReset}
              className="px-3.5 py-2 rounded-xl text-xs font-semibold text-md-on-surface bg-md-surface-container-high border border-md-outline-variant hover:opacity-90 transition-opacity flex items-center gap-1.5 cursor-pointer"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>Revert</span>
            </button>
          )}
          <button
            onClick={handleSave}
            disabled={!canSave || isSaving}
            className="px-4 py-2 rounded-xl text-xs font-bold bg-emerald-700 hover:bg-emerald-800 dark:bg-emerald-600 dark:hover:bg-emerald-500 disabled:opacity-40 text-white flex items-center gap-1.5 shadow-xs transition-colors cursor-pointer"
          >
            {isSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            <span>{isNew ? 'Create Agent' : 'Save Profile'}</span>
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-8 bg-md-surface">
        <div className="space-y-6 max-w-4xl mx-auto pb-12">
          {error && (
            <div className="rounded-xl border border-md-outline-variant bg-md-error-container px-4 py-3 text-xs text-md-error">
              {error}
            </div>
          )}

          {/* Identity */}
          <section className="space-y-3">
            <h3 className="text-xs font-bold uppercase tracking-wider text-md-on-surface">Identity</h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-md-on-surface mb-1.5">
                  Agent Identifier
                </label>
                <input
                  type="text"
                  value={id}
                  disabled={!isNew}
                  onChange={(event) => setId(event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '_'))}
                  placeholder="e.g. autonomous_coder"
                  className={`w-full bg-md-surface-container-lowest border rounded-xl px-3.5 py-2.5 text-xs text-md-on-surface font-mono focus:outline-none focus:ring-1 disabled:opacity-60 transition-colors ${
                    idError
                      ? 'border-md-error focus:border-md-error focus:ring-md-error'
                      : 'border-md-outline-variant focus:border-md-primary focus:ring-md-primary'
                  }`}
                />
                {idError && <p className="text-[11px] text-md-error mt-1">{idError}</p>}
                {!isNew && (
                  <p className="text-[10px] text-md-on-surface-variant mt-1">
                    Identifiers are fixed. Use Duplicate to create a differently named copy.
                  </p>
                )}
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
                />
              </div>
            </div>

            <div>
              <label className="block text-xs font-semibold text-md-on-surface mb-1.5">Description</label>
              <input
                type="text"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="What this agent is for."
                className="w-full bg-md-surface-container-lowest border border-md-outline-variant rounded-xl px-3.5 py-2.5 text-xs text-md-on-surface focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary transition-colors"
              />
            </div>
          </section>

          {/* Model & sampling */}
          <section className="p-5 bg-md-surface border border-md-outline-variant rounded-2xl shadow-xs space-y-4">
            <h3 className="text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
              <Sliders className="w-3.5 h-3.5 text-md-primary" /> Model &amp; Sampling
            </h3>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-start">
              <div>
                <label className="block text-xs font-semibold text-md-on-surface mb-1.5">Model</label>
                <div className="p-4 bg-md-surface-container-lowest border border-md-outline-variant rounded-xl flex items-center justify-between shadow-xs">
                  <div className="flex items-center space-x-3 overflow-hidden mr-2">
                    <div className="w-10 h-10 rounded-xl bg-md-surface-container-high border border-md-outline-variant flex items-center justify-center text-xl shrink-0">
                      {getProviderIcon(model)}
                    </div>
                    <div className="truncate min-w-0">
                      <div className="text-xs font-bold text-md-on-surface font-mono truncate">{model}</div>
                      <div className="text-[11px] text-md-on-surface-variant mt-0.5">
                        {model.startsWith('vllm/') ? 'Local server' : 'Cloud provider'} · LiteLLM
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

              <div>
                <div className="flex justify-between items-center mb-1.5">
                  <label className="text-xs font-semibold text-md-on-surface">Temperature</label>
                  <span className="font-mono text-xs text-md-primary font-bold">{temperature.toFixed(2)}</span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="2"
                  step="0.05"
                  value={temperature}
                  onChange={(event) => setTemperature(parseFloat(event.target.value))}
                  className="w-full h-2 bg-md-surface-container-high rounded-lg appearance-none cursor-pointer accent-md-primary mt-2"
                />
                <div className="flex justify-between text-[10px] text-md-on-surface-variant mt-1.5">
                  <span>0.0 deterministic</span>
                  <span>2.0 exploratory</span>
                </div>
              </div>
            </div>
          </section>

          {/* Instructions */}
          <section className="space-y-2">
            <h3 className="text-xs font-bold uppercase tracking-wider text-md-on-surface">
              System Prompt
            </h3>
            <textarea
              rows={10}
              value={systemPrompt}
              onChange={(event) => setSystemPrompt(event.target.value)}
              placeholder="Define this agent's role, behavior, and constraints..."
              className="w-full bg-md-surface-container-lowest border border-md-outline-variant rounded-xl p-3.5 text-xs text-md-on-surface font-mono focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary leading-relaxed resize-y transition-colors"
            />
          </section>

          {/* Capabilities */}
          <section>
            <CapabilityPicker
              tools={tools}
              categories={categories}
              access={toolAccess}
              onChange={(toolName, value) =>
                setToolAccess((prev) => ({ ...prev, [toolName]: value }))
              }
              onBulkChange={(toolNames, value) =>
                setToolAccess((prev) => {
                  const next = { ...prev };
                  for (const toolName of toolNames) next[toolName] = value;
                  return next;
                })
              }
            />
          </section>

          {/* Skills */}
          <section className="space-y-2">
            <label className="text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
              <Sparkles className="w-3.5 h-3.5 text-md-primary" /> Preloaded Skills ({preloadedSkills.length})
            </label>
            <p className="text-[11px] text-md-on-surface-variant leading-relaxed">
              Preloaded skills are injected in full at the start of every turn. Every other skill
              stays discoverable and is fetched on demand with <code className="font-mono">load_skill</code>.
            </p>

            {allowedSkills.length > 0 && (
              <div className="flex items-center justify-between gap-3 rounded-xl border border-md-outline-variant bg-md-surface-container-high px-3.5 py-2.5">
                <p className="text-[11px] text-md-on-surface leading-relaxed">
                  On-demand skills are restricted to:{' '}
                  <span className="font-mono">{allowedSkills.join(', ')}</span>
                </p>
                <button
                  type="button"
                  onClick={() => setAllowedSkills([])}
                  className="shrink-0 text-[11px] font-semibold px-2.5 py-1 rounded-lg border border-md-outline-variant text-md-on-surface hover:bg-md-surface-container transition-colors cursor-pointer"
                >
                  Clear restriction
                </button>
              </div>
            )}

            <div className="space-y-2">
              {skills.map((skill) => {
                const isPreloaded = preloadedSkills.includes(skill.name);
                return (
                  <div
                    key={skill.name}
                    className={`p-3 rounded-xl border flex items-center justify-between gap-4 transition-colors ${
                      isPreloaded
                        ? 'bg-md-primary-container/40 border-md-primary/50'
                        : 'bg-md-surface border-md-outline-variant'
                    }`}
                  >
                    <div className="min-w-0">
                      <div className="font-semibold text-xs font-mono text-md-on-surface">{skill.name}</div>
                      <p className="text-[11px] text-md-on-surface-variant mt-0.5 line-clamp-2 leading-normal">
                        {skill.description}
                      </p>
                    </div>
                    <div className="flex bg-md-surface-container-high border border-md-outline-variant rounded-lg p-0.5 shrink-0">
                      {[
                        { value: false, label: 'On demand' },
                        { value: true, label: 'Preloaded' },
                      ].map((option) => (
                        <button
                          key={String(option.value)}
                          type="button"
                          onClick={() => {
                            if (option.value !== isPreloaded) togglePreloadedSkill(skill.name);
                          }}
                          className={`px-2.5 py-1 rounded-md text-[11px] font-semibold transition-colors cursor-pointer ${
                            isPreloaded === option.value
                              ? 'bg-md-primary text-md-on-primary shadow-xs'
                              : 'text-md-on-surface-variant hover:text-md-on-surface'
                          }`}
                        >
                          {option.label}
                        </button>
                      ))}
                    </div>
                  </div>
                );
              })}
              {skills.length === 0 && (
                <p className="text-xs text-md-on-surface-variant py-4 text-center">
                  No skills installed yet.
                </p>
              )}
            </div>
          </section>

          {/* Sub-agent policy */}
          <section className="space-y-2">
            <label className="text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
              <Network className="w-3.5 h-3.5 text-md-primary" /> Sub-agents
            </label>
            <p className="text-[11px] text-md-on-surface-variant leading-relaxed">
              Profiles this agent may start with <code className="font-mono">spawn_subagent</code>.
              By default an agent may only start its own profile — otherwise it could
              sidestep its restrictions by delegating to a more permissive agent.
            </p>

            <div className="space-y-2">
              {[
                {
                  key: '__self__',
                  title: `${name.trim() || 'This agent'} (itself)`,
                  subtitle: 'Delegate focused sub-tasks to a copy of this profile.',
                  checked: allowSelfSubagent,
                  toggle: () => setAllowSelfSubagent((previous) => !previous),
                },
                ...existingAgents
                  .filter((candidate) => candidate.id !== (agent?.id ?? id.trim()))
                  .map((candidate) => ({
                    key: candidate.id,
                    title: `${candidate.name} (${candidate.id})`,
                    subtitle: candidate.description,
                    checked: otherSubagents.includes(candidate.id),
                    toggle: () =>
                      setOtherSubagents((previous) =>
                        previous.includes(candidate.id)
                          ? previous.filter((existing) => existing !== candidate.id)
                          : [...previous, candidate.id]
                      ),
                  })),
              ].map((row) => (
                <button
                  key={row.key}
                  type="button"
                  onClick={row.toggle}
                  className={`w-full p-3 rounded-xl border flex items-center justify-between gap-4 transition-colors text-left cursor-pointer ${
                    row.checked
                      ? 'bg-md-primary-container/40 border-md-primary/50'
                      : 'bg-md-surface border-md-outline-variant hover:border-md-outline'
                  }`}
                >
                  <div className="min-w-0">
                    <div className="font-semibold text-xs text-md-on-surface">{row.title}</div>
                    <p className="text-[11px] text-md-on-surface-variant mt-0.5 line-clamp-2 leading-normal">
                      {row.subtitle}
                    </p>
                  </div>
                  <span
                    className={`w-5 h-5 rounded-md border flex items-center justify-center shrink-0 transition-colors ${
                      row.checked
                        ? 'bg-md-primary border-md-primary text-md-on-primary'
                        : 'border-md-outline-variant text-transparent'
                    }`}
                  >
                    <Check className="w-3.5 h-3.5 stroke-[3]" />
                  </span>
                </button>
              ))}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
};
