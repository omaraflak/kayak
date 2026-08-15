import React, { useEffect, useState } from 'react';
import { BackgroundTask } from '../../types';
import { api, errorMessage } from '../../api/client';
import { CodeBlock } from '../../ui/CodeBlock';
import { acceptsInput, orderTasks, subagentConversationId } from './conversationTasks';
import {
  ArrowLeft,
  Bot,
  ExternalLink,
  Send,
  StopCircle,
  Terminal as TerminalIcon,
} from 'lucide-react';

/**
 * Processes running inside one conversation's container: background shell commands
 * and delegated sub-agents.
 *
 * This used to be a platform-wide page, which mixed every conversation's processes
 * together even though a task has only ever belonged to the container it runs in.
 */

interface ContainerTasksProps {
  tasks: BackgroundTask[];
  /** Re-reads the list after an action that changes it. */
  onRefresh: () => void;
  /** Opens a sub-agent's transcript. */
  onOpenConversation: (conversationId: string) => void;
}

export const ContainerTasks: React.FC<ContainerTasksProps> = ({
  tasks,
  onRefresh,
  onOpenConversation,
}) => {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const ordered = orderTasks(tasks);
  const selected = ordered.find((task) => task.id === selectedId) || null;

  // A task can finish and be replaced while its detail view is open; dropping the
  // selection when it disappears avoids a blank pane with no way back.
  useEffect(() => {
    if (selectedId && !tasks.some((task) => task.id === selectedId)) {
      setSelectedId(null);
    }
  }, [tasks, selectedId]);

  if (selected) {
    return (
      <TaskDetail
        task={selected}
        onBack={() => setSelectedId(null)}
        onRefresh={onRefresh}
        onOpenConversation={onOpenConversation}
      />
    );
  }

  return (
    <div className="flex-1 overflow-y-auto py-1 min-h-0">
      {ordered.length === 0 ? (
        <p className="px-3 py-6 text-center text-[11px] text-md-on-surface-variant leading-relaxed">
          Nothing running. Long commands the agent starts in the background, and
          sub-agents it delegates to, appear here.
        </p>
      ) : (
        ordered.map((task) => (
          <button
            key={task.id}
            type="button"
            onClick={() => setSelectedId(task.id)}
            className="w-full px-3 py-2 flex items-start gap-2 text-left hover:bg-md-surface-container transition-colors cursor-pointer"
          >
            {task.task_type === 'subagent' ? (
              <Bot className="w-4 h-4 text-md-primary shrink-0 mt-0.5" />
            ) : (
              <TerminalIcon className="w-4 h-4 text-md-on-surface-variant shrink-0 mt-0.5" />
            )}
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-1.5">
                <span className="text-xs font-semibold text-md-on-surface truncate">
                  {task.name}
                </span>
                <StatusChip status={task.status} />
              </span>
              <span className="block text-[10px] text-md-on-surface-variant font-mono truncate mt-0.5">
                {task.command || task.id}
              </span>
            </span>
          </button>
        ))
      )}
    </div>
  );
};

