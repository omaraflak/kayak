/**
 * Background tasks belonging to one conversation's container.
 *
 * A task is a process the agent started and left running -- a dev server, a build, a
 * delegated sub-agent. It is scoped to a container, not to the platform, so this
 * describes one conversation's tasks for the container drawer that shows them.
 */

import { BackgroundTask } from '../types';

/** How often the drawer re-reads the task list while it is on screen. */
export const TASK_POLL_INTERVAL_MS = 3000;

export function isRunning(task: BackgroundTask): boolean {
  return task.status === 'running';
}

/** Number shown on the container button. Zero means the badge is not rendered. */
export function runningTaskCount(tasks: ReadonlyArray<BackgroundTask>): number {
  return tasks.filter(isRunning).length;
}

/**
 * Running tasks first, then the rest newest-first.
 *
 * What is still running is what someone opens this list to find; a finished task is
 * history. Sorting purely by time buried a live process under everything that had
 * completed since it started.
 */
export function orderTasks(tasks: ReadonlyArray<BackgroundTask>): BackgroundTask[] {
  return [...tasks].sort((left, right) => {
    if (isRunning(left) !== isRunning(right)) return isRunning(left) ? -1 : 1;
    return right.created_at.localeCompare(left.created_at);
  });
}

/** Whether this task's live output can be typed into. */
export function acceptsInput(task: BackgroundTask): boolean {
  // Only a shell process has a stdin. A sub-agent is driven by the agent that
  // spawned it, and there is no channel for anyone else to send it instructions.
  return task.task_type === 'shell_command' && isRunning(task);
}

/** The conversation a sub-agent task's transcript lives in, if it has one. */
export function subagentConversationId(task: BackgroundTask): string | null {
  if (task.task_type !== 'subagent') return null;
  return task.subagent_conversation_id || null;
}
