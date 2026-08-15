import React, { useState, useEffect } from 'react';
import { Skill } from '../../types';
import { api } from '../../api/client';
import { MarkdownContent } from '../../ui/Markdown';
import { SkillEditor } from './SkillEditor';
import { Sparkles, Plus, RefreshCw, Trash2, Pencil, FileText, Paperclip } from 'lucide-react';
import { useDialog } from '../../context/DialogContext';

/**
 * Skills library: browse and read installed skills, edit them, or create one.
 *
 * Deliberately structured like ToolsView -- same sidebar, same header, same
 * view-then-edit flow -- so the two management surfaces are learned once.
 */

interface SkillsViewProps {
  selectedId?: string | null;
  onSelectId?: (name: string | null) => void;
  onStartAgentChat?: (agentId: string) => void;
}

export const SkillsView: React.FC<SkillsViewProps> = ({
  selectedId,
  onSelectId,
  onStartAgentChat,
}) => {
  const dialog = useDialog();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [isReloading, setIsReloading] = useState(false);

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
        setIsEditing(false);
        setSelectedSkill(null);
      } else {
        const found = skills.find((s) => s.name === selectedId);
        if (found) {
          setIsCreating(false);
          if (found.name !== selectedSkill?.name) setIsEditing(false);
          setSelectedSkill(found);
        }
      }
    } else if (skills.length > 0 && !selectedSkill && !isCreating) {
      setSelectedSkill(skills[0]);
      onSelectId?.(skills[0].name);
    }
  }, [selectedId, skills]);

  const handleReload = async () => {
    setIsReloading(true);
    try {
      await loadSkills();
    } finally {
      setIsReloading(false);
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
      onSelectId?.(null);
    }
  };

  return (
    <div className="flex-1 flex h-full min-h-0 bg-md-surface overflow-hidden transition-colors">
      {/* Skills List Sidebar */}
      <div className="w-80 border-r border-md-outline-variant bg-md-surface-container-low flex flex-col shrink-0 transition-colors">
        <div className="p-4 border-b border-md-outline-variant flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Sparkles className="w-4 h-4 text-md-primary" />
            <h2 className="font-bold text-xs text-md-on-surface uppercase tracking-wider">
              Skills ({skills.length})
            </h2>
          </div>

          <div className="flex items-center space-x-1.5">
            <button
              onClick={handleReload}
              disabled={isReloading}
              className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high transition-colors cursor-pointer"
              title="Reload skills from disk"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isReloading ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={() => {
                setIsCreating(true);
                setIsEditing(false);
                setSelectedSkill(null);
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
          {skills.length === 0 ? (
            <div className="text-center py-10 px-4 text-md-on-surface-variant text-xs leading-relaxed">
              No skills found in <code className="font-mono">data/skills</code>.
              <br />
              Click + New to write one, or ask the Skill Architect agent in a chat.
            </div>
          ) : (
            skills.map((skill) => {
              const isSelected = !isCreating && selectedSkill?.name === skill.name;
              return (
                <div
                  key={skill.name}
                  onClick={() => {
                    setIsCreating(false);
                    setIsEditing(false);
                    setSelectedSkill(skill);
                    onSelectId?.(skill.name);
                  }}
                  className={`p-3.5 rounded-xl cursor-pointer transition-all border ${
                    isSelected
                      ? 'bg-md-primary-container text-md-on-primary-container border-md-primary shadow-xs ring-1 ring-md-primary/40 font-medium'
                      : 'bg-md-surface border-md-outline-variant text-md-on-surface hover:bg-md-surface-container hover:border-md-outline shadow-xs'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-semibold text-xs font-mono text-md-on-surface truncate">
                      {skill.name}
                    </span>
                    {skill.helper_files.length > 0 && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded font-mono border bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant shrink-0">
                        {skill.helper_files.length} file{skill.helper_files.length > 1 ? 's' : ''}
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-md-on-surface-variant line-clamp-2 leading-relaxed">
                    {skill.description}
                  </p>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Main Workspace Pane: Editor (create or edit) vs read-only Viewer */}
      {isCreating || isEditing ? (
        <div className="flex-1 flex flex-col h-full min-h-0 overflow-hidden bg-md-surface">
          <SkillEditor
            skill={isCreating ? null : selectedSkill}
            onStartAgentChat={onStartAgentChat}
            onSaved={async (savedName) => {
              setIsCreating(false);
              setIsEditing(false);
              const refreshed = await api.listSkills();
              setSkills(refreshed);
              setSelectedSkill(refreshed.find((s) => s.name === savedName) ?? null);
              onSelectId?.(savedName);
            }}
            onCancel={() => {
              setIsCreating(false);
              setIsEditing(false);
              onSelectId?.(selectedSkill?.name ?? null);
            }}
          />
        </div>
      ) : (
        <div className="flex-1 flex flex-col h-full min-h-0 overflow-hidden bg-md-surface">
          {/* Top Header rigidly docked at top */}
          <div className="h-16 border-b border-md-outline-variant px-8 flex items-center justify-between bg-md-surface-container-low shrink-0 transition-colors">
            <div className="min-w-0">
              <div className="flex items-center space-x-2.5">
                <h1 className="text-base font-bold font-mono text-md-on-surface truncate">
                  {selectedSkill ? selectedSkill.name : 'Select Skill'}
                </h1>
              </div>
              <p className="text-xs text-md-on-surface-variant mt-0.5 truncate">
                {selectedSkill?.description || 'Select a skill to read its instructions.'}
              </p>
            </div>

            {selectedSkill && (
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => setIsEditing(true)}
                  className="px-3.5 py-2 rounded-xl text-xs font-semibold text-md-on-primary bg-md-primary hover:opacity-90 transition-opacity flex items-center gap-1.5 shadow-xs cursor-pointer"
                >
                  <Pencil className="w-3.5 h-3.5" />
                  <span>Edit Skill</span>
                </button>
                <button
                  onClick={() => handleDelete(selectedSkill.name)}
                  className="px-3.5 py-2 rounded-xl text-xs font-semibold text-md-error bg-md-error-container hover:opacity-90 border border-md-outline-variant transition-opacity flex items-center gap-1.5 cursor-pointer"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>Delete Skill</span>
                </button>
              </div>
            )}
          </div>

          {/* Scrollable Skill Details */}
          <div className="flex-1 overflow-y-auto p-8 w-full space-y-6 bg-md-surface">
            {selectedSkill ? (
              <div className="max-w-5xl mx-auto space-y-6 pb-12">
                <div className="space-y-2">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
                    <FileText className="w-4 h-4 text-md-primary" /> Instructions (SKILL.md)
                  </h3>
                  <div className="bg-md-surface-container-lowest p-6 rounded-xl border border-md-outline-variant">
                    <MarkdownContent>
                      {selectedSkill.instructions || '*This skill has no instructions yet.*'}
                    </MarkdownContent>
                  </div>
                </div>

                {selectedSkill.helper_files.length > 0 && (
                  <div className="space-y-2">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
                      <Paperclip className="w-4 h-4 text-md-primary" /> Helper Files
                    </h3>
                    <div className="flex flex-wrap gap-2">
                      {selectedSkill.helper_files.map((file) => (
                        <span
                          key={file}
                          className="text-[11px] font-mono px-2.5 py-1 rounded-lg bg-md-surface-container-high text-md-on-surface border border-md-outline-variant"
                        >
                          {file}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="text-center py-20 text-md-on-surface-variant text-sm">
                Select a skill from the list to read its instructions.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
