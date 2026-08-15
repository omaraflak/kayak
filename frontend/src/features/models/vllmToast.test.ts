import { describe, expect, it } from 'vitest';
import { shouldShowVllmToast } from './vllmToast';

const base = { dismissed: false, sawDeployment: false, readyExpired: false };

describe('shouldShowVllmToast', () => {
  it('stays hidden before any status has been fetched', () => {
    expect(shouldShowVllmToast({ ...base, state: undefined })).toBe(false);
    expect(shouldShowVllmToast({ ...base, state: null })).toBe(false);
  });

  it('stays hidden when the server is idle or stopped', () => {
    expect(shouldShowVllmToast({ ...base, state: 'idle' })).toBe(false);
    expect(shouldShowVllmToast({ ...base, state: 'stopped' })).toBe(false);
  });

  it('shows while a deployment is in progress', () => {
    for (const state of ['pulling_image', 'starting_container', 'loading']) {
      expect(shouldShowVllmToast({ ...base, state })).toBe(true);
    }
  });

  it('shows errors regardless of how the tab arrived', () => {
    expect(shouldShowVllmToast({ ...base, state: 'error' })).toBe(true);
  });

  it('does not resurface on a page refresh while a model is already serving', () => {
    // The reported bug: reloading the page brought the toast back permanently.
    expect(
      shouldShowVllmToast({ ...base, state: 'ready', sawDeployment: false })
    ).toBe(false);
  });

  it('confirms a deployment this tab watched, then retires after the timeout', () => {
    expect(
      shouldShowVllmToast({ ...base, state: 'ready', sawDeployment: true })
    ).toBe(true);
    expect(
      shouldShowVllmToast({
        ...base,
        state: 'ready',
        sawDeployment: true,
        readyExpired: true,
      })
    ).toBe(false);
  });

  it('respects a manual dismissal in every state', () => {
    expect(
      shouldShowVllmToast({ ...base, state: 'loading', dismissed: true })
    ).toBe(false);
    expect(
      shouldShowVllmToast({
        ...base,
        state: 'ready',
        sawDeployment: true,
        dismissed: true,
      })
    ).toBe(false);
  });
});
