import { describe, expect, it } from 'vitest';
import { applyActivityEvent } from './conversationActivity';

const setOf = (...ids: string[]) => new Set(ids);

describe('applyActivityEvent', () => {
  it('adopts the snapshot that opens every connection', () => {
    const next = applyActivityEvent(setOf(), { type: 'snapshot', running: ['a', 'b'] });
    expect([...next].sort()).toEqual(['a', 'b']);
  });

  it('lets a snapshot correct state missed while disconnected', () => {
    // 'a' finished during the outage; the snapshot is authoritative.
    const next = applyActivityEvent(setOf('a'), { type: 'snapshot', running: ['b'] });
    expect([...next]).toEqual(['b']);
  });

  it('marks a conversation as working and back to idle', () => {
    let state = applyActivityEvent(setOf(), {
      type: 'conversation_activity',
      conversation_id: 'a',
      running: true,
    });
    expect(state.has('a')).toBe(true);

    state = applyActivityEvent(state, {
      type: 'conversation_activity',
      conversation_id: 'a',
      running: false,
    });
    expect(state.has('a')).toBe(false);
  });

  it('tracks several conversations at once', () => {
    let state = applyActivityEvent(setOf('a'), {
      type: 'conversation_activity',
      conversation_id: 'b',
      running: true,
    });
    expect([...state].sort()).toEqual(['a', 'b']);

    state = applyActivityEvent(state, {
      type: 'conversation_activity',
      conversation_id: 'a',
      running: false,
    });
    expect([...state]).toEqual(['b']);
  });

  it('keeps the same instance when nothing changes, so idle pings do not re-render', () => {
    const state = setOf('a');
    expect(applyActivityEvent(state, { type: 'ping' })).toBe(state);
    expect(
      applyActivityEvent(state, { type: 'snapshot', running: ['a'] })
    ).toBe(state);
    expect(
      applyActivityEvent(state, {
        type: 'conversation_activity',
        conversation_id: 'a',
        running: true,
      })
    ).toBe(state);
    expect(
      applyActivityEvent(state, {
        type: 'conversation_activity',
        conversation_id: 'other',
        running: false,
      })
    ).toBe(state);
  });

  it('survives malformed frames rather than losing the whole stream', () => {
    const state = setOf('a');
    expect(applyActivityEvent(state, null)).toBe(state);
    expect(applyActivityEvent(state, undefined)).toBe(state);
    expect([...applyActivityEvent(state, { type: 'snapshot' } as never)]).toEqual([]);
    expect(
      applyActivityEvent(state, { type: 'conversation_activity', running: true } as never)
    ).toBe(state);
  });
});
