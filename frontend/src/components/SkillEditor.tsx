import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { Skill } from '../types';
import { MarkdownContent } from './Markdown';
import { AgentChatPrompt } from './AgentChatPrompt';
import { Eye, FileCode, FileText, Loader2, RotateCcw, Save, Sparkles } from 'lucide-react';
import { useDialog } from '../context/DialogContext';

/**
 * Editor for a skill's SKILL.md, used for both creating a new skill and editing an
 * installed one. Structured to mirror ToolEditor so the two management surfaces
 * behave the same way.
 */

const NEW_SKILL_TEMPLATE = `# Skill Title

Describe when an agent should reach for this skill.

## Steps

1. First step.
2. Second step.
`;

interface SkillEditorProps {
  /** Skill being edited, or null when creating a new one. */
  skill: Skill | null;
  onSaved: (skillName: string) => void;
  onCancel: () => void;
  onStartAgentChat?: (agentId: string) => void;
}

export const SkillEditor: React.FC<SkillEditorProps> = ({
  skill,
  onSaved,
  onCancel,
  onStartAgentChat,
}) => {
  const dialog = useDialog();
  const isNew = skill === null;

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [instructions, setInstructions] = useState('');
  const [activeTab, setActiveTab] = useState<'editor' | 'preview'>('editor');
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    setName(skill?.name ?? '');
    setDescription(skill?.description ?? '');
    setInstructions(skill?.instructions ?? NEW_SKILL_TEMPLATE);
    setActiveTab('editor');
  }, [skill]);

  const isDirty = useMemo(() => {
    if (isNew) return true;
    return (
      name !== (skill?.name ?? '') ||
      description !== (skill?.description ?? '') ||
      instructions !== (skill?.instructions ?? '')
    );
  }, [isNew, skill, name, description, instructions]);

  const canSubmit = Boolean(name.trim() && instructions.trim());

  const handleReset = () => {
    setName(skill?.name ?? '');
    setDescription(skill?.description ?? '');
    setInstructions(skill?.instructions ?? NEW_SKILL_TEMPLATE);
  };

  const handleSave = async () => {
    if (!canSubmit || isSaving) return;

    setIsSaving(true);
    try {
      const saved = await api.saveSkill({
        name: name.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '_'),
        description: description.trim(),
        instructions,
      });
      onSaved(saved.name);
    } catch (error: any) {
      dialog.alert({
        title: 'Save Failed',
        message: error?.message || 'Could not save this skill.',
        variant: 'danger',
      });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 bg-md-surface overflow-hidden transition-colors">
      <div className="h-16 border-b border-md-outline-variant px-8 flex items-center justify-between bg-md-surface-container-low shrink-0 transition-colors">
        <div className="flex items-center space-x-3 min-w-0">
          <Sparkles className="w-5 h-5 text-md-primary shrink-0" />
          <div className="min-w-0">
            <h2 className="font-bold text-sm text-md-on-surface truncate">
              {isNew ? 'New Skill' : `Editing ${skill?.name}`}
            </h2>
            <p className="text-[11px] text-md-on-surface-variant truncate">
              Saved to{' '}
              <code className="font-mono">data/skills/{name || '<name>'}/SKILL.md</code>
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
              disabled={!isDirty}
              className="px-3.5 py-2 rounded-xl text-xs font-semibold text-md-on-surface bg-md-surface-container-high border border-md-outline-variant hover:opacity-90 disabled:opacity-40 transition-opacity flex items-center gap-1.5 cursor-pointer"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>Revert</span>
            </button>
          )}
          <button
            onClick={handleSave}
            disabled={!canSubmit || isSaving || !isDirty}
            className="px-4 py-2 rounded-xl text-xs font-bold bg-emerald-700 hover:bg-emerald-800 dark:bg-emerald-600 dark:hover:bg-emerald-500 disabled:opacity-40 text-white flex items-center gap-1.5 shadow-xs transition-colors cursor-pointer"
          >
            {isSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            <span>{isNew ? 'Create Skill' : 'Save Skill'}</span>
          </button>
        </div>
      </div>

      {isNew && (
        <AgentChatPrompt
          agentId="skill_architect"
          agentLabel="Skill Architect"
          description="Rather describe the skill than write it? The Skill Architect can draft and save it for you."
          onStartAgentChat={onStartAgentChat}
        />
      )}

      <div className="p-4 border-b border-md-outline-variant bg-md-surface-container-low space-y-3 shrink-0">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-[11px] font-semibold text-md-on-surface mb-1">
              Skill Directory Name
            </label>
            <input
              type="text"
              value={name}
              disabled={!isNew}
              onChange={(event) =>
                setName(event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '_'))
              }
              placeholder="e.g. data_analyzer"
              className="w-full bg-md-surface-container-lowest border border-md-outline-variant rounded-xl px-3 py-1.5 text-xs text-md-on-surface font-mono focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary disabled:opacity-60 transition-colors"
            />
          </div>

          <div>
            <label className="block text-[11px] font-semibold text-md-on-surface mb-1">
              Description / Purpose
            </label>
            <input
              type="text"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Brief description of when this skill applies..."
              className="w-full bg-md-surface-container-lowest border border-md-outline-variant rounded-xl px-3 py-1.5 text-xs text-md-on-surface focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary transition-colors"
            />
          </div>
        </div>

        <div className="flex items-center justify-between pt-1">
          <span className="text-[11px] font-bold text-md-on-surface-variant uppercase tracking-wider flex items-center gap-1.5">
            <FileText className="w-3.5 h-3.5 text-md-primary" /> SKILL.md Content
          </span>

          <div className="flex bg-md-surface-container-high border border-md-outline-variant rounded-lg p-0.5 text-xs">
            <button
              onClick={() => setActiveTab('editor')}
              className={`px-3 py-1 rounded-md font-medium text-xs transition-colors flex items-center gap-1.5 cursor-pointer ${
                activeTab === 'editor'
                  ? 'bg-md-primary text-md-on-primary font-semibold shadow-xs'
                  : 'text-md-on-surface-variant hover:text-md-on-surface'
              }`}
            >
              <FileCode className="w-3.5 h-3.5" />
              <span>Markdown</span>
            </button>
            <button
              onClick={() => setActiveTab('preview')}
              className={`px-3 py-1 rounded-md font-medium text-xs transition-colors flex items-center gap-1.5 cursor-pointer ${
                activeTab === 'preview'
                  ? 'bg-md-primary text-md-on-primary font-semibold shadow-xs'
                  : 'text-md-on-surface-variant hover:text-md-on-surface'
              }`}
            >
              <Eye className="w-3.5 h-3.5" />
              <span>Preview</span>
            </button>
          </div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-4 bg-md-surface transition-colors">
        {activeTab === 'editor' ? (
          <textarea
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
            spellCheck={false}
            className="w-full h-full min-h-[400px] bg-md-surface-container-lowest border border-md-outline-variant rounded-xl p-4 font-mono text-[12px] text-md-on-surface focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary resize-none leading-relaxed transition-colors"
          />
        ) : (
          <div className="max-w-3xl mx-auto bg-md-surface-container-lowest p-6 rounded-xl border border-md-outline-variant">
            <MarkdownContent>{instructions || '*No instructions written yet.*'}</MarkdownContent>
          </div>
        )}
      </div>
    </div>
  );
};
