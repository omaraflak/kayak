import React, { useState, useEffect } from 'react';
import { ToolDefinition } from '../../types';
import { api, errorMessage } from '../../api/client';
import { Wrench, Plus, RefreshCw, Trash2, ShieldCheck, FileCode, Pencil } from 'lucide-react';
import { ToolEditor } from './ToolEditor';
import { CodeBlock } from '../../ui/CodeBlock';
import { useDialog } from '../../context/DialogContext';

interface ToolsViewProps {
  selectedId?: string | null;
  onSelectId?: (name: string | null) => void;
  onStartAgentChat?: (agentId: string) => void;
}

export const ToolsView: React.FC<ToolsViewProps> = ({
  selectedId,
  onSelectId,
  onStartAgentChat,
}) => {
  const dialog = useDialog();
  const [tools, setTools] = useState<ToolDefinition[]>([]);
  const [selectedTool, setSelectedTool] = useState<ToolDefinition | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [isReloading, setIsReloading] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  const loadTools = async () => {
    try {
      const data = await api.listTools();
      setTools(data);
      setLoadFailed(false);
      if (data.length > 0 && !selectedTool && !isCreating && !selectedId) {
        setSelectedTool(data[0]);
        onSelectId?.(data[0].name);
      }
    } catch (error) {
      console.error('Failed to load tools:', error);
      setLoadFailed(true);
    }
  };

  useEffect(() => {
    loadTools();
  }, []);

  useEffect(() => {
    if (selectedId) {
      if (selectedId === 'new') {
        setIsCreating(true);
        setSelectedTool(null);
      } else {
        const found = tools.find((t) => t.name === selectedId);
        if (found) {
          setIsCreating(false);
          if (found.name !== selectedTool?.name) setIsEditing(false);
          setSelectedTool(found);
        }
      }
    } else if (tools.length > 0 && !selectedTool && !isCreating) {
      setSelectedTool(tools[0]);
      onSelectId?.(tools[0].name);
    }
  }, [selectedId, tools]);

  const handleReload = async () => {
    setIsReloading(true);
    try {
      await api.reloadTools();
      await loadTools();
    } catch (error) {
      dialog.alert({
        title: "Couldn't refresh the tools",
        message: errorMessage(error, "Couldn't refresh the list of tools. Try again."),
        variant: 'danger',
      });
    } finally {
      setIsReloading(false);
    }
  };

  const handleDelete = async (toolName: string) => {
    const confirmed = await dialog.confirm({
      title: 'Delete this tool?',
      message: `Agents will no longer be able to use "${toolName}". This can't be undone.`,
      confirmText: 'Delete',
      variant: 'danger',
    });
    if (!confirmed) return;
    try {
      await api.deleteTool(toolName);
      setSelectedTool(null);
      await loadTools();
    } catch (error) {
      dialog.alert({
        title: "Couldn't delete the tool",
        message: errorMessage(error, "Couldn't delete the tool. Try again."),
        variant: 'danger',
      });
    }
  };

  return (
    <div className="flex-1 flex h-full min-h-0 bg-md-surface overflow-hidden transition-colors">
      {/* Tools List Sidebar */}
      <div className="w-80 border-r border-md-outline-variant bg-md-surface-container-low flex flex-col shrink-0 transition-colors">
        <div className="p-4 border-b border-md-outline-variant flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Wrench className="w-4 h-4 text-md-primary" />
            <h2 className="font-bold text-xs text-md-on-surface uppercase tracking-wider">
              Tools ({tools.length})
            </h2>
          </div>

          <div className="flex items-center space-x-1.5">
            <button
              onClick={handleReload}
              disabled={isReloading}
              className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high transition-colors cursor-pointer"
              title="Refresh"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isReloading ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={() => {
                setIsCreating(true);
                setIsEditing(false);
                setSelectedTool(null);
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
          {loadFailed && tools.length === 0 ? (
            <div className="text-center py-10 px-4 text-md-on-surface-variant text-xs space-y-2">
              <p>Couldn't load the tools.</p>
              <button
                type="button"
                onClick={loadTools}
                className="font-bold text-md-primary underline cursor-pointer"
              >
                Try again
              </button>
            </div>
          ) : tools.length === 0 ? (
            <div className="text-center py-10 px-4 text-md-on-surface-variant text-xs">
              No tools yet.<br />Click New to write one, or ask the Tool Architect agent in a chat.
            </div>
          ) : (
            tools.map((tool) => {
              const isSelected = !isCreating && selectedTool?.name === tool.name;
              return (
                <div
                  key={tool.name}
                  onClick={() => {
                    setIsCreating(false);
                    setIsEditing(false);
                    setSelectedTool(tool);
                    onSelectId?.(tool.name);
                  }}
                  className={`p-3.5 rounded-xl cursor-pointer transition-all border ${
                    isSelected
                      ? 'bg-md-primary-container text-md-on-primary-container border-md-primary shadow-xs ring-1 ring-md-primary/40 font-medium'
                      : 'bg-md-surface border-md-outline-variant text-md-on-surface hover:bg-md-surface-container hover:border-md-outline shadow-xs'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-semibold text-xs font-mono text-md-on-surface">{tool.name}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono border ${
                      tool.is_builtin
                        ? 'bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant'
                        : 'bg-md-tertiary-container text-md-on-tertiary-container border-md-outline-variant font-medium'
                    }`}>
                      {tool.is_builtin ? 'builtin' : 'custom'}
                    </span>
                  </div>
                  <p className="text-[11px] text-md-on-surface-variant line-clamp-2 leading-relaxed">
                    {tool.description}
                  </p>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Main Workspace Pane: Editor (create or edit) vs read-only Inspector */}
      {isCreating || isEditing ? (
        <div className="flex-1 flex flex-col h-full min-h-0 overflow-hidden bg-md-surface">
          <ToolEditor
            tool={isCreating ? null : selectedTool}
            onStartAgentChat={onStartAgentChat}
            onSaved={async (savedName) => {
              setIsCreating(false);
              setIsEditing(false);
              onSelectId?.(savedName);
              try {
                const refreshed = await api.listTools();
                setTools(refreshed);
                setSelectedTool(refreshed.find((t) => t.name === savedName) ?? null);
              } catch {
                // Installed; only the refresh failed. The list says so itself.
                setLoadFailed(true);
              }
            }}
            onCancel={() => {
              setIsCreating(false);
              setIsEditing(false);
              onSelectId?.(selectedTool?.name ?? null);
            }}
          />
        </div>
      ) : (
        <div className="flex-1 flex flex-col h-full min-h-0 overflow-hidden bg-md-surface">
          {/* Top Header rigidly docked at top */}
          <div className="h-16 border-b border-md-outline-variant px-8 flex items-center justify-between bg-md-surface-container-low shrink-0 transition-colors">
            <div>
              <div className="flex items-center space-x-2.5">
                <h1 className="text-base font-bold font-mono text-md-on-surface">
                  {selectedTool ? selectedTool.name : 'Select Tool'}
                </h1>
                {selectedTool && (
                  <span className={`text-xs px-2.5 py-0.5 rounded-full font-medium border ${
                    selectedTool.is_builtin
                      ? 'bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant'
                      : 'bg-md-tertiary-container text-md-on-tertiary-container border-md-outline-variant'
                  }`}>
                    {selectedTool.is_builtin ? 'Built-in Tool' : 'Custom Tool'}
                  </span>
                )}
              </div>
              <p className="text-xs text-md-on-surface-variant mt-0.5">
                {selectedTool?.description || 'Select a tool to inspect its parameters and source code.'}
              </p>
            </div>

            {selectedTool && !selectedTool.is_builtin && (
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setIsEditing(true)}
                  className="px-3.5 py-2 rounded-xl text-xs font-semibold text-md-on-primary bg-md-primary hover:opacity-90 transition-opacity flex items-center gap-1.5 shadow-xs cursor-pointer"
                >
                  <Pencil className="w-3.5 h-3.5" />
                  <span>Edit Tool</span>
                </button>
                <button
                  onClick={() => handleDelete(selectedTool.name)}
                  className="px-3.5 py-2 rounded-xl text-xs font-semibold text-md-error bg-md-error-container hover:opacity-90 border border-md-outline-variant transition-opacity flex items-center gap-1.5 cursor-pointer"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>Delete Tool</span>
                </button>
              </div>
            )}
          </div>

          {/* Scrollable Tool Details */}
          <div className="flex-1 overflow-y-auto p-8 w-full space-y-6 bg-md-surface">
            {selectedTool ? (
              <div className="max-w-5xl mx-auto space-y-6 pb-12">
                {/* Parameters Schema */}
                <div className="space-y-2">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-md-on-surface">
                    Input Parameters Schema
                  </h3>
                  <CodeBlock
                    code={JSON.stringify(selectedTool.parameters, null, 2)}
                    language="json"
                  />
                </div>

                {/* Source Code if Custom Tool */}
                {selectedTool.source_code && (
                  <div className="space-y-2">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
                      <FileCode className="w-4 h-4 text-md-primary" /> Python Source Code (tool.py)
                    </h3>
                    <CodeBlock
                      code={selectedTool.source_code}
                      language="python"
                    />
                  </div>
                )}

                {/* Verification Test Code */}
                {selectedTool.verify_code && (
                  <div className="space-y-2">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
                      <ShieldCheck className="w-4 h-4 text-md-primary" /> Verification Test Suite (verify.py)
                    </h3>
                    <CodeBlock
                      code={selectedTool.verify_code}
                      language="python"
                    />
                  </div>
                )}
              </div>
            ) : (
              <div className="text-center py-20 text-md-on-surface-variant text-sm">
                Select a tool from the list to view its specification and implementation.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

