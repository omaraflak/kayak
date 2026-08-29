/**
 * Messages typed while the agent is still answering.
 *
 * Sending during a turn used to be refused outright, so a thought you had while
 * reading the reply had to be held in your head until the agent finished. They
 * queue instead, and go together as one message when the turn ends -- one message
 * rather than several because each would otherwise start its own turn, and the
 * agent would answer the first before ever seeing the rest.
 */

export interface QueuedMessage {
  id: string;
  text: string;
}

/**
 * The single message a set of queued ones becomes.
 *
 * One queued message is sent as itself: turning a single thought into a
 * one-item bullet list would be formatting for its own sake. Several become a
 * list, so the agent can see they are separate points rather than one rambling
 * paragraph.
 */
export function coalesceQueued(queued: QueuedMessage[]): string {
  const texts = queued.map((message) => message.text.trim()).filter(Boolean);
  if (texts.length === 0) return '';
  if (texts.length === 1) return texts[0];
  return texts.map((text) => `- ${indentContinuation(text)}`).join('\n');
}

/**
 * Keeps a multi-line message inside its bullet.
 *
 * Without this, the second line of a queued message starts at column zero and
 * reads as a sibling of the bullets rather than as part of one.
 */
function indentContinuation(text: string): string {
  return text.split('\n').join('\n  ');
}
