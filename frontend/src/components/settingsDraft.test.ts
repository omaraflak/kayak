import { describe, expect, it } from 'vitest';
import { AppSettings, ProviderCredential } from '../types';
import {
  buildSettingsUpdate,
  describeCredential,
  draftFromSettings,
  isCredentialDirty,
  isDraftDirty,
} from './settingsDraft';

/**
 * These pin down the bug that motivated the rewrite: the old form kept the typed key
 * in state while the server answered with a mask, so "unsaved changes" compared a real
 * key against its own mask and stayed true forever -- leaving the plaintext key in
 * browser state and re-sending it on every later save.
 */

function provider(overrides: Partial<ProviderCredential> = {}): ProviderCredential {
  return {
    id: 'openai',
    name: 'OpenAI',
    icon: '🧠',
    setting_key: 'OPENAI_API_KEY',
    console_url: 'https://platform.openai.com/api-keys',
    key_hint: 'sk-proj-...',
    preview: '',
    is_set: false,
    ...overrides,
  };
}

function settingsWith(providers: ProviderCredential[]): AppSettings {
  return {
    providers,
    security: { auth_required: false, host: '127.0.0.1', is_loopback: true, warning: null },
  };
}

describe('draftFromSettings', () => {
  it('starts clean', () => {
    const settings = settingsWith([provider({ is_set: true, preview: '••••••••9876' })]);
    expect(isDraftDirty(draftFromSettings(settings), settings)).toBe(false);
  });

  it('stays clean after a save that stored a key', () => {
    // The regression: saving returned a mask while the form kept the real key, so the
    // form never went clean again.
    const saved = settingsWith([provider({ is_set: true, preview: '••••••••9876' })]);
    expect(isDraftDirty(draftFromSettings(saved), saved)).toBe(false);
  });
});

describe('isCredentialDirty', () => {
  it('treats an untouched credential as clean', () => {
    expect(isCredentialDirty({ kind: 'unchanged' }, provider({ is_set: true }))).toBe(false);
  });

  it('treats a replacement as a change', () => {
    expect(isCredentialDirty({ kind: 'replace', value: 'sk-new' }, provider())).toBe(true);
  });

  it('ignores a replacement that is only whitespace', () => {
    expect(isCredentialDirty({ kind: 'replace', value: '   ' }, provider())).toBe(false);
  });

  it('treats clearing a stored key as a change', () => {
    expect(isCredentialDirty({ kind: 'clear' }, provider({ is_set: true }))).toBe(true);
  });

  it('ignores clearing a provider that has no key', () => {
    expect(isCredentialDirty({ kind: 'clear' }, provider({ is_set: false }))).toBe(false);
  });
});

describe('buildSettingsUpdate', () => {
  it('sends nothing when nothing changed', () => {
    const settings = settingsWith([provider({ is_set: true })]);
    expect(buildSettingsUpdate(draftFromSettings(settings), settings)).toEqual({});
  });

  it('omits untouched credentials entirely', () => {
    // Absence is what lets an empty input mean "keep the existing key" -- there is no
    // mask for the server to have to recognise.
    const settings = settingsWith([
      provider({ is_set: true, preview: '••••••••9876' }),
      provider({ id: 'gemini', setting_key: 'GEMINI_API_KEY' }),
    ]);
    const draft = draftFromSettings(settings);
    draft.credentials.GEMINI_API_KEY = { kind: 'replace', value: 'AIza-new' };

    const update = buildSettingsUpdate(draft, settings);

    expect(update).toEqual({ GEMINI_API_KEY: 'AIza-new' });
    expect('OPENAI_API_KEY' in update).toBe(false);
  });

  it('sends a replacement key trimmed', () => {
    const settings = settingsWith([provider()]);
    const draft = draftFromSettings(settings);
    draft.credentials.OPENAI_API_KEY = { kind: 'replace', value: '  sk-new-key  ' };

    expect(buildSettingsUpdate(draft, settings)).toEqual({ OPENAI_API_KEY: 'sk-new-key' });
  });

  it('sends an empty string to remove a key', () => {
    const settings = settingsWith([provider({ is_set: true })]);
    const draft = draftFromSettings(settings);
    draft.credentials.OPENAI_API_KEY = { kind: 'clear' };

    expect(buildSettingsUpdate(draft, settings)).toEqual({ OPENAI_API_KEY: '' });
  });

  it('carries several changes at once', () => {
    const settings = settingsWith([
      provider(),
      provider({ id: 'gemini', setting_key: 'GEMINI_API_KEY', is_set: true }),
    ]);
    const draft = draftFromSettings(settings);
    draft.credentials.OPENAI_API_KEY = { kind: 'replace', value: 'sk-a' };
    draft.credentials.GEMINI_API_KEY = { kind: 'clear' };

    expect(buildSettingsUpdate(draft, settings)).toEqual({
      OPENAI_API_KEY: 'sk-a',
      GEMINI_API_KEY: '',
    });
  });
});

describe('describeCredential', () => {
  it('shows the preview for a stored key', () => {
    expect(describeCredential(provider({ is_set: true, preview: '••••••••9876' }), { kind: 'unchanged' }))
      .toBe('Configured (••••••••9876)');
  });

  it('says so when nothing is stored', () => {
    expect(describeCredential(provider(), { kind: 'unchanged' })).toBe('Not set');
  });

  it('previews a pending removal', () => {
    expect(describeCredential(provider({ is_set: true }), { kind: 'clear' }))
      .toBe('Will be removed on save');
  });

  it('previews a pending replacement', () => {
    expect(describeCredential(provider({ is_set: true }), { kind: 'replace', value: 'sk-x' }))
      .toBe('Will be replaced on save');
  });
});
