import { describe, expect, it } from 'vitest';
import { VLLMDeploymentProgress } from '../../types';
import {
  APPLE_CHIP_REQUIRED,
  failureTitle,
  formatElapsed,
  shortModelName,
  shouldResetLogs,
  startBlocker,
} from './serverStatus';

function status(overrides: Partial<VLLMDeploymentProgress>): VLLMDeploymentProgress {
  return {
    modality: 'text',
    model_id: 'Org/Model',
    state: 'ready',
    message: '',
    logs_tail: [],
    port: 8001,
    endpoint: '',
    ...overrides,
  };
}

describe('shortModelName', () => {
  it('keeps the part a person recognises', () => {
    expect(shortModelName('Qwen/Qwen2.5-Coder-7B-Instruct')).toBe('Qwen2.5-Coder-7B-Instruct');
    expect(shortModelName('solo')).toBe('solo');
    expect(shortModelName(null)).toBe('The model');
  });
});

describe('shouldResetLogs', () => {
  it('starts a fresh log when a start begins', () => {
    expect(shouldResetLogs(status({ state: 'error' }), status({ state: 'starting_container' }))).toBe(true);
    expect(shouldResetLogs(undefined, status({ state: 'loading' }))).toBe(true);
  });

  it('starts a fresh log when a different model starts', () => {
    expect(
      shouldResetLogs(
        status({ state: 'loading', model_id: 'Org/A' }),
        status({ state: 'loading', model_id: 'Org/B' })
      )
    ).toBe(true);
  });

  it('keeps the log while one start moves through its phases', () => {
    expect(
      shouldResetLogs(status({ state: 'pulling_image' }), status({ state: 'loading' }))
    ).toBe(false);
  });

  it('keeps the log of a failure, which is what Details is for', () => {
    expect(shouldResetLogs(status({ state: 'loading' }), status({ state: 'error' }))).toBe(false);
  });
});

describe('failureTitle', () => {
  it('tells a crash from a failed start', () => {
    expect(failureTitle(status({ state: 'error', crashed: true }))).toBe('Model stopped unexpectedly');
    expect(failureTitle(status({ state: 'error' }))).toBe("Model couldn't start");
  });
});

describe('formatElapsed', () => {
  it('reads at a glance', () => {
    expect(formatElapsed(12)).toBe('12 s');
    expect(formatElapsed(180)).toBe('3 min');
    expect(formatElapsed(1834)).toBe('30 min');
    expect(formatElapsed(3900)).toBe('1 h 5 min');
    expect(formatElapsed(7200)).toBe('2 h');
    expect(formatElapsed(-3)).toBe('0 s');
  });
});

describe('startBlocker', () => {
  it('stops a GPU-only model on a Mac without the GPU', () => {
    expect(startBlocker('mlx-community/Qwen3-4B-4bit', { supported: false, detail: null })).toBe(
      APPLE_CHIP_REQUIRED
    );
  });

  it("prefers the app's own explanation", () => {
    expect(
      startBlocker('mlx-community/Qwen3-4B-4bit', { supported: false, detail: 'Running under Rosetta.' })
    ).toBe('Running under Rosetta.');
  });

  it('lets everything else try', () => {
    expect(startBlocker('mlx-community/Qwen3-4B-4bit', { supported: true, detail: null })).toBeNull();
    expect(startBlocker('Qwen/Qwen3-4B', { supported: false, detail: null })).toBeNull();
    expect(startBlocker('mlx-community/Qwen3-4B-4bit', null)).toBe(APPLE_CHIP_REQUIRED);
  });
});
