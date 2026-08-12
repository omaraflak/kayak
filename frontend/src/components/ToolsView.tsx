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
    <div className="flex-1 flex h-full min-h-0 bg-zinc-50 dark:bg-zinc-950 overflow-hidden transition-colors">
      {/* Tools List Sidebar */}
      <div className="w-80 border-r border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 flex flex-col shrink-0 transition-colors">
        <div className="p-4 border-b border-zinc-200 dark:border-zinc-800 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Wrench className="w-4 h-4 text-zinc-700 dark:text-zinc-300" />
            <h2 className="font-bold text-xs text-zinc-900 dark:text-zinc-100 uppercase tracking-wider">
              Tools ({tools.length})
            </h2>
          </div>

          <div className="flex items-center space-x-1.5">
            <button
              onClick={handleReload}
              disabled={isReloading}
              className="p-1.5 rounded-lg text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
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
              className="p-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white text-xs flex items-center gap-1 font-semibold transition-colors shadow-xs"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>New</span>
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
          {tools.length === 0 ? (
            <div className="text-center py-10 px-4 text-zinc-400 dark:text-zinc-500 text-xs">
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
                      ? 'bg-indigo-50/90 dark:bg-indigo-950/80 border-indigo-600 dark:border-indigo-400 text-zinc-950 dark:text-zinc-100 font-medium shadow-xs ring-1 ring-indigo-500/20'
                      : 'bg-white dark:bg-zinc-800/80 border-zinc-200 dark:border-zinc-700 text-zinc-800 dark:text-zinc-200 hover:bg-zinc-100/70 dark:hover:bg-zinc-750 hover:border-zinc-300 dark:hover:border-zinc-600 shadow-xs'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-semibold text-xs font-mono text-zinc-900 dark:text-zinc-100">{tool.name}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono border ${
                      tool.is_builtin
                        ? 'bg-zinc-100 dark:bg-zinc-900 text-zinc-700 dark:text-zinc-300 border-zinc-200 dark:border-zinc-700'
                        : 'bg-indigo-100 dark:bg-indigo-900 text-indigo-800 dark:text-indigo-200 border border-indigo-200 dark:border-indigo-700 font-medium'
                    }`}>
                      {tool.is_builtin ? 'builtin' : 'custom'}
                    </span>
                  </div>
                  <p className="text-[11px] text-zinc-600 dark:text-zinc-300 line-clamp-2 leading-relaxed">
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
        <div className="flex-1 flex flex-col h-full min-h-0 overflow-hidden">
          <ToolBuilder
            onToolActivated={() => {
              loadTools();
              setIsCreating(false);
            }}
            onRefreshConversations={onRefreshConversations}
          />
        </div>
      ) : (
        <div className="flex-1 flex flex-col h-full min-h-0 overflow-hidden">
          {/* Top Header rigidly docked at top */}
          <div className="h-16 border-b border-zinc-200 dark:border-zinc-800 px-8 flex items-center justify-between bg-white dark:bg-zinc-900 shrink-0 transition-colors">
            <div>
              <div className="flex items-center space-x-2.5">
                <h1 className="text-base font-bold font-mono text-zinc-900 dark:text-zinc-100">
                  {selectedTool ? selectedTool.name : 'Select Tool'}
                </h1>
                {selectedTool && (
                  <span className={`text-xs px-2.5 py-0.5 rounded-full font-medium border ${
                    selectedTool.is_builtin
                      ? 'bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 border-zinc-200 dark:border-zinc-700'
                      : 'bg-indigo-50 dark:bg-indigo-950/80 text-indigo-700 dark:text-indigo-300 border-indigo-200 dark:border-indigo-800/80'
                  }`}>
                    {selectedTool.is_builtin ? 'Built-in Tool' : 'Custom Tool'}
                  </span>
                )}
              </div>
              <p className="text-xs text-zinc-600 dark:text-zinc-300 mt-0.5">
                {selectedTool?.description || 'Select a tool to inspect its parameters and source code.'}
              </p>
            </div>

            {selectedTool && !selectedTool.is_builtin && (
              <button
                onClick={() => handleDelete(selectedTool.name)}
                className="px-3.5 py-2 rounded-xl text-xs font-semibold text-rose-700 dark:text-rose-300 bg-rose-50 dark:bg-rose-950/50 hover:bg-rose-100 dark:hover:bg-rose-900/50 border border-rose-200 dark:border-rose-800/80 transition-colors flex items-center gap-1.5 cursor-pointer"
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span>Delete Tool</span>
              </button>
            )}
          </div>

          {/* Scrollable Tool Details */}
          <div className="flex-1 overflow-y-auto p-8 w-full space-y-6 bg-zinc-50/50 dark:bg-zinc-950">
            {selectedTool ? (
              <div className="max-w-5xl mx-auto space-y-6 pb-12">
                {/* Parameters Schema */}
                <div className="space-y-2">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-zinc-800 dark:text-zinc-200">
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
                    <h3 className="text-xs font-bold uppercase tracking-wider text-zinc-700 dark:text-zinc-300 flex items-center gap-1.5">
                      <FileCode className="w-4 h-4 text-indigo-600 dark:text-indigo-400" /> Python Source Code (tool.py)
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
                    <h3 className="text-xs font-bold uppercase tracking-wider text-zinc-700 dark:text-zinc-300 flex items-center gap-1.5">
                      <ShieldCheck className="w-4 h-4 text-emerald-600 dark:text-emerald-400" /> Verification Test Suite (verify.py)
                    </h3>
                    <CodeBlock
                      code={selectedTool.verify_code}
                      language="python"
                    />
                  </div>
                )}
              </div>
            ) : (
              <div className="text-center py-24 text-zinc-400 dark:text-zinc-600 text-xs">
                Select a tool to inspect its specification and parameters.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
