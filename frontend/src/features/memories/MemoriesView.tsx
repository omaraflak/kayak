import React, { useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from '../../api/client';
import { useDialog } from '../../context/DialogContext';
import { Brain, Check, Loader2, Plus, Trash2, X, XCircle } from 'lucide-react';

/**
 * Everything Kayak has learned about you, in one place.
 *
 * One list, shared by every agent: a memory describes the user, not the profile that
 * happened to be listening when they said it. Everything held about a person is shown
 * here in full and can be changed or deleted -- memory that accumulates where you
 * cannot see it is the part of this feature that would be worth distrusting.
 */

export const MemoriesView: React.FC = () => {
  const dialog = useDialog();
  const [memories, setMemories] = useState<string[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editingText, setEditingText] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      setMemories(await api.listMemories());
    } catch (error) {
      setLoadError(errorMessage(error));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  /** Runs a change against the store, keeping the list and the file in step. */
  const save = async (next: Promise<string[]>, failureTitle: string) => {
    setIsSaving(true);
    try {
      setMemories(await next);
      return true;
    } catch (error) {
      dialog.alert({ title: failureTitle, message: errorMessage(error), variant: 'danger' });
      return false;
    } finally {
      setIsSaving(false);
    }
  };

  const handleAdd = async (event: React.FormEvent) => {
    event.preventDefault();
    const content = draft.trim();
    if (!content || isSaving) return;
    if (await save(api.addMemory(content), 'Not saved')) setDraft('');
  };

  const handleSaveEdit = async (index: number) => {
    if (!memories || isSaving) return;
    const text = editingText.trim();
    if (!text) return;

    const next = memories.map((memory, i) => (i === index ? text : memory));
    if (await save(api.replaceMemories(next), 'Not saved')) setEditingIndex(null);
  };

  const handleForget = async (index: number) => {
    if (!memories || isSaving) return;
    await save(
      api.replaceMemories(memories.filter((_, i) => i !== index)),
      'Not removed'
    );
  };

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 bg-md-surface overflow-y-auto transition-colors">
      <div className="h-16 border-b border-md-outline-variant px-8 flex items-center justify-between bg-md-surface-container-low shrink-0 transition-colors sticky top-0 z-10">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded-xl bg-md-primary-container text-md-on-primary-container border border-md-outline-variant flex items-center justify-center shadow-2xs">
            <Brain className="w-4 h-4" />
          </div>
          <div>
            <h1 className="font-bold text-base text-md-on-surface">Memories</h1>
            <p className="text-xs text-md-on-surface-variant">
              What Kayak has learned about you. Every agent reads this in every
              conversation.
            </p>
          </div>
        </div>
        {memories !== null && (
          <span className="text-[11px] font-mono text-md-on-surface-variant">
            {memories.length} {memories.length === 1 ? 'memory' : 'memories'}
          </span>
        )}
      </div>

      <div className="p-8 max-w-3xl w-full mx-auto space-y-4">
        {memories === null && !loadError ? (
          <p className="text-xs text-md-on-surface-variant flex items-center gap-1.5">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading memories…
          </p>
        ) : loadError ? (
          <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <XCircle className="w-6 h-6 text-md-error" />
            <p className="text-xs text-md-on-surface-variant max-w-md leading-relaxed">
              {loadError}
            </p>
            <button
              type="button"
              onClick={load}
              className="px-4 py-2 rounded-xl bg-md-primary text-md-on-primary text-xs font-bold hover:opacity-90 transition-opacity cursor-pointer"
            >
              Try again
            </button>
          </div>
        ) : memories && memories.length > 0 ? (
          <ul className="space-y-1.5">
            {memories.map((memory, index) => (
              <li
                key={`${index}-${memory}`}
                className="group flex items-start gap-2 px-3.5 py-2.5 rounded-xl bg-md-surface-container-lowest border border-md-outline-variant"
              >
                {editingIndex === index ? (
                  <>
                    <input
                      autoFocus
                      value={editingText}
                      onChange={(event) => setEditingText(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') handleSaveEdit(index);
                        if (event.key === 'Escape') setEditingIndex(null);
                      }}
                      className="flex-1 min-w-0 bg-md-surface border border-md-primary rounded-lg px-2.5 py-1 text-xs text-md-on-surface focus:outline-none focus:ring-1 focus:ring-md-primary"
                    />
                    <button
                      type="button"
                      onClick={() => handleSaveEdit(index)}
                      disabled={!editingText.trim() || isSaving}
                      className="p-1 rounded text-md-primary hover:opacity-80 transition-opacity cursor-pointer disabled:opacity-40"
                      title="Save"
                    >
                      <Check className="w-4 h-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditingIndex(null)}
                      className="p-1 rounded text-md-on-surface-variant hover:text-md-on-surface transition-colors cursor-pointer"
                      title="Cancel"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        setEditingIndex(index);
                        setEditingText(memory);
                      }}
                      className="flex-1 text-left text-xs text-md-on-surface leading-relaxed cursor-text"
                      title="Click to edit"
                    >
                      {memory}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleForget(index)}
                      disabled={isSaving}
                      className="opacity-0 group-hover:opacity-100 focus:opacity-100 p-1 rounded text-md-on-surface-variant hover:text-md-error transition-opacity cursor-pointer disabled:opacity-40"
                      title="Forget this"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-md-on-surface-variant leading-relaxed">
            Nothing learned yet. Add something below, or correct an agent in a
            conversation and it will record what it learned.
          </p>
        )}

        {memories !== null && !loadError && (
          <form onSubmit={handleAdd} className="flex items-center gap-2">
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Teach Kayak something it should always know…"
              className="flex-1 min-w-0 bg-md-surface border border-md-outline-variant rounded-xl px-3.5 py-2 text-xs text-md-on-surface focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary transition-colors"
            />
            <button
              type="submit"
              disabled={!draft.trim() || isSaving}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-bold bg-md-primary text-md-on-primary hover:opacity-90 disabled:opacity-40 transition-opacity cursor-pointer shrink-0"
            >
              {isSaving ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Plus className="w-3.5 h-3.5" />
              )}
              Add
            </button>
          </form>
        )}

        <p className="text-[11px] text-md-on-surface-variant leading-relaxed">
          Stored as plain markdown in <code className="font-mono">data/memories.md</code>,
          so you can also edit the file directly. Agents add to this themselves when you
          correct them, using their <code className="font-mono">remember</code> tool.
        </p>
      </div>
    </div>
  );
};
