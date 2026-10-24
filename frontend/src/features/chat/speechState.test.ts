import { describe, expect, it } from 'vitest';
import { MessageSpeech } from '../../types';
import { forgetMissing, withoutPending } from './speechState';

function speech(message_id: string, state: MessageSpeech['state']): MessageSpeech {
  return { message_id, state, chunks_done: 0, chunks_total: 0 };
}

describe('forgetMissing', () => {
  it('drops what the server no longer knows about', () => {
    // After a restart the server forgets work in flight; the page used to poll
    // for it for as long as it was open.
    const before = { a: speech('a', 'pending'), b: speech('b', 'ready') };

    const after = forgetMissing(before, ['a', 'b'], [speech('b', 'ready')]);

    expect(after).toEqual({ b: speech('b', 'ready') });
  });

  it('leaves messages it did not ask about alone', () => {
    const before = { a: speech('a', 'pending'), c: speech('c', 'ready') };

    const after = forgetMissing(before, ['a'], [speech('a', 'ready')]);

    expect(after).toEqual({ a: speech('a', 'ready'), c: speech('c', 'ready') });
  });
});

describe('withoutPending', () => {
  it('drops work nothing can finish', () => {
    const before = { a: speech('a', 'pending'), b: speech('b', 'ready'), c: speech('c', 'failed') };

    expect(withoutPending(before)).toEqual({ b: speech('b', 'ready'), c: speech('c', 'failed') });
  });

  it('keeps the same record when nothing is pending', () => {
    const before = { b: speech('b', 'ready') };

    expect(withoutPending(before)).toBe(before);
  });
});
