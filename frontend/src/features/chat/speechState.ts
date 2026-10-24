import { MessageSpeech } from '../../types';

/**
 * How the page's record of read-aloud work follows the server's.
 *
 * Kept apart from the hook so the two rules -- forget what the server no longer
 * knows, and drop what nothing can finish -- can be tested without a browser.
 */

/**
 * Applies the server's answer about some messages to the page's record.
 *
 * Messages that were asked about but are missing from the answer are removed:
 * the server keeps nothing in flight across a restart, and an entry it no longer
 * knows about stayed "Preparing…" -- polled every second -- for as long as the page
 * was open.
 */
export function forgetMissing(
  previous: Record<string, MessageSpeech>,
  asked: string[],
  found: MessageSpeech[]
): Record<string, MessageSpeech> {
  const next = { ...previous };
  const known = new Set(found.map((entry) => entry.message_id));
  for (const id of asked) {
    if (!known.has(id)) delete next[id];
  }
  for (const entry of found) next[entry.message_id] = entry;
  return next;
}

/**
 * The record without work still waiting, for when nothing can finish it.
 *
 * With no voice model running, a "Preparing…" chip only ever spins.
 */
export function withoutPending(
  previous: Record<string, MessageSpeech>
): Record<string, MessageSpeech> {
  const entries = Object.entries(previous);
  if (!entries.some(([, entry]) => entry.state === 'pending')) return previous;
  return Object.fromEntries(entries.filter(([, entry]) => entry.state !== 'pending'));
}
