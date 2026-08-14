import { describe, expect, it } from 'vitest';
import { FOLLOW_THRESHOLD_PX, distanceFromBottom, isNearBottom } from './chatScroll';

describe('distanceFromBottom', () => {
  it('is zero when parked at the end', () => {
    expect(
      distanceFromBottom({ scrollHeight: 5000, scrollTop: 4400, clientHeight: 600 })
    ).toBe(0);
  });

  it('grows as the reader scrolls up', () => {
    expect(
      distanceFromBottom({ scrollHeight: 5000, scrollTop: 1000, clientHeight: 600 })
    ).toBe(3400);
  });

  it('is zero for content shorter than the viewport', () => {
    expect(
      distanceFromBottom({ scrollHeight: 400, scrollTop: 0, clientHeight: 400 })
    ).toBe(0);
  });
});

describe('isNearBottom', () => {
  it('counts the exact bottom as following', () => {
    expect(
      isNearBottom({ scrollHeight: 5000, scrollTop: 4400, clientHeight: 600 })
    ).toBe(true);
  });

  it('tolerates a small gap, so rounding does not unstick the view', () => {
    expect(
      isNearBottom({
        scrollHeight: 5000,
        scrollTop: 4400 - (FOLLOW_THRESHOLD_PX - 1),
        clientHeight: 600,
      })
    ).toBe(true);
  });

  it('stops following once the reader scrolls meaningfully up', () => {
    expect(
      isNearBottom({
        scrollHeight: 5000,
        scrollTop: 4400 - (FOLLOW_THRESHOLD_PX + 1),
        clientHeight: 600,
      })
    ).toBe(false);
  });

  it('treats a transcript shorter than the viewport as following', () => {
    // Nothing to scroll: arriving output must still pin to the bottom.
    expect(
      isNearBottom({ scrollHeight: 300, scrollTop: 0, clientHeight: 600 })
    ).toBe(true);
  });
});
