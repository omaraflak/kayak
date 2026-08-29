import { describe, expect, it } from 'vitest';
import { HuggingFaceModelSearchResult, RuntimeDescriptor } from '../../types';
import { canRuntimeServe, defaultQueryFor, pipelineTagFor, runtimeFor } from './runtimeSupport';

const speech: RuntimeDescriptor = {
  modality: 'speech',
  key: 'tts',
  label: 'Speech synthesis',
  description: '',
  pipeline_tags: ['text-to-speech'],
  supported_libraries: ['transformers'],
  supported_id_fragments: ['kokoro'],
  tunable_fields: [],
  default_query: 'kokoro',
};

const text: RuntimeDescriptor = {
  modality: 'text',
  key: 'vllm',
  label: 'Text generation',
  description: '',
  pipeline_tags: ['text-generation'],
  supported_libraries: [],
  supported_id_fragments: [],
  tunable_fields: [],
  default_query: 'qwen2.5-coder',
};

function model(
  overrides: Partial<HuggingFaceModelSearchResult> & { id: string }
): HuggingFaceModelSearchResult {
  return {
    name: overrides.id,
    downloads: 0,
    likes: 0,
    model_string_hf: '',
    model_string_vllm: '',
    ...overrides,
  };
}

describe('canRuntimeServe', () => {
  it('matches a repository by the library the Hub names', () => {
    expect(
      canRuntimeServe(speech, model({ id: 'microsoft/VibeVoice-1.5B', library_name: 'transformers' }))
    ).toBe(true);
  });

  it('matches by repository id when the Hub names no library', () => {
    // The most downloaded speech model on the Hub reports no library at all, so
    // matching on library alone would exclude exactly the model people want.
    expect(canRuntimeServe(speech, model({ id: 'hexgrad/Kokoro-82M' }))).toBe(true);
    expect(canRuntimeServe(speech, model({ id: 'someone/kokoro-finetune' }))).toBe(true);
  });

  it('matches on tags, which carry the library for some repositories', () => {
    expect(
      canRuntimeServe(speech, model({ id: 'facebook/mms-tts-eng', tags: ['transformers', 'vits'] }))
    ).toBe(true);
  });

  it('rejects a backend the image does not ship', () => {
    // Offering a Start button that cannot work is worse than saying so upfront.
    expect(canRuntimeServe(speech, model({ id: 'coqui/XTTS-v2', library_name: 'coqui' }))).toBe(false);
    expect(canRuntimeServe(speech, model({ id: 'SWivid/F5-TTS', library_name: 'f5-tts' }))).toBe(false);
  });

  it('lets a runtime with no declared restriction serve anything', () => {
    expect(canRuntimeServe(text, model({ id: 'some/unusual-repo' }))).toBe(true);
  });

  it('reads support from the descriptor, not from anything written here', () => {
    // Adding a backend to the image widens what the API reports, and the page has
    // to follow without being rebuilt.
    const widened = { ...speech, supported_libraries: ['transformers', 'coqui'] };

    expect(canRuntimeServe(widened, model({ id: 'coqui/XTTS-v2', library_name: 'coqui' }))).toBe(true);
  });
});

describe('runtime lookup', () => {
  it('finds the runtime for a modality', () => {
    expect(runtimeFor([text, speech], 'speech')?.key).toBe('tts');
    expect(runtimeFor([text, speech], 'transcription')).toBeUndefined();
  });

  it('uses the runtime pipeline tag when searching', () => {
    expect(pipelineTagFor([text, speech], 'speech')).toBe('text-to-speech');
  });

  it('takes the opening query from the runtime, not from a table in the page', () => {
    // Which model is a representative example changes as backends are added.
    expect(defaultQueryFor([text, speech], 'speech')).toBe('kokoro');
    expect(defaultQueryFor([], 'speech')).toBe('');
  });

  it('falls back to text generation before the descriptors arrive', () => {
    // The page renders before the first request finishes; searching for nothing
    // would show an empty catalogue that looks broken.
    expect(pipelineTagFor([], 'speech')).toBe('text-generation');
  });
});
