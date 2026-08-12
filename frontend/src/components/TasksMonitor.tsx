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
    <div className="flex-1 flex h-full min-h-0 bg-md-surface overflow-hidden transition-colors">
      {/* Task List Sidebar */}
      <div className="w-80 border-r border-md-outline-variant bg-md-surface-container-low flex flex-col shrink-0 transition-colors">
        <div className="p-4 border-b border-md-outline-variant flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Activity className="w-4 h-4 text-md-primary" />
            <h2 className="font-bold text-xs text-md-on-surface uppercase tracking-wider">Active Tasks ({tasks.length})</h2>
          </div>
          <button
            onClick={loadTasks}
            className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high transition-colors border border-md-outline-variant cursor-pointer"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {tasks.length === 0 ? (
            <div className="text-center py-16 text-md-on-surface-variant text-xs">
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
                      ? 'bg-md-primary-container text-md-on-primary-container border-md-primary shadow-xs ring-1 ring-md-primary/40 font-medium'
                      : 'bg-md-surface border-md-outline-variant text-md-on-surface hover:bg-md-surface-container hover:border-md-outline shadow-xs'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center space-x-1.5 min-w-0">
                      {t.task_type === 'subagent' ? (
                        <Bot className="w-3.5 h-3.5 text-md-primary shrink-0" />
                      ) : (
                        <Terminal className="w-3.5 h-3.5 text-md-primary shrink-0" />
                      )}
                      <span className="font-semibold text-xs truncate text-md-on-surface">{t.name}</span>
                    </div>

                    <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold uppercase ${
                      t.status === 'running'
                        ? 'bg-teal-100 dark:bg-teal-950/80 text-teal-800 dark:text-teal-200 border border-teal-300 dark:border-teal-800/80 animate-pulse'
                        : t.status === 'completed'
                        ? 'bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-200 border border-emerald-300 dark:border-emerald-800/80'
                        : 'bg-rose-100 dark:bg-rose-950/80 text-rose-800 dark:text-rose-200 border border-rose-300 dark:border-rose-800/80'
                    }`}>
                      {t.status}
                    </span>
                  </div>

                  <p className="text-[10px] text-md-on-surface-variant font-mono truncate">
                    {t.command || t.id}
                  </p>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Task Log Inspector */}
      <div className="flex-1 flex flex-col h-full min-h-0 overflow-hidden bg-md-surface">
        {selectedTask ? (
          <>
            {/* Header */}
            <div className="p-4 border-b border-md-outline-variant bg-md-surface-container-low flex items-center justify-between shrink-0 transition-colors">
              <div>
                <div className="flex items-center space-x-3">
                  <h1 className="text-sm font-bold text-md-on-surface font-mono">
                    {selectedTask.name}
                  </h1>
                  <span className={`text-xs px-2.5 py-0.5 rounded-full font-semibold uppercase ${
                    selectedTask.status === 'running'
                      ? 'bg-teal-100 dark:bg-teal-950/80 text-teal-800 dark:text-teal-200 border border-teal-300 dark:border-teal-800/80'
                      : selectedTask.status === 'completed'
                      ? 'bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-200 border border-emerald-300 dark:border-emerald-800/80'
                      : 'bg-rose-100 dark:bg-rose-950/80 text-rose-800 dark:text-rose-200 border border-rose-300 dark:border-rose-800/80'
                  }`}>
                    {selectedTask.status}
                  </span>
                </div>
                <div className="text-[11px] text-md-on-surface-variant font-mono mt-1 flex items-center gap-3">
                  <span>Task ID: {selectedTask.id}</span>
                  {selectedTask.pid && <span>PID: {selectedTask.pid}</span>}
                  {selectedTask.exit_code !== null && <span>Exit Code: {selectedTask.exit_code}</span>}
                </div>
              </div>

              {selectedTask.status === 'running' && (
                <button
                  onClick={() => handleStop(selectedTask.id)}
                  className="px-3.5 py-1.5 rounded-xl text-xs font-semibold text-md-error bg-md-error-container hover:opacity-90 border border-md-outline-variant flex items-center gap-1.5 transition-opacity cursor-pointer"
                >
                  <StopCircle className="w-4 h-4" />
                  <span>Terminate Process</span>
                </button>
              )}
            </div>

            {/* Console Output */}
            <div className="flex-1 p-6 bg-md-surface overflow-y-auto font-mono text-xs text-md-on-surface space-y-4 transition-colors">
              {selectedTask.command && (
                <div>
                  <div className="text-[10px] uppercase font-bold text-md-on-surface-variant mb-1">
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
                <div className="text-[10px] uppercase font-bold text-md-on-surface-variant mb-1">
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
                  <div className="text-[10px] uppercase font-bold text-md-error mb-1">
                    Standard Error
                  </div>
                  <CodeBlock
                    code={selectedTask.stderr}
                    language="bash"
                    showHeader={false}
                  />
                </div>
              )}
            </div>

            {/* Stdin Interactive Bar if running */}
            {selectedTask.status === 'running' && selectedTask.task_type === 'shell_command' && (
              <div className="p-3 border-t border-md-outline-variant bg-md-surface-container-low transition-colors">
                <form onSubmit={handleSendInput} className="flex items-center space-x-2">
                  <input
                    type="text"
                    value={inputVal}
                    onChange={(e) => setInputVal(e.target.value)}
                    placeholder="Send input to stdin..."
                    className="flex-1 bg-md-surface-container-lowest border border-md-outline-variant rounded-xl px-3.5 py-2 text-xs font-mono text-md-on-surface placeholder:text-md-on-surface-variant/70 focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary transition-colors"
                  />
                  <button
                    type="submit"
                    className="px-4 py-2 rounded-xl text-xs font-semibold bg-md-primary hover:opacity-90 text-md-on-primary flex items-center gap-1.5 transition-opacity shadow-xs cursor-pointer"
                  >
                    <span>Send</span>
                    <Send className="w-3.5 h-3.5" />
                  </button>
                </form>
              </div>
            )}
          </>
        ) : (
          <div className="text-center py-24 text-md-on-surface-variant text-sm">
            Select a task to inspect logs and interact with stdin.
          </div>
        )}
      </div>
    </div>
  );
};
