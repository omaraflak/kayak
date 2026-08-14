/**
 * Merging of the global activity stream into the set of working conversations.
 *
 * Kept separate from the transport so the state machine can be tested directly:
 * a stream that mis-handles a snapshot or a stale event leaves spinners running
 * forever, or hides one while the agent is genuinely working.
 */

export interface ActivitySnapshotEvent {
  type: 'snapshot';
  running: string[];
}

export interface ActivityChangeEvent {
  type: 'conversation_activity';
  conversation_id: string;
  running: boolean;
}

export type ActivityEvent =
  | ActivitySnapshotEvent
  | ActivityChangeEvent
  | { type: string; [key: string]: unknown };

/**
 * Applies one stream event to the running set.
 *
 * Returns the *same* set instance when nothing changed, so React can skip the
 * re-render: pings arrive every 20 seconds on an idle stream.
 */
export function applyActivityEvent(
  previous: ReadonlySet<string>,
  event: ActivityEvent | null | undefined
): ReadonlySet<string> {
  if (!event) return previous;

  if (event.type === 'snapshot') {
    // The snapshot is authoritative: it opens every (re)connection, which is
    // what lets a client that missed events while disconnected recover.
    const running = Array.isArray((event as ActivitySnapshotEvent).running)
      ? (event as ActivitySnapshotEvent).running
      : [];
    if (running.length === previous.size && running.every((id) => previous.has(id))) {
      return previous;
    }
    return new Set(running);
  }

  if (event.type === 'conversation_activity') {
    const { conversation_id: id, running } = event as ActivityChangeEvent;
    if (!id) return previous;
    if (running && previous.has(id)) return previous;
    if (!running && !previous.has(id)) return previous;

    const next = new Set(previous);
    if (running) next.add(id);
    else next.delete(id);
    return next;
  }

  return previous;
}
