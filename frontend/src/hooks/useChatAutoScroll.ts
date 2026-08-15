import { MutableRefObject, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { isNearBottom } from '../features/chat/chatScroll';

interface ChatAutoScroll {
  /** Attach to the scrolling element. */
  containerRef: MutableRefObject<HTMLDivElement | null>;
  /** Attach to the element that grows as messages arrive. */
  contentRef: MutableRefObject<HTMLDivElement | null>;
  /** Whether the view is pinned to the newest message. */
  isFollowing: boolean;
  /** Attach to the container's onScroll. */
  onScroll: () => void;
  /** Re-pin to the end, e.g. after sending. */
  followOutput: () => void;
  scrollToBottom: (smooth?: boolean) => void;
}

/**
 * Keeps a transcript pinned to its newest message while the reader wants it to be.
 *
 * Two mechanisms, because messages arrive in two ways. New content is caught before
 * the browser paints, so opening a conversation simply *is* at the end rather than
 * showing the top and scrolling down through the history. Content that finishes
 * loading after paint -- images, KaTeX, highlighted code -- grows the transcript
 * underneath the viewport instead, and is caught by observing the height.
 *
 * @param dependencies Values whose change means new content has arrived.
 * @param resetKey Changing this re-pins to the end; pass the conversation id.
 */
export function useChatAutoScroll(
  dependencies: ReadonlyArray<unknown>,
  resetKey: string | null
): ChatAutoScroll {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const [isFollowing, setIsFollowing] = useState(true);

  // Mirrors isFollowing for the resize observer, which is registered once and would
  // otherwise capture the value it had at registration.
  const isFollowingRef = useRef(true);
  isFollowingRef.current = isFollowing;

  const scrollToBottom = useCallback((smooth = false) => {
    const container = containerRef.current;
    if (!container) return;
    container.scrollTo({
      top: container.scrollHeight,
      behavior: smooth ? 'smooth' : 'auto',
    });
  }, []);

  useEffect(() => {
    setIsFollowing(true);
  }, [resetKey]);

  // Instant, never animated: a smooth scroll here only chases the content it is
  // trying to follow.
  useLayoutEffect(() => {
    if (isFollowing) scrollToBottom();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencies, isFollowing, scrollToBottom]);

  useEffect(() => {
    const content = contentRef.current;
    if (!content) return;

    const observer = new ResizeObserver(() => {
      if (isFollowingRef.current) scrollToBottom();
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [resetKey, scrollToBottom]);

  const onScroll = useCallback(() => {
    const container = containerRef.current;
    if (container) setIsFollowing(isNearBottom(container));
  }, []);

  const followOutput = useCallback(() => setIsFollowing(true), []);

  return {
    containerRef,
    contentRef,
    isFollowing,
    onScroll,
    followOutput,
    scrollToBottom,
  };
}
