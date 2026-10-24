import { describe, expect, it } from 'vitest';
import { formatBytes, shortModelName } from './format';

describe('formatBytes', () => {
  it('uses the unit that matches the magnitude', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(1024 * 1024 * 1.5)).toBe('1.5 MB');
    expect(formatBytes(1024 ** 3 * 15)).toBe('15 GB');
  });

  it('treats nothing as zero rather than NaN', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(Number.NaN)).toBe('0 B');
  });
});

describe('shortModelName', () => {
  it('drops the routing prefixes nobody says out loud', () => {
    expect(shortModelName('vllm/Qwen/Qwen3-0.6B')).toBe('Qwen3-0.6B');
    expect(shortModelName('gemini/gemini-3.6-flash')).toBe('gemini-3.6-flash');
  });

  it('keeps a bare name as it is', () => {
    expect(shortModelName('gpt-5')).toBe('gpt-5');
  });
});
