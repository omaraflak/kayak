import React, { useState, useEffect } from 'react';
import { BackgroundTask } from '../types';
import { api } from '../api/client';
import { CodeBlock } from './CodeBlock';
import { 
  Activity, 
  Terminal, 
  Bot, 
  StopCircle, 
  Send, 
  RefreshCw
} from 'lucide-react';

export const TasksMonitor: React.FC = () => {
  const [tasks, setTasks] = useState<BackgroundTask[]>([]);
  const [selectedTask, setSelectedTask] = useState<BackgroundTask | null>(null);
  const [inputVal, setInputVal] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const loadTasks = async () => {
    setIsLoading(true);
    try {
      const data = await api.listTasks();
      setTasks(data);
      if (data.length > 0 && !selectedTask) {
        setSelectedTask(data[0]);
      } else if (selectedTask) {
        const updated = data.find((t) => t.id === selectedTask.id);
        if (updated) setSelectedTask(updated);
      }
    } catch (e) {
      console.error('Failed to load tasks:', e);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadTasks();
    const interval = setInterval(loadTasks, 3000);
    return () => clearInterval(interval);
  }, [selectedTask?.id]);

  const handleStop = async (taskId: string) => {
    try {
      await api.stopTask(taskId);
      loadTasks();
    } catch (e) {
      console.error('Failed to stop task:', e);
    }
  };

  const handleSendInput = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedTask || !inputVal.trim()) return;

    try {
      await api.sendTaskInput(selectedTask.id, inputVal);
      setInputVal('');
      loadTasks();
    } catch (e) {
      console.error('Failed to send task input:', e);
    }
  };

  return (
    <div className="flex-1 flex h-full min-h-0 bg-zinc-50 overflow-hidden">
      {/* Task List Sidebar */}
      <div className="w-80 border-r border-zinc-200 bg-white flex flex-col shrink-0">
        <div className="p-4 border-b border-zinc-200 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Activity className="w-4 h-4 text-zinc-700" />
            <h2 className="font-bold text-xs text-zinc-900 uppercase tracking-wider">Active Tasks ({tasks.length})</h2>
          </div>
          <button
            onClick={loadTasks}
            className="p-1.5 rounded-lg text-zinc-600 hover:text-zinc-900 hover:bg-zinc-100 transition-colors border border-zinc-200"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {tasks.length === 0 ? (
            <div className="text-center py-16 text-zinc-400 text-xs">
              No background tasks or subagents currently running.
            </div>
          ) : (
            tasks.map((t) => {
              const isSelected = selectedTask?.id === t.id;
              return (
                <div
                  key={t.id}
                  onClick={() => setSelectedTask(t)}
                  className={`p-3 rounded-xl cursor-pointer transition-all border ${
                    isSelected
                      ? 'bg-zinc-100 border-zinc-300 text-zinc-900 font-medium shadow-xs'
                      : 'bg-transparent border-transparent text-zinc-600 hover:bg-zinc-50 hover:text-zinc-900'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center space-x-1.5 min-w-0">
                      {t.task_type === 'subagent' ? (
                        <Bot className="w-3.5 h-3.5 text-zinc-700 shrink-0" />
                      ) : (
                        <Terminal className="w-3.5 h-3.5 text-zinc-700 shrink-0" />
                      )}
                      <span className="font-semibold text-xs truncate text-zinc-900">{t.name}</span>
                    </div>

                    <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold uppercase ${
                      t.status === 'running'
                        ? 'bg-teal-50 text-teal-700 border border-teal-200 animate-pulse'
                        : t.status === 'completed'
                        ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                        : 'bg-rose-50 text-rose-700 border border-rose-200'
                    }`}>
                      {t.status}
                    </span>
                  </div>

                  <p className="text-[10px] text-zinc-400 font-mono truncate">
                    {t.command || t.id}
                  </p>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Task Log Inspector */}
      <div className="flex-1 flex flex-col h-full min-h-0 overflow-hidden">
        {selectedTask ? (
          <>
            {/* Header */}
            <div className="p-4 border-b border-zinc-200 bg-white flex items-center justify-between shrink-0">
              <div>
                <div className="flex items-center space-x-3">
                  <h1 className="text-sm font-bold text-zinc-900 font-mono">
                    {selectedTask.name}
                  </h1>
                  <span className={`text-xs px-2.5 py-0.5 rounded-full font-semibold uppercase ${
                    selectedTask.status === 'running'
                      ? 'bg-teal-50 text-teal-700 border border-teal-200'
                      : selectedTask.status === 'completed'
                      ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                      : 'bg-rose-50 text-rose-700 border border-rose-200'
                  }`}>
                    {selectedTask.status}
                  </span>
                </div>
                <div className="text-[11px] text-zinc-400 font-mono mt-1 flex items-center gap-3">
                  <span>Task ID: {selectedTask.id}</span>
                  {selectedTask.pid && <span>PID: {selectedTask.pid}</span>}
                  {selectedTask.exit_code !== null && <span>Exit Code: {selectedTask.exit_code}</span>}
                </div>
              </div>

              {selectedTask.status === 'running' && (
                <button
                  onClick={() => handleStop(selectedTask.id)}
                  className="px-3.5 py-1.5 rounded-lg text-xs font-semibold text-rose-700 hover:bg-rose-50 border border-rose-200 flex items-center gap-1.5 transition-colors"
                >
                  <StopCircle className="w-4 h-4 text-rose-600" />
                  <span>Terminate Process</span>
                </button>
              )}
            </div>

            {/* Console Output */}
            <div className="flex-1 p-6 bg-zinc-50 overflow-y-auto font-mono text-xs text-zinc-800 space-y-4">
              {selectedTask.command && (
                <div>
                  <div className="text-[10px] uppercase font-bold text-zinc-400 mb-1">
                    Command Invocation
                  </div>
                  <CodeBlock
                    code={`$ ${selectedTask.command}`}
                    language="bash"
                    showHeader={false}
                  />
                </div>
              )}

              <div>
                <div className="text-[10px] uppercase font-bold text-zinc-400 mb-1">
                  Standard Output (Live Log Stream)
                </div>
                <CodeBlock
                  code={selectedTask.stdout || 'Waiting for output...'}
                  language="bash"
                  showHeader={false}
                />
              </div>

              {selectedTask.stderr && (
                <div>
                  <div className="text-[10px] uppercase font-bold text-rose-500 mb-1">
                    Standard Error
                  </div>
                  <CodeBlock
                    code={selectedTask.stderr}
                    language="bash"
                    className="border-rose-200"
                    showHeader={false}
                  />
                </div>
              )}
            </div>

            {/* Stdin Interactive Bar if running */}
            {selectedTask.status === 'running' && selectedTask.task_type === 'shell_command' && (
              <div className="p-3 border-t border-zinc-200 bg-white">
                <form onSubmit={handleSendInput} className="flex items-center space-x-2">
                  <input
                    type="text"
                    value={inputVal}
                    onChange={(e) => setInputVal(e.target.value)}
                    placeholder="Send input to stdin..."
                    className="flex-1 bg-zinc-50 border border-zinc-300 rounded-lg px-3.5 py-2 text-xs font-mono text-zinc-900 focus:bg-white focus:outline-none focus:border-zinc-900 focus:ring-1 focus:ring-zinc-900"
                  />
                  <button
                    type="submit"
                    className="px-4 py-2 rounded-lg text-xs font-semibold bg-zinc-900 hover:bg-zinc-800 text-white flex items-center gap-1.5 transition-colors shadow-xs"
                  >
                    <span>Send</span>
                    <Send className="w-3.5 h-3.5" />
                  </button>
                </form>
              </div>
            )}
          </>
        ) : (
          <div className="text-center py-24 text-zinc-400 text-sm">
            Select a task to inspect logs and interact with stdin.
          </div>
        )}
      </div>
    </div>
  );
};
