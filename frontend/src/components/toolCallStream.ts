/**
 * State transitions for tool calls streamed over SSE during a live turn.
 *
 * The order of events cannot be assumed. A client that connects mid-turn is
 * caught up from the server's replay buffer, which sends a single
 * `tool_call_result` event for calls that already finished -- there is no
 * preceding delta or executing event. Building the entry by spreading a
 * nonexistent previous one left `args` undefined, and rendering that crashed
 * the whole page (the reported blank-screen-while-the-agent-works bug).
 * Every transition here therefore produces a complete entry on its own.
 */

export interface ActiveToolCall {
  name: string;
  args: string;
  output?: string;
  isError?: boolean;
}

export type ActiveToolCalls = Record<string, ActiveToolCall>;

export function applyToolDelta(
  prev: ActiveToolCalls,
  delta: { id: string; name?: string; arguments?: string }
): ActiveToolCalls {
  const existing = prev[delta.id] || { name: delta.name || '', args: '' };
  return {
    ...prev,
    [delta.id]: {
      ...existing,
      name: delta.name || existing.name,
      args: existing.args + (delta.arguments || ''),
    },
  };
}

export function applyToolExecuting(
  prev: ActiveToolCalls,
  data: { id: string; name: string; arguments?: string }
): ActiveToolCalls {
  return {
    ...prev,
    [data.id]: {
      name: data.name,
      args: data.arguments ?? '',
    },
  };
}

export function applyToolResult(
  prev: ActiveToolCalls,
  data: { id: string; name: string; arguments?: string; output: string; is_error?: boolean }
): ActiveToolCalls {
  const existing = prev[data.id];
  return {
    ...prev,
    [data.id]: {
      name: data.name || existing?.name || '',
      // Replay events carry the arguments precisely because the executing event
      // this result answers may never have reached this client.
      args: existing?.args ?? data.arguments ?? '',
      output: data.output,
      isError: Boolean(data.is_error),
    },
  };
}
