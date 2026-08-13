import { describe, expect, it } from 'vitest';
import { Message } from '../types';
import {
  GroupedTurn,
  countMessagesFrom,
  findPrecedingUserMessageId,
  groupMessagesIntoTurns,
  isPersistedTurn,
  lastAssistantTurnIndex,
} from './conversationTurns';

/**
 * The turn boundaries these tests pin down are what revert, retry and branch cut on.
 * A turn that reports the wrong end would branch a history whose last tool call has
 * no result -- which no provider accepts.
 */

let counter = 0;

function message(partial: Partial<Message> & Pick<Message, 'role'>): Message {
  counter += 1;
  return {
    id: `m${counter}`,
    conversation_id: 'c1',
    content: null,
    created_at: '2026-01-01T00:00:00Z',
    ...partial,
  } as Message;
}

function toolCall(id: string, name = 'run_command') {
  return { id, type: 'function' as const, function: { name, arguments: '{}' } };
}

/** A prompt, an agent turn that used one tool, then a closing summary. */
function historyWithOneToolTurn(): Message[] {
  counter = 0;
  return [
    message({ role: 'user', content: 'list the files' }),
    message({ role: 'assistant', content: 'Looking.', tool_calls: [toolCall('call_1')] }),
    message({ role: 'tool', content: 'a.py', tool_call_id: 'call_1', name: 'run_command' }),
    message({ role: 'assistant', content: 'One file.' }),
  ];
}

describe('groupMessagesIntoTurns', () => {
  it('renders a prompt and its agent turn as two turns', () => {
    const turns = groupMessagesIntoTurns(historyWithOneToolTurn());

    expect(turns.map((turn) => turn.role)).toEqual(['user', 'assistant']);
  });

  it('anchors an agent turn on its first stored message', () => {
    const turns = groupMessagesIntoTurns(historyWithOneToolTurn());

    // m2 is the assistant message that opened the turn; cutting here removes the
    // whole turn including its tool result.
    expect(turns[1].id).toBe('m2');
  });

  it('carries the turn end past its tool results', () => {
    const turns = groupMessagesIntoTurns(historyWithOneToolTurn());

    // m4 is the closing assistant message; a branch copies through it so that
    // call_1 keeps its result.
    expect(turns[1].lastMessageId).toBe('m4');
  });

  it('ends a turn on a tool result when the agent said nothing after it', () => {
    counter = 0;
    const turns = groupMessagesIntoTurns([
      message({ role: 'user', content: 'go' }),
      message({ role: 'assistant', tool_calls: [toolCall('call_1')] }),
      message({ role: 'tool', content: 'done', tool_call_id: 'call_1' }),
    ]);

    expect(turns[1].lastMessageId).toBe('m3');
  });

  it('merges the iterations of one agent turn', () => {
    counter = 0;
    const turns = groupMessagesIntoTurns([
      message({ role: 'user', content: 'go' }),
      message({ role: 'assistant', content: 'First.', tool_calls: [toolCall('call_1')] }),
      message({ role: 'tool', content: 'ok', tool_call_id: 'call_1' }),
      message({ role: 'assistant', content: 'Second.', tool_calls: [toolCall('call_2')] }),
      message({ role: 'tool', content: 'ok', tool_call_id: 'call_2' }),
      message({ role: 'assistant', content: 'Done.' }),
    ]);

    expect(turns).toHaveLength(2);
    expect(turns[1].content).toBe('First.\n\nSecond.\n\nDone.');
    expect(turns[1].toolCalls.map((call) => call.id)).toEqual(['call_1', 'call_2']);
    expect(turns[1].id).toBe('m2');
    expect(turns[1].lastMessageId).toBe('m6');
  });

  it('attaches each tool result to its own call', () => {
    const turns = groupMessagesIntoTurns(historyWithOneToolTurn());

    expect(turns[1].toolCalls[0].output).toBe('a.py');
    expect(turns[1].toolCalls[0].isError).toBe(false);
  });

  it('marks a failed tool result as an error', () => {
    counter = 0;
    const turns = groupMessagesIntoTurns([
      message({ role: 'user', content: 'go' }),
      message({ role: 'assistant', tool_calls: [toolCall('call_1')] }),
      message({ role: 'tool', content: 'Error: no such file', tool_call_id: 'call_1' }),
    ]);

    expect(turns[1].toolCalls[0].isError).toBe(true);
  });

  it('starts a new turn at each prompt', () => {
    counter = 0;
    const turns = groupMessagesIntoTurns([
      message({ role: 'user', content: 'one' }),
      message({ role: 'assistant', content: 'a' }),
      message({ role: 'user', content: 'two' }),
      message({ role: 'assistant', content: 'b' }),
    ]);

    expect(turns.map((turn) => turn.role)).toEqual(['user', 'assistant', 'user', 'assistant']);
    expect(turns[3].id).toBe('m4');
  });

  it('handles an empty history', () => {
    expect(groupMessagesIntoTurns([])).toEqual([]);
  });

  it('does not lose a turn that is still the last thing in the history', () => {
    counter = 0;
    const turns = groupMessagesIntoTurns([
      message({ role: 'user', content: 'go' }),
      message({ role: 'assistant', content: 'working' }),
    ]);

    expect(turns).toHaveLength(2);
  });
});

