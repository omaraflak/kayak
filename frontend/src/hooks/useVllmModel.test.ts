import { describe, expect, it } from 'vitest';
import { VLLMDeploymentProgress } from '../types';
import { localModelState, parseVllmModelId } from './useVllmModel';

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

describe('localModelState', () => {
  it('reports a failure of this model as an error', () => {
    expect(localModelState(status({ state: 'error' }), 'Org/Model')).toBe('error');
  });

  it("does not blame this model for another model's failure", () => {
    expect(localModelState(status({ state: 'error', model_id: 'Org/Other' }), 'Org/Model')).toBe(
      'offline'
    );
  });

  it('keeps the states it always had', () => {
    expect(localModelState(status({ state: 'ready' }), 'Org/Model')).toBe('ready');
    expect(localModelState(status({ state: 'loading', model_id: null }), 'Org/Model')).toBe('loading');
    expect(localModelState(status({ state: 'stopped' }), 'Org/Model')).toBe('offline');
    expect(localModelState(null, 'Org/Model')).toBe('offline');
  });
});

describe('parseVllmModelId', () => {
  it('reads local model ids only', () => {
    expect(parseVllmModelId('vllm/Org/Model')).toBe('Org/Model');
    expect(parseVllmModelId('openai/gpt-4o')).toBeNull();
    expect(parseVllmModelId('vllm/')).toBeNull();
  });
});
