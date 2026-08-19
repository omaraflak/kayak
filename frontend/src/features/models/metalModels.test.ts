import { describe, it, expect } from 'vitest';
import { MLX_SEARCH_PREFIX, isMlxModel } from './metalModels';

describe('isMlxModel', () => {
  it('accepts repositories the Apple GPU can serve', () => {
    expect(isMlxModel('mlx-community/Qwen3.8-27B-8bit')).toBe(true);
  });

  it('matches the organisation case-insensitively', () => {
    // Hugging Face resolves owners case-insensitively, so the same model can
    // arrive with either casing and must still be badged.
    expect(isMlxModel('MLX-Community/Llama-3.2-3B-Instruct-4bit')).toBe(true);
  });

  it('rejects ordinary repositories', () => {
    expect(isMlxModel('Qwen/Qwen2.5-Coder-7B-Instruct')).toBe(false);
    expect(isMlxModel('meta-llama/Llama-3.1-8B')).toBe(false);
  });

  it('rejects identifiers that are not a repository', () => {
    expect(isMlxModel('mlx-community')).toBe(false);
    expect(isMlxModel('mlx-community/')).toBe(false);
    expect(isMlxModel('mlx-community/a/b')).toBe(false);
    expect(isMlxModel('')).toBe(false);
  });

  it('searching the prefix finds only models it accepts', () => {
    // The button fills the search box with this, so the two have to agree.
    expect(isMlxModel(`${MLX_SEARCH_PREFIX}Qwen2.5-7B-Instruct-4bit`)).toBe(true);
  });
});
