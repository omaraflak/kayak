/**
 * Scroll-position logic for the transcript.
 *
 * The reader is "following" while they sit at the newest message; everything
 * auto-scrolls only in that state, so scrolling up to re-read something is
 * never undone by arriving output.
 */

/** How close to the bottom still counts as following along, in pixels. */
export const FOLLOW_THRESHOLD_PX = 120;

export interface ScrollMetrics {
  scrollHeight: number;
  scrollTop: number;
  clientHeight: number;
}

/** Distance in pixels between the viewport's bottom edge and the content's end. */
export function distanceFromBottom({
  scrollHeight,
  scrollTop,
  clientHeight,
}: ScrollMetrics): number {
  return scrollHeight - scrollTop - clientHeight;
}

/**
 * Whether the reader counts as parked at the newest message.
 *
 * A threshold rather than an exact match: sub-pixel rounding and a trailing
 * margin mean an untouched transcript is rarely at exactly zero.
 */
export function isNearBottom(
  metrics: ScrollMetrics,
  threshold: number = FOLLOW_THRESHOLD_PX
): boolean {
  return distanceFromBottom(metrics) <= threshold;
}
