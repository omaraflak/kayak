import { Message } from '../types';

/**
 * Collapses a stored message history into the turns the transcript displays.
 *
 * One agent turn is many stored messages: the ReAct loop writes an assistant message
 * per iteration, each followed by the results of any tools it called. They are shown
 * as a single turn, which means the turn needs to know both of its ends -- the first
 * message, which is where a revert or retry cuts, and the last, which is where a
 * branch copies through. Cutting anywhere else would strand a tool call without its
 * result, and every provider rejects a history like that.
 */

export interface TurnToolCall {
  id: string;
  name: string;
  argumentsStr: string;
  output?: string;
  isError?: boolean;
}

export interface GroupedTurn {
  /** Identifier of the first stored message in this turn; the cut point. */
  id: string;
  /** Identifier of the last stored message in this turn; the copy-through point. */
  lastMessageId: string;
  role: 'user' | 'assistant';
  content?: string;
  thinking?: string;
  toolCalls: TurnToolCall[];
}

/** Reports whether a tool result records a failure. */
function isErrorOutput(content: string | null | undefined): boolean {
  return Boolean(content?.startsWith('Error:') || content?.startsWith('✗'));
}

export function groupMessagesIntoTurns(messages: Message[]): GroupedTurn[] {
  const turns: GroupedTurn[] = [];
  let currentAssistantTurn: GroupedTurn | null = null;
  const toolOutputsMap: Record<string, { output: string; name?: string; isError?: boolean }> = {};

  // First pass: collect tool outputs by tool_call_id
  for (const msg of messages) {
    if (msg.role === 'tool' && msg.tool_call_id) {
      toolOutputsMap[msg.tool_call_id] = {
        output: msg.content || '',
        name: msg.name || undefined,
        isError: isErrorOutput(msg.content),
      };
    }
  }

  for (let index = 0; index < messages.length; index++) {
    const msg = messages[index];

    if (msg.role === 'user') {
      if (currentAssistantTurn) {
        turns.push(currentAssistantTurn);
        currentAssistantTurn = null;
      }
      const id = msg.id || `user_${index}`;
      turns.push({
        id,
        lastMessageId: id,
        role: 'user',
        content: msg.content || '',
        toolCalls: [],
      });
      continue;
    }

    if (msg.role === 'assistant') {
      if (!currentAssistantTurn) {
        const id = msg.id || `assistant_${index}`;
        currentAssistantTurn = {
          id,
          lastMessageId: id,
          role: 'assistant',
          content: msg.content || '',
          thinking: msg.thinking || undefined,
          toolCalls: [],
        };
      } else {
        if (msg.id) currentAssistantTurn.lastMessageId = msg.id;
        if (msg.content) {
          currentAssistantTurn.content = currentAssistantTurn.content
            ? `${currentAssistantTurn.content}\n\n${msg.content}`
            : msg.content;
        }
        if (msg.thinking) {
          currentAssistantTurn.thinking = currentAssistantTurn.thinking
            ? `${currentAssistantTurn.thinking}\n\n${msg.thinking}`
            : msg.thinking;
        }
      }

      if (msg.tool_calls) {
        for (const tc of msg.tool_calls) {
          const matched = toolOutputsMap[tc.id];
          currentAssistantTurn.toolCalls.push({
            id: tc.id,
            name: tc.function.name,
            argumentsStr: tc.function.arguments,
            output: matched?.output,
            isError: matched?.isError,
          });
        }
      }
      continue;
    }

    // Tool results belong to the turn that requested them. They render inside the
    // tool-call accordion rather than on their own, but they still move the turn's
    // end marker: a branch taken before them would copy a call with no result.
    if (msg.role === 'tool' && currentAssistantTurn && msg.id) {
      currentAssistantTurn.lastMessageId = msg.id;
    }
  }

  if (currentAssistantTurn) {
    turns.push(currentAssistantTurn);
  }

  return turns;
}

/**
 * Counts the messages a cut at `anchorId` would remove.
 *
 * There is no undo for any of these operations, so the confirmation has to state the
 * cost, and a turn is almost always more stored messages than the one reply on screen.
 */
export function countMessagesFrom(messages: Message[], anchorId: string): number {
  const index = messages.findIndex((message) => message.id === anchorId);
  return index === -1 ? 0 : messages.length - index;
}

/** Finds the prompt that produced a turn, which a revert restores to the composer. */
export function findPrecedingUserMessageId(
  messages: Message[],
  anchorId: string
): string | null {
  const index = messages.findIndex((message) => message.id === anchorId);
  if (index === -1) return null;

  for (let cursor = index - 1; cursor >= 0; cursor -= 1) {
    if (messages[cursor].role === 'user') return messages[cursor].id ?? null;
  }
  return null;
}

/** Index of the last assistant turn, or -1 when the transcript has none. */
export function lastAssistantTurnIndex(turns: GroupedTurn[]): number {
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    if (turns[index].role === 'assistant') return index;
  }
  return -1;
}

/**
 * Reports whether a turn can be acted on.
 *
 * Turns rendered from optimistic or unsaved messages carry synthetic ids that the
 * server has never seen, so offering to revert or branch at them would only produce
 * a 404.
 */
export function isPersistedTurn(turn: GroupedTurn): boolean {
  return !turn.id.startsWith('user_')
    && !turn.id.startsWith('assistant_')
    && !turn.id.startsWith('optimistic_');
}
