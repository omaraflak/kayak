import React, { useState, useEffect } from 'react';
import { ToolDefinition } from '../types';
import { api } from '../api/client';
import { Wrench, Plus, RefreshCw, Trash2, ShieldCheck, FileCode } from 'lucide-react';
import { ToolBuilder } from './ToolBuilder';
import { CodeBlock } from './CodeBlock';
import { useDialog } from '../context/DialogContext';

interface ToolsViewProps {
  selectedId?: string | null;
  onSelectId?: (name: string | null) => void;
  onRefreshConversations?: () => void;
}

export const ToolsView: React.FC<ToolsViewProps> = ({ 
  selectedId, 
  onSelectId, 
  onRefreshConversations 
}) => {
  const dialog = useDialog();
  const [tools, setTools] = useState<ToolDefinition[]>([]);
  const [selectedTool, setSelectedTool] = useState<ToolDefinition | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [isReloading, setIsReloading] = useState(false);

  const loadTools = async () => {
    try {
      const data = await api.listTools();
      setTools(data);
      if (data.length > 0 && !selectedTool && !isCreating && !selectedId) {
        setSelectedTool(data[0]);
        onSelectId?.(data[0].name);
      }
    } catch (error) {
      console.error('Failed to load tools:', error);
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
    } finally {
      setIsReloading(false);
    }
  };

  const handleDelete = async (toolName: string) => {
    const confirmed = await dialog.confirm({
      title: 'Delete Custom Tool',
      message: `Are you sure you want to delete tool "${toolName}" (including tool.py and verify.py)? This will remove it from the runtime registry.`,
      confirmText: 'Delete',
      variant: 'danger',
    });
    if (confirmed) {
      await api.deleteTool(toolName);
      await loadTools();
      setSelectedTool(null);
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
              title="Reload tools from disk"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isReloading ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={() => {
                setIsCreating(true);
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
          {tools.length === 0 ? (
            <div className="text-center py-10 px-4 text-md-on-surface-variant text-xs">
              No registered tools found.<br />Click + New to build a tool.
            </div>
          ) : (
            tools.map((tool) => {
              const isSelected = !isCreating && selectedTool?.name === tool.name;
              return (
                <div
                  key={tool.name}
                  onClick={() => {
                    setIsCreating(false);
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

      {/* Main Workspace Pane: Creation Studio vs Inspector */}
      {isCreating ? (
        <div className="flex-1 flex flex-col h-full min-h-0 overflow-hidden bg-md-surface">
          <ToolBuilder
            onToolActivated={() => {
              loadTools();
              setIsCreating(false);
            }}
            onRefreshConversations={onRefreshConversations}
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
              <button
                onClick={() => handleDelete(selectedTool.name)}
                className="px-3.5 py-2 rounded-xl text-xs font-semibold text-md-error bg-md-error-container hover:opacity-90 border border-md-outline-variant transition-opacity flex items-center gap-1.5 cursor-pointer"
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span>Delete Tool</span>
              </button>
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

