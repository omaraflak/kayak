import React, { useState, useEffect, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { Skill } from '../types';
import { api } from '../api/client';
import { CodeBlock } from './CodeBlock';
import { ChatPane } from './ChatPane';
import { 
  Sparkles, 
  Plus,
  Trash2, 
  Save, 
  FileCode, 
  Eye, 
  RotateCcw,
  FileText
} from 'lucide-react';
import { useDialog } from '../context/DialogContext';

interface SkillsViewProps {
  selectedId?: string | null;
  onSelectId?: (name: string | null) => void;
  onRefreshConversations?: () => void;
}

export const SkillsView: React.FC<SkillsViewProps> = ({ 
  selectedId, 
  onSelectId, 
  onRefreshConversations 
}) => {
  const dialog = useDialog();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  // Skill Architect Chat State
  const [conversationId, setConversationId] = useState<string | null>(null);

  // Form State
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [instructions, setInstructions] = useState('');
  const [activeEditorTab, setActiveEditorTab] = useState<'editor' | 'preview'>('editor');

  const loadSkills = async () => {
    try {
      const data = await api.listSkills();
      setSkills(data);
      if (data.length > 0 && !selectedSkill && !isCreating && !selectedId) {
        setSelectedSkill(data[0]);
        onSelectId?.(data[0].name);
      }
    } catch (error) {
      console.error('Failed to load skills:', error);
    }
  };

  useEffect(() => {
    loadSkills();
  }, []);

  useEffect(() => {
    if (selectedId) {
      if (selectedId === 'new') {
        setIsCreating(true);
        setSelectedSkill(null);
        setName('');
        setDescription('');
        setInstructions('# Skill Title\n\nDetailed instructions for the agent...\n');
        setConversationId(null);
      } else {
        const found = skills.find((s) => s.name === selectedId);
        if (found) {
          setIsCreating(false);
          setSelectedSkill(found);
        }
      }
    } else if (skills.length > 0 && !selectedSkill && !isCreating) {
      setSelectedSkill(skills[0]);
      onSelectId?.(skills[0].name);
    }
  }, [selectedId, skills]);

  useEffect(() => {
    if (selectedSkill && !isCreating) {
      setName(selectedSkill.name);
      setDescription(selectedSkill.description);
      setInstructions(selectedSkill.instructions);
    }
  }, [selectedSkill, isCreating]);

  const handleSkillDraftDetected = (draft: {
    name?: string;
    description?: string;
    instructions?: string;
  }) => {
    if (draft.name && !name) {
      setName(draft.name);
    } else if (draft.name && name !== draft.name) {
      setName(draft.name);
    }
    if (draft.description) {
      setDescription(draft.description);
    }
    if (draft.instructions) {
      setInstructions(draft.instructions);
    }
  };

  const isDirty = useMemo(() => {
    if (isCreating) return name.trim().length > 0 || instructions.trim().length > 0;
    if (!selectedSkill) return false;
    return (
      name !== selectedSkill.name ||
      description !== selectedSkill.description ||
      instructions !== selectedSkill.instructions
    );
  }, [isCreating, selectedSkill, name, description, instructions]);

  const handleStartCreate = () => {
    setIsCreating(true);
    setSelectedSkill(null);
    setName('');
    setDescription('');
    setInstructions('# New Skill Instructions\n\n1. Step one...\n2. Step two...');
  };

  const handleReset = () => {
    if (selectedSkill) {
      setName(selectedSkill.name);
      setDescription(selectedSkill.description);
      setInstructions(selectedSkill.instructions);
    } else if (isCreating) {
      setName('');
      setDescription('');
      setInstructions('# New Skill Instructions\n\n1. Step one...\n2. Step two...');
    }
  };

  const handleSave = async (event?: React.FormEvent) => {
    if (event) event.preventDefault();
    if (!isDirty || !name.trim() || isSaving) return;

    setIsSaving(true);
    try {
      const cleanName = name.trim().toLowerCase().replace(/[^a-z0-9_-]/g, '_');
      const saved = await api.saveSkill({
        name: cleanName,
        description: description.trim(),
        instructions,
      });
      setIsCreating(false);
      await loadSkills();
      setSelectedSkill(saved);
    } catch (error) {
      console.error('Failed to save skill:', error);
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async (skillName: string) => {
    const confirmed = await dialog.confirm({
      title: 'Delete Skill',
      message: `Are you sure you want to delete the skill "${skillName}" and its SKILL.md definition? This cannot be undone.`,
      confirmText: 'Delete',
      variant: 'danger',
    });
    if (confirmed) {
      await api.deleteSkill(skillName);
      await loadSkills();
      setSelectedSkill(null);
    }
  };

  const markdownComponents = {
    code({ node, inline, className, children, ...props }: any) {
      const match = /language-(\w+)/.exec(className || '');
      const codeStr = String(children).replace(/\n$/, '');
      if (!inline && (match || codeStr.includes('\n'))) {
        return (
          <CodeBlock
            language={match ? match[1] : 'markdown'}
            code={codeStr}
          />
        );
      }
      return (
        <code className="px-1.5 py-0.5 rounded bg-zinc-100 border border-zinc-200 text-zinc-800 font-mono text-[11px]" {...props}>
          {children}
        </code>
      );
    }
  };

  return (
    <div className="flex-1 flex h-full min-h-0 bg-zinc-50 overflow-hidden">
      {/* Skills List Sidebar with + New Button */}
      <div className="w-80 border-r border-zinc-200 bg-white flex flex-col shrink-0">
        <div className="p-4 border-b border-zinc-200 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Sparkles className="w-4 h-4 text-indigo-600" />
            <h2 className="font-bold text-xs text-zinc-900 uppercase tracking-wider">
              Skills Directory
            </h2>
          </div>
          <button
            onClick={() => {
              handleStartCreate();
              onSelectId?.('new');
            }}
            className="p-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs flex items-center gap-1 font-semibold transition-colors shadow-xs"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>New</span>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
          {skills.length === 0 ? (
            <div className="text-center py-10 px-4 text-zinc-400 text-xs">
              No skills found in <code className="font-mono">data/skills</code>.<br />Click + New to create one.
            </div>
          ) : (
            skills.map((skill) => {
              const isSelected = !isCreating && selectedSkill?.name === skill.name;
              return (
                <div
                  key={skill.name}
                  onClick={() => {
                    setIsCreating(false);
                    setSelectedSkill(skill);
                    onSelectId?.(skill.name);
                  }}
                  className={`p-3.5 rounded-xl cursor-pointer transition-all border ${
                    isSelected
                      ? 'bg-indigo-50/80 border border-indigo-600 text-zinc-950 font-medium shadow-xs ring-1 ring-indigo-500/20'
                      : 'bg-white border-zinc-200 text-zinc-700 hover:bg-zinc-50 hover:border-zinc-300 shadow-xs'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-semibold text-xs text-zinc-900">{skill.name}</span>
                    {skill.helper_files.length > 0 && (
                      <span className="text-[10px] text-zinc-500 font-mono">
                        {skill.helper_files.length} helper{skill.helper_files.length > 1 ? 's' : ''}
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-zinc-500 line-clamp-2 leading-relaxed">
                    {skill.description}
                  </p>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Main Workspace Pane: Top Header + Split (Chat 50% | Editor 50%) */}
      <div className="flex-1 flex flex-col h-full min-h-0 overflow-hidden">
        {/* Top Header Rigidly Docked */}
        <div className="h-16 border-b border-zinc-200 px-8 flex items-center justify-between bg-white shrink-0">
          <div className="flex items-center space-x-3">
            <Sparkles className="w-5 h-5 text-indigo-600" />
            <div>
              <h1 className="text-sm font-bold text-zinc-900 flex items-center gap-2">
                <span>{isCreating ? 'Create New Skill' : `Skill: ${name || 'Untitled'}`}</span>
                {isDirty && !isCreating && (
                  <span className="text-[10px] font-medium text-amber-600 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded-full flex items-center gap-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse"></span>
                    Unsaved changes
                  </span>
                )}
              </h1>
              <p className="text-[11px] text-zinc-500">
                Directory: <code className="font-mono text-zinc-700">data/skills/{name || '...'}/SKILL.md</code>
              </p>
            </div>
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

            {!isCreating && selectedSkill && (
              <button
                onClick={() => handleDelete(selectedSkill.name)}
                className="p-2 rounded-xl text-rose-600 hover:bg-rose-50 border border-rose-200 transition-colors"
                title="Delete Skill"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            )}

            <button
              onClick={() => handleSave()}
              disabled={!isDirty || !name.trim() || isSaving}
              className="px-5 py-2.5 rounded-xl text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 disabled:hover:bg-indigo-600 disabled:cursor-not-allowed flex items-center gap-2 shadow-sm transition-all focus:ring-2 focus:ring-indigo-500 focus:ring-offset-1"
            >
              <Save className="w-4 h-4" />
              <span>{isSaving ? 'Saving...' : 'Save Skill'}</span>
            </button>
          </div>
        </div>

        {/* Split View (50% AI Architect Chat | 50% Manual Editor & Preview) */}
        <div className="flex-1 flex overflow-hidden">
          {/* Left Column: Skill Architect Chat Assistant (Full ChatPane component!) */}
          <div className="w-1/2 border-r border-zinc-200 bg-zinc-50 flex flex-col overflow-hidden">
            <ChatPane
              conversationId={conversationId}
              agentId="skill_architect"
              agentName="Skill Architect"
              agentModel="gemini/gemini-3.6-flash"
              headerTitle="Skill Architect Assistant"
              headerBadge="Autonomous Synthesis"
              headerSubtitle="Describe the skill or workflow to teach your agents. Instructions and descriptions will sync directly to the live editor."
              placeholder="e.g. Write a skill for writing SQL queries and running schema migrations..."
              onConversationCreated={(newId) => setConversationId(newId)}
              onSkillDraftDetected={handleSkillDraftDetected}
              onRefreshConversations={onRefreshConversations}
            />
          </div>

          {/* Right Column: Skill Metadata & Instructions Editor / Preview (50%) */}
          <div className="w-1/2 flex flex-col bg-white overflow-hidden">
            {/* Metadata Bar */}
            <div className="p-4 border-b border-zinc-200 bg-zinc-50/50 space-y-3 shrink-0">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-[11px] font-semibold text-zinc-700 mb-1">
                    Skill Directory Name
                  </label>
                  <input
                    type="text"
                    value={name}
                    onChange={(event) => setName(event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '_'))}
                    placeholder="e.g. data_analyzer"
                    className="w-full bg-white border border-zinc-300 rounded-xl px-3 py-1.5 text-xs text-zinc-900 font-mono focus:outline-none focus:border-indigo-600 focus:ring-1 focus:ring-indigo-600"
                  />
                </div>

                <div>
                  <label className="block text-[11px] font-semibold text-zinc-700 mb-1">
                    Description / Purpose
                  </label>
                  <input
                    type="text"
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    placeholder="Brief description of when this skill triggers..."
                    className="w-full bg-white border border-zinc-300 rounded-xl px-3 py-1.5 text-xs text-zinc-900 focus:outline-none focus:border-indigo-600 focus:ring-1 focus:ring-indigo-600"
                  />
                </div>
              </div>

              {/* Tab Selector */}
              <div className="flex items-center justify-between pt-1">
                <span className="text-[11px] font-bold text-zinc-500 uppercase tracking-wider flex items-center gap-1.5">
                  <FileText className="w-3.5 h-3.5 text-zinc-600" /> SKILL.md Content
                </span>

                <div className="flex bg-zinc-100 border border-zinc-200 rounded-lg p-0.5 text-xs">
                  <button
                    onClick={() => setActiveEditorTab('editor')}
                    className={`px-3 py-1 rounded-md font-medium text-xs transition-colors flex items-center gap-1.5 ${
                      activeEditorTab === 'editor' 
                        ? 'bg-white text-zinc-900 font-semibold shadow-xs' 
                        : 'text-zinc-600 hover:text-zinc-900'
                    }`}
                  >
                    <FileCode className="w-3.5 h-3.5" />
                    <span>Markdown Editor</span>
                  </button>
                  <button
                    onClick={() => setActiveEditorTab('preview')}
                    className={`px-3 py-1 rounded-md font-medium text-xs transition-colors flex items-center gap-1.5 ${
                      activeEditorTab === 'preview' 
                        ? 'bg-white text-zinc-900 font-semibold shadow-xs' 
                        : 'text-zinc-600 hover:text-zinc-900'
                    }`}
                  >
                    <Eye className="w-3.5 h-3.5" />
                    <span>Live Preview</span>
                  </button>
                </div>
              </div>
            </div>

            {/* Content Area */}
            <div className="flex-1 overflow-y-auto p-4 bg-white">
              {activeEditorTab === 'editor' ? (
                <textarea
                  value={instructions}
                  onChange={(event) => setInstructions(event.target.value)}
                  placeholder="# Skill Instructions&#10;&#10;Detailed workflows, parameters, and constraints will sync here from the agent or you can type directly..."
                  spellCheck={false}
                  className="w-full h-full min-h-[450px] bg-zinc-50 border border-zinc-200 rounded-xl p-4 font-mono text-[12px] text-zinc-800 focus:bg-white focus:outline-none focus:border-indigo-600 focus:ring-1 focus:ring-indigo-600 resize-none leading-relaxed"
                />
              ) : (
                <div className="prose prose-zinc max-w-none text-xs leading-relaxed bg-zinc-50/50 p-6 rounded-xl border border-zinc-200">
                  <ReactMarkdown 
                    remarkPlugins={[remarkGfm, remarkMath]} 
                    rehypePlugins={[rehypeKatex]}
                    components={markdownComponents}
                  >
                    {instructions || '*No instructions written yet.*'}
                  </ReactMarkdown>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