describe('lastAssistantTurnIndex', () => {
  it('finds the turn a retry would replace', () => {
    const turns = groupMessagesIntoTurns(historyWithOneToolTurn());
    expect(lastAssistantTurnIndex(turns)).toBe(1);
  });

  it('ignores a trailing prompt that has no reply yet', () => {
    counter = 0;
    const turns = groupMessagesIntoTurns([
      message({ role: 'user', content: 'one' }),
      message({ role: 'assistant', content: 'a' }),
      message({ role: 'user', content: 'two' }),
    ]);

    expect(lastAssistantTurnIndex(turns)).toBe(1);
  });

  it('reports none when the agent has not replied at all', () => {
    counter = 0;
    const turns = groupMessagesIntoTurns([message({ role: 'user', content: 'one' })]);
    expect(lastAssistantTurnIndex(turns)).toBe(-1);
  });
});

describe('countMessagesFrom', () => {
  it('counts the whole tail, not just the visible reply', () => {
    // The turn shows as one message but is stored as three; the confirmation has to
    // say three.
    const history = historyWithOneToolTurn();
    expect(countMessagesFrom(history, 'm2')).toBe(3);
  });

  it('counts a single trailing message', () => {
    expect(countMessagesFrom(historyWithOneToolTurn(), 'm4')).toBe(1);
  });

  it('counts nothing for a message that is not there', () => {
    expect(countMessagesFrom(historyWithOneToolTurn(), 'nope')).toBe(0);
  });
});

describe('findPrecedingUserMessageId', () => {
  it('finds the prompt that produced a turn', () => {
    expect(findPrecedingUserMessageId(historyWithOneToolTurn(), 'm2')).toBe('m1');
  });

  it('skips back over the turn is own messages', () => {
    expect(findPrecedingUserMessageId(historyWithOneToolTurn(), 'm4')).toBe('m1');
  });

  it('returns null when nothing precedes it', () => {
    expect(findPrecedingUserMessageId(historyWithOneToolTurn(), 'm1')).toBeNull();
  });
});

describe('isPersistedTurn', () => {
  it('accepts a turn built from stored messages', () => {
    const turns = groupMessagesIntoTurns(historyWithOneToolTurn());
    expect(isPersistedTurn(turns[1])).toBe(true);
  });

  it('rejects a turn whose id the server has never seen', () => {
    // An optimistic message or one stored without an id gets a synthetic anchor;
    // offering to branch at it would only produce a 404.
    const synthetic = { id: 'assistant_3', lastMessageId: 'assistant_3' } as GroupedTurn;
    expect(isPersistedTurn(synthetic)).toBe(false);
    expect(isPersistedTurn({ id: 'optimistic_1712' } as GroupedTurn)).toBe(false);
  });
});
