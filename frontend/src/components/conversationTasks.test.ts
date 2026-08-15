import { describe, expect, it } from 'vitest';
import { BackgroundTask } from '../types';
import {
  acceptsInput,
  orderTasks,
  runningTaskCount,
  subagentConversationId,
} from './conversationTasks';

function task(overrides: Partial<BackgroundTask>): BackgroundTask {
  return {
    id: 'task-1',
    conversation_id: 'conv-1',
    task_type: 'shell_command',
    name: 'task',
    status: 'running',
    stdout: '',
    stderr: '',
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-01T00:00:00',
    ...overrides,
  };
}

describe('runningTaskCount', () => {
  it('counts only what is still running', () => {
    const count = runningTaskCount([
      task({ id: 'a', status: 'running' }),
      task({ id: 'b', status: 'completed' }),
      task({ id: 'c', status: 'failed' }),
      task({ id: 'd', status: 'running' }),
    ]);

    expect(count).toBe(2);
  });

  it('is zero for an empty list, so no badge is drawn', () => {
    expect(runningTaskCount([])).toBe(0);
  });
});

describe('orderTasks', () => {
  it('puts running tasks above finished ones', () => {
    // The finished task is newer; it must still sort below the live process.
    const ordered = orderTasks([
      task({ id: 'done', status: 'completed', created_at: '2026-01-02T00:00:00' }),
      task({ id: 'live', status: 'running', created_at: '2026-01-01T00:00:00' }),
    ]);

    expect(ordered.map((entry) => entry.id)).toEqual(['live', 'done']);
  });

  it('orders within each group newest first', () => {
    const ordered = orderTasks([
      task({ id: 'old', status: 'running', created_at: '2026-01-01T00:00:00' }),
      task({ id: 'new', status: 'running', created_at: '2026-01-03T00:00:00' }),
      task({ id: 'mid', status: 'running', created_at: '2026-01-02T00:00:00' }),
    ]);

    expect(ordered.map((entry) => entry.id)).toEqual(['new', 'mid', 'old']);
  });

  it('does not mutate the list it was given', () => {
    const input = [
      task({ id: 'done', status: 'completed' }),
      task({ id: 'live', status: 'running' }),
    ];

    orderTasks(input);

    expect(input.map((entry) => entry.id)).toEqual(['done', 'live']);
  });
});

describe('acceptsInput', () => {
  it('allows stdin on a running shell process', () => {
    expect(acceptsInput(task({ task_type: 'shell_command', status: 'running' }))).toBe(true);
  });

  it('refuses a finished process', () => {
    expect(acceptsInput(task({ task_type: 'shell_command', status: 'completed' }))).toBe(false);
  });

  it('refuses a sub-agent', () => {
    // A sub-agent takes its instructions from the agent that spawned it; there is no
    // channel for the user to steer it mid-run.
    expect(acceptsInput(task({ task_type: 'subagent', status: 'running' }))).toBe(false);
  });
});

describe('subagentConversationId', () => {
  it('returns the delegated conversation', () => {
    const id = subagentConversationId(
      task({ task_type: 'subagent', subagent_conversation_id: 'child-1' })
    );

    expect(id).toBe('child-1');
  });

  it('returns null for a shell task', () => {
    expect(subagentConversationId(task({ task_type: 'shell_command' }))).toBeNull();
  });

  it('returns null for a sub-agent task recorded before the link existed', () => {
    // Older rows have no conversation id; the row must still render, just without
    // an "open transcript" button.
    expect(subagentConversationId(task({ task_type: 'subagent' }))).toBeNull();
  });
});
