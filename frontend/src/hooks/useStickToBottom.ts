import { useCallback, useLayoutEffect, useRef } from 'react';
import { isNearBottom } from '../features/chat/chatScroll';

/**
 * Keeps a scrolling box pinned to its newest content while it is being appended to.
 *
 * For panes that scroll inside the page rather than with it -- a reasoning box, a log
 * tail. Without this the box fills up and then shows its first lines forever, while
 * everything arriving goes out of sight below the fold.
 *
 * Following stops as soon as the reader scrolls up, and resumes if they scroll back
 * down: someone reading an earlier line must not be yanked to the end mid-sentence.
 *
 * Returns a callback ref rather than an object one, because the box it watches can be
 * mounted and unmounted long after this hook first runs -- a collapsed accordion has
 * no element at all -- and the scroll listener has to follow the node that exists.
 *
 * @param dependencies Values whose change means content was appended.
 * @param active Whether content is still arriving; pinning stops when it is not.
 */
export function useStickToBottom(
  dependencies: ReadonlyArray<unknown>,
  active: boolean
): (node: HTMLDivElement | null) => void {
  const elementRef = useRef<HTMLDivElement | null>(null);
  const isFollowingRef = useRef(true);
  const detachRef = useRef<(() => void) | null>(null);

  const attach = useCallback((node: HTMLDivElement | null) => {
    detachRef.current?.();
    detachRef.current = null;
    elementRef.current = node;
    if (!node) return;

    // Re-reading on every scroll is what lets the reader take over and give control
    // back, rather than being followed or abandoned for the rest of the stream.
    const onScroll = () => {
      isFollowingRef.current = isNearBottom(node);
    };
    node.addEventListener('scroll', onScroll, { passive: true });
    detachRef.current = () => node.removeEventListener('scroll', onScroll);
  }, []);

  useLayoutEffect(() => {
    const element = elementRef.current;
    if (!element || !active || !isFollowingRef.current) return;
    element.scrollTop = element.scrollHeight;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencies, active]);

  return attach;
}
