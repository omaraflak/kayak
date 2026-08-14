import { describe, expect, it } from 'vitest';
import {
  detectWeightFormat,
  estimateModelSize,
  formatBytes,
  judgeFit,
  maxWorkingMemoryGB,
  parseParameterCount,
} from './modelSizing';

describe('parseParameterCount', () => {
  it('reads the parameter count from a typical repository id', () => {
    expect(parseParameterCount('Qwen/Qwen2.5-Coder-7B-Instruct')).toBe(7);
    expect(parseParameterCount('meta-llama/Llama-3.2-1B')).toBe(1);
    expect(parseParameterCount('mistralai/Mistral-7B-Instruct-v0.3')).toBe(7);
  });

  it('does not mistake a version number for a size', () => {
    // "Qwen2.5" and "3.2" are versions; only the value carrying a B is a count.
    expect(parseParameterCount('Qwen/Qwen2.5-Coder-7B-Instruct')).toBe(7);
    expect(parseParameterCount('Qwen/Qwen2.5-Coder')).toBeNull();
  });

  it('ignores digits in the organization name', () => {
    expect(parseParameterCount('01-ai/Yi-Coder-9B')).toBe(9);
  });

  it('handles fractional sizes', () => {
    expect(parseParameterCount('Qwen/Qwen2.5-1.5B-Instruct')).toBe(1.5);
  });

  it('returns null when the name says nothing about size', () => {
    expect(parseParameterCount('openai/gpt-oss')).toBeNull();
    expect(parseParameterCount('some-org/an-untitled-experiment')).toBeNull();
  });

  it('rejects implausible counts rather than reporting nonsense', () => {
    expect(parseParameterCount('org/model-99999B')).toBeNull();
  });
});

describe('detectWeightFormat', () => {
  it('recognizes four-bit quantization', () => {
    expect(detectWeightFormat('Qwen/Qwen2.5-7B-Instruct-AWQ')).toBe('int4');
    expect(detectWeightFormat('TheBloke/Llama-2-13B-GPTQ')).toBe('int4');
  });

  it('recognizes eight-bit formats', () => {
    expect(detectWeightFormat('neuralmagic/Llama-3.1-8B-FP8')).toBe('fp8');
  });

  it('treats an unmarked repository as full precision', () => {
    expect(detectWeightFormat('Qwen/Qwen2.5-Coder-7B-Instruct')).toBe('full');
  });
});

describe('estimateModelSize', () => {
  it('sizes an unquantized model at two bytes per parameter', () => {
    const estimate = estimateModelSize('Qwen/Qwen2.5-Coder-7B-Instruct');
    expect(estimate?.weightsGB).toBe(14);
    // Weights alone are not the requirement: the KV cache and activations need room.
    expect(estimate?.requiredGB).toBeGreaterThan(14);
  });

  it('accounts for quantization', () => {
    const full = estimateModelSize('org/Model-7B');
    const quantized = estimateModelSize('org/Model-7B-AWQ');
    expect(quantized!.weightsGB).toBeLessThan(full!.weightsGB);
  });

  it('returns null rather than guessing at an unlabelled model', () => {
    expect(estimateModelSize('org/mystery-model')).toBeNull();
  });
});

describe('judgeFit', () => {
  it('flags a machine with no accelerator regardless of model size', () => {
    // Model size is beside the point here: CPU inference is the headline.
    expect(judgeFit(estimateModelSize('org/Model-1B'), 0)).toBe('cpu-only');
  });

  it('reports a comfortable fit', () => {
    expect(judgeFit(estimateModelSize('org/Model-7B'), 48)).toBe('fits');
  });

  it('reports a model that cannot fit', () => {
    expect(judgeFit(estimateModelSize('org/Model-70B'), 24)).toBe('too-large');
  });

  it('warns when the model only just fits', () => {
    const estimate = estimateModelSize('org/Model-7B'); // ~18.8 GB required
    expect(judgeFit(estimate, 20)).toBe('tight');
  });

  it('says unknown when the size could not be read', () => {
    expect(judgeFit(null, 24)).toBe('unknown');
  });
});

describe('maxWorkingMemoryGB', () => {
  it('leaves room for the model and the runtime', () => {
    // The reported machine: 7.77 GB of Docker memory running a 1.4 GB model had only
    // 2.5 GB free once loaded, so the ceiling must sit at or below that.
    const ceiling = maxWorkingMemoryGB(7.77, estimateModelSize('Qwen/Qwen3-0.6B'));

    expect(ceiling).toBeGreaterThanOrEqual(1);
    expect(ceiling).toBeLessThanOrEqual(2);
  });

  it('shrinks as the model grows', () => {
    const small = maxWorkingMemoryGB(32, estimateModelSize('org/Model-1B'));
    const large = maxWorkingMemoryGB(32, estimateModelSize('org/Model-7B'));

    expect(large).toBeLessThan(small);
  });

  it('never offers less than a gigabyte', () => {
    // Below this the deployment cannot run at all, so the floor is honest about the
    // minimum rather than proposing zero.
    expect(maxWorkingMemoryGB(4, estimateModelSize('org/Model-7B'))).toBe(1);
    expect(maxWorkingMemoryGB(0, null)).toBe(1);
  });

  it('still bounds a model whose size is unknown', () => {
    const ceiling = maxWorkingMemoryGB(16, null);

    expect(ceiling).toBeGreaterThan(1);
    expect(ceiling).toBeLessThan(16);
  });
});

describe('formatBytes', () => {
  it('uses the unit that matches the magnitude', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(1024 * 1024 * 1.5)).toBe('1.5 MB');
    expect(formatBytes(1024 ** 3 * 15)).toBe('15 GB');
  });

  it('treats an empty cache as zero rather than NaN', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(Number.NaN)).toBe('0 B');
  });
});
