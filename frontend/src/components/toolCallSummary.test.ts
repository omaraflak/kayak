import { describe, expect, it } from 'vitest';
import { summarizeToolCalls } from './toolCallSummary';

describe('summarizeToolCalls', () => {
  it('reports a clean group as completed', () => {
    const summary = summarizeToolCalls([{}, {}, {}]);
    expect(summary.label).toBe('3 tool calls executed');
    expect(summary.badgeLabel).toBe('completed');
    expect(summary.tone).toBe('clean');
  });

  it('counts the errors rather than condemning the whole group', () => {
    // The reported case: one step failed, the agent retried, the turn finished.
    // Seven of eight calls worked, so "failed" misdescribed the turn.
    const summary = summarizeToolCalls([
      {}, {}, { isError: true }, {}, {}, {}, {}, {},
    ]);
    expect(summary.errorCount).toBe(1);
    expect(summary.badgeLabel).toBe('1 error');
    expect(summary.tone).toBe('partial');
  });

  it('pluralises both counts', () => {
    expect(summarizeToolCalls([{}]).label).toBe('1 tool call executed');
    expect(summarizeToolCalls([{ isError: true }]).badgeLabel).toBe('1 error');
    expect(
      summarizeToolCalls([{ isError: true }, { isError: true }]).badgeLabel
    ).toBe('2 errors');
  });

  it('still reads as partial when every call errored', () => {
    // Deliberately not a special "all failed" state: the turn-level outcome is
    // reported elsewhere, and this line only describes the steps.
    const summary = summarizeToolCalls([{ isError: true }, { isError: true }]);
    expect(summary.tone).toBe('partial');
    expect(summary.badgeLabel).toBe('2 errors');
  });

  it('treats a missing flag as a success', () => {
    const summary = summarizeToolCalls([{ isError: undefined }, { isError: false }]);
    expect(summary.errorCount).toBe(0);
    expect(summary.tone).toBe('clean');
  });

  it('handles an empty group without inventing a status', () => {
    const summary = summarizeToolCalls([]);
    expect(summary.total).toBe(0);
    expect(summary.label).toBe('0 tool calls executed');
    expect(summary.tone).toBe('clean');
  });
});