const TaskDetail: React.FC<{
  task: BackgroundTask;
  onBack: () => void;
  onRefresh: () => void;
  onOpenConversation: (conversationId: string) => void;
}> = ({ task, onBack, onRefresh, onOpenConversation }) => {
  const [inputValue, setInputValue] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);
  const childConversationId = subagentConversationId(task);

  const handleStop = async () => {
    try {
      await api.stopTask(task.id);
      onRefresh();
    } catch (err) {
      setActionError(errorMessage(err));
    }
  };

  const handleSend = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!inputValue.trim()) return;
    try {
      await api.sendTaskInput(task.id, inputValue);
      setInputValue('');
      onRefresh();
    } catch (err) {
      setActionError(errorMessage(err));
    }
  };

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="px-3 py-2 border-b border-md-outline-variant flex items-center gap-2 shrink-0">
        <button
          type="button"
          onClick={onBack}
          className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container transition-colors cursor-pointer"
          title="Back to tasks"
        >
          <ArrowLeft className="w-4 h-4" />
        </button>
        <span className="text-xs font-semibold text-md-on-surface truncate flex-1 min-w-0">
          {task.name}
        </span>
        <StatusChip status={task.status} />
        {task.status === 'running' && (
          <button
            type="button"
            onClick={handleStop}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-semibold text-md-error hover:bg-md-error-container border border-md-outline-variant transition-colors cursor-pointer shrink-0"
            title="Terminate this task"
          >
            <StopCircle className="w-3.5 h-3.5" /> Stop
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto min-h-0 p-3 space-y-3">
        {actionError && (
          <p className="text-[11px] text-md-error leading-relaxed">{actionError}</p>
        )}

        {childConversationId && (
          <button
            type="button"
            onClick={() => onOpenConversation(childConversationId)}
            className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold bg-md-primary-container text-md-on-primary-container border border-md-primary/40 hover:opacity-90 transition-opacity cursor-pointer"
          >
            <ExternalLink className="w-3.5 h-3.5" />
            Open the conversation with this sub-agent
          </button>
        )}

        <div className="text-[10px] text-md-on-surface-variant font-mono flex flex-wrap gap-x-3 gap-y-1">
          {task.pid != null && <span>PID {task.pid}</span>}
          {task.exit_code != null && <span>Exit code {task.exit_code}</span>}
        </div>

        {task.command && (
          <div>
            <div className="text-[10px] uppercase font-bold text-md-on-surface-variant mb-1">
              {task.task_type === 'subagent' ? 'Prompt' : 'Command'}
            </div>
            <CodeBlock
              code={task.task_type === 'subagent' ? task.command : `$ ${task.command}`}
              language={task.task_type === 'subagent' ? 'markdown' : 'bash'}
              showHeader={false}
            />
          </div>
        )}

        <div>
          <div className="text-[10px] uppercase font-bold text-md-on-surface-variant mb-1">
            {task.task_type === 'subagent' ? 'Result' : 'Output'}
          </div>
          <CodeBlock
            code={task.stdout || (task.status === 'running' ? 'Waiting for output…' : 'No output.')}
            language="bash"
            showHeader={false}
          />
        </div>

        {task.stderr && (
          <div>
            <div className="text-[10px] uppercase font-bold text-md-error mb-1">
              Errors
            </div>
            <CodeBlock code={task.stderr} language="bash" showHeader={false} />
          </div>
        )}
      </div>

      {acceptsInput(task) && (
        <form
          onSubmit={handleSend}
          className="p-2 border-t border-md-outline-variant flex items-center gap-2 shrink-0 bg-md-surface-container-low"
        >
          <input
            type="text"
            value={inputValue}
            onChange={(event) => setInputValue(event.target.value)}
            placeholder="Send input to this process…"
            className="flex-1 min-w-0 bg-md-surface border border-md-outline-variant rounded-lg px-3 py-1.5 text-xs font-mono text-md-on-surface focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary transition-colors"
          />
          <button
            type="submit"
            className="p-2 rounded-lg bg-md-primary text-md-on-primary hover:opacity-90 transition-opacity cursor-pointer shrink-0"
            title="Send"
          >
            <Send className="w-3.5 h-3.5" />
          </button>
        </form>
      )}
    </div>
  );
};

const StatusChip: React.FC<{ status: BackgroundTask['status'] }> = ({ status }) => (
  <span
    className={`text-[10px] px-1.5 py-0.5 rounded-full font-bold uppercase border shrink-0 ${
      status === 'running'
        ? 'bg-teal-100 dark:bg-teal-950/80 text-teal-800 dark:text-teal-200 border-teal-300 dark:border-teal-800/80'
        : status === 'completed'
        ? 'bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-200 border-emerald-300 dark:border-emerald-800/80'
        : 'bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant'
    }`}
  >
    {status}
  </span>
);
