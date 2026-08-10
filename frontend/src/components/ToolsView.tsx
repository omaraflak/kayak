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
    <div className="flex-1 flex h-screen bg-zinc-50 overflow-hidden">
      {/* Tools List Sidebar */}
      <div className="w-80 border-r border-zinc-200 bg-white flex flex-col shrink-0">
        <div className="p-4 border-b border-zinc-200 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Wrench className="w-4 h-4 text-zinc-700" />
            <h2 className="font-bold text-xs text-zinc-900 uppercase tracking-wider">
              Tools ({tools.length})
            </h2>
          </div>

          <div className="flex items-center space-x-1.5">
            <button
              onClick={handleReload}
              disabled={isReloading}
              className="p-1.5 rounded-lg text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100 transition-colors"
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
            <div className="text-center py-10 px-4 text-zinc-400 text-xs">
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
                      ? 'bg-indigo-50/80 border border-indigo-600 text-zinc-950 font-medium shadow-xs ring-1 ring-indigo-500/20'
                      : 'bg-white border-zinc-200 text-zinc-700 hover:bg-zinc-50 hover:border-zinc-300 shadow-xs'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-semibold text-xs font-mono text-zinc-900">{tool.name}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono border ${
                      tool.is_builtin
                        ? 'bg-zinc-100 text-zinc-600 border-zinc-200'
                        : 'bg-indigo-100 text-indigo-700 border-indigo-200 font-medium'
                    }`}>
                      {tool.is_builtin ? 'builtin' : 'custom'}
                    </span>
                  </div>
                  <p className="text-[11px] text-zinc-500 line-clamp-2 leading-relaxed">
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
        <div className="flex-1 flex flex-col h-screen overflow-hidden">
          <ToolBuilder
            onToolActivated={() => {
              loadTools();
              setIsCreating(false);
            }}
            onRefreshConversations={onRefreshConversations}
          />
        </div>
      ) : (
        <div className="flex-1 flex flex-col h-screen overflow-hidden">
          {/* Top Header rigidly docked at top */}
          <div className="h-16 border-b border-zinc-200 px-8 flex items-center justify-between bg-white shrink-0">
            <div>
              <div className="flex items-center space-x-2.5">
                <h1 className="text-base font-bold font-mono text-zinc-900">
                  {selectedTool ? selectedTool.name : 'Select Tool'}
                </h1>
                {selectedTool && (
                  <span className={`text-xs px-2.5 py-0.5 rounded-full font-medium border ${
                    selectedTool.is_builtin
                      ? 'bg-zinc-100 text-zinc-600 border-zinc-200'
                      : 'bg-indigo-50 text-indigo-700 border-indigo-200'
                  }`}>
                    {selectedTool.is_builtin ? 'Built-in Tool' : 'Custom Tool'}
                  </span>
                )}
              </div>
              <p className="text-xs text-zinc-500 mt-0.5">
                {selectedTool?.description || 'Select a tool to inspect its parameters and source code.'}
              </p>
            </div>

            {selectedTool && !selectedTool.is_builtin && (
              <button
                onClick={() => handleDelete(selectedTool.name)}
                className="px-3.5 py-2 rounded-xl text-xs font-medium text-rose-600 hover:bg-rose-50 border border-rose-200 transition-colors flex items-center gap-1.5"
              >
                <Trash2 className="w-3.5 h-3.5" />
                <span>Delete Tool</span>
              </button>
            )}
          </div>

          {/* Scrollable Tool Details */}
          <div className="flex-1 overflow-y-auto p-8 w-full space-y-6">
            {selectedTool ? (
              <div className="max-w-5xl mx-auto space-y-6 pb-12">
                {/* Parameters Schema */}
                <div className="space-y-2">
                  <h3 className="text-xs font-bold uppercase tracking-wider text-zinc-700">
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
                    <h3 className="text-xs font-bold uppercase tracking-wider text-zinc-700 flex items-center gap-1.5">
                      <FileCode className="w-4 h-4 text-indigo-600" /> Python Source Code (tool.py)
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
                    <h3 className="text-xs font-bold uppercase tracking-wider text-zinc-700 flex items-center gap-1.5">
                      <ShieldCheck className="w-4 h-4 text-emerald-600" /> Verification Test Suite (verify.py)
                    </h3>
                    <CodeBlock
                      code={selectedTool.verify_code}
                      language="python"
                    />
                  </div>
                )}
              </div>
            ) : (
              <div className="text-center py-24 text-zinc-400 text-xs">
                Select a tool to inspect its specification and parameters.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
