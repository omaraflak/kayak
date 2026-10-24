/**
 * Headline for a turn's collapsed tool calls.
 *
 * The summary states what happened; it does not pass a verdict on the turn. A
 * tool call erroring is ordinary mid-turn behaviour -- the agent probes a path
 * that does not exist, hits a timeout, retries with different arguments -- and
 * labelling the whole group "failed" because one step errored made successful
 * work read as a failure. Whether the *turn* failed is a separate fact, already
 * surfaced by the error banner and the iteration-ceiling notice.
 */

export type ToolCallTone = 'clean' | 'partial';

export interface ToolCallSummary {
  total: number;
  errorCount: number;
  /** Describes the group, e.g. "8 steps". */
  label: string;
  /** Short status chip, e.g. "done" or "1 didn't work". */
  badgeLabel: string;
  tone: ToolCallTone;
}

export function summarizeToolCalls(
  toolCalls: ReadonlyArray<{ isError?: boolean }>
): ToolCallSummary {
  const total = toolCalls.length;
  const errorCount = toolCalls.filter((call) => call.isError).length;

  return {
    total,
    errorCount,
    label: `${total} ${total === 1 ? 'step' : 'steps'}`,
    badgeLabel: errorCount === 0 ? 'done' : `${errorCount} didn't work`,
    tone: errorCount === 0 ? 'clean' : 'partial',
  };
}
