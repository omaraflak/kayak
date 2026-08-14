import { describe, expect, it } from 'vitest';
import {
  ActiveToolCalls,
  applyToolDelta,
  applyToolExecuting,
  applyToolResult,
} from './toolCallStream';

describe('tool call stream transitions', () => {
  it('accumulates argument fragments across deltas', () => {
    let state: ActiveToolCalls = {};
    state = applyToolDelta(state, { id: 'c1', name: 'run_command', arguments: '{"comm' });
    state = applyToolDelta(state, { id: 'c1', arguments: 'and": "ls"}' });
    expect(state.c1).toEqual({ name: 'run_command', args: '{"command": "ls"}' });
  });

  it('follows the normal executing → result sequence', () => {
    let state: ActiveToolCalls = {};
    state = applyToolExecuting(state, { id: 'c1', name: 'run_command', arguments: '{"command": "ls"}' });
    state = applyToolResult(state, { id: 'c1', name: 'run_command', output: 'file.txt', is_error: false });
    expect(state.c1).toEqual({
      name: 'run_command',
      args: '{"command": "ls"}',
      output: 'file.txt',
      isError: false,
    });
  });

  it('survives a result with no prior events, as the reconnect replay sends', () => {
    // The reported crash: a tab connecting mid-turn receives a bare
    // tool_call_result for an already-finished call, and rendering the entry
    // with undefined args blanked the page.
    const state = applyToolResult({}, {
      id: 'c1',
      name: 'write_file',
      arguments: '{"path": "a.txt"}',
      output: 'ok',
      is_error: false,
    });
    expect(state.c1.args).toBe('{"path": "a.txt"}');
    expect(state.c1.name).toBe('write_file');
  });

  it('never leaves args undefined even when the replay omits arguments too', () => {
    const state = applyToolResult({}, { id: 'c1', name: 'tool', output: 'ok' });
    expect(state.c1.args).toBe('');
  });

  it('keeps the streamed arguments over the replayed copy when both exist', () => {
    let state: ActiveToolCalls = {};
    state = applyToolExecuting(state, { id: 'c1', name: 'tool', arguments: 'streamed' });
    state = applyToolResult(state, { id: 'c1', name: 'tool', arguments: 'replayed', output: 'ok' });
    expect(state.c1.args).toBe('streamed');
  });

  it('does not disturb other in-flight calls', () => {
    let state: ActiveToolCalls = {};
    state = applyToolExecuting(state, { id: 'c1', name: 'a', arguments: '{}' });
    state = applyToolResult(state, { id: 'c2', name: 'b', arguments: '{}', output: 'done' });
    expect(Object.keys(state)).toEqual(['c1', 'c2']);
    expect(state.c1.output).toBeUndefined();
  });
});
