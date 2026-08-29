import { describe, expect, it } from 'vitest';
import { coalesceQueued, QueuedMessage } from './messageQueue';

function queued(...texts: string[]): QueuedMessage[] {
  return texts.map((text, index) => ({ id: String(index), text }));
}

describe('coalesceQueued', () => {
  it('sends a single message as itself', () => {
    // A one-item bullet list is formatting for its own sake.
    expect(coalesceQueued(queued('what about caching?'))).toBe('what about caching?');
  });

  it('makes a list of several, so they read as separate points', () => {
    expect(coalesceQueued(queued('add tests', 'and a README'))).toBe(
      '- add tests\n- and a README'
    );
  });

  it('keeps a multi-line message inside its own bullet', () => {
    // Otherwise the second line starts at column zero and reads as a sibling.
    expect(coalesceQueued(queued('first\nstill first', 'second'))).toBe(
      '- first\n  still first\n- second'
    );
  });

  it('ignores entries that are only whitespace', () => {
    expect(coalesceQueued(queued('real', '   '))).toBe('real');
  });

  it('has nothing to send when everything was removed', () => {
    expect(coalesceQueued([])).toBe('');
    expect(coalesceQueued(queued('  '))).toBe('');
  });
});
