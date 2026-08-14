import { AppSettings, ProviderCredential, SettingsUpdate } from '../types';

/**
 * The edits a user has made to the stored credentials but not yet saved.
 *
 * Credentials are deliberately not round-tripped through the form. The server returns
 * a mask, and the old form loaded that mask into the input: you could not tell what
 * was configured, replacing a key meant clearing the mask first, and appending to it
 * left the value still looking masked, so the save was silently discarded. A pending
 * edit is instead one of three explicit states.
 */

/** No pending change; the stored key, if any, stays as it is. */
export type CredentialEdit =
  | { kind: 'unchanged' }
  /** Replace the stored key with this one. */
  | { kind: 'replace'; value: string }
  /** Remove the stored key. */
  | { kind: 'clear' };

export interface SettingsDraft {
  /** Keyed by the provider's `setting_key`. */
  credentials: Record<string, CredentialEdit>;
}

/** Builds a draft that matches the saved state exactly. */
export function draftFromSettings(settings: AppSettings): SettingsDraft {
  const credentials: Record<string, CredentialEdit> = {};
  for (const provider of settings.providers) {
    credentials[provider.setting_key] = { kind: 'unchanged' };
  }
  return { credentials };
}

/** Reports whether a credential edit would change what is stored. */
export function isCredentialDirty(
  edit: CredentialEdit | undefined,
  provider: ProviderCredential
): boolean {
  if (!edit || edit.kind === 'unchanged') return false;
  // Clearing a provider that has no key stored changes nothing.
  if (edit.kind === 'clear') return provider.is_set;
  return edit.value.trim().length > 0;
}

/** Reports whether the draft differs from what is saved. */
export function isDraftDirty(draft: SettingsDraft, settings: AppSettings): boolean {
  return settings.providers.some((provider) =>
    isCredentialDirty(draft.credentials[provider.setting_key], provider)
  );
}

/**
 * Reduces a draft to the credentials that actually changed.
 *
 * Sending only what changed is what makes "leave the input empty to keep the existing
 * key" work: an untouched credential is simply absent from the payload, so there is no
 * mask for the server to have to recognise and ignore.
 */
export function buildSettingsUpdate(
  draft: SettingsDraft,
  settings: AppSettings
): SettingsUpdate {
  const update: SettingsUpdate = {};

  for (const provider of settings.providers) {
    const edit = draft.credentials[provider.setting_key];
    if (!isCredentialDirty(edit, provider)) continue;
    // Narrowed by isCredentialDirty: 'unchanged' never reaches here.
    const value = edit!.kind === 'clear' ? '' : (edit as { value: string }).value.trim();
    (update as Record<string, string>)[provider.setting_key] = value;
  }

  return update;
}

/** Describes the stored state of a credential for display. */
export function describeCredential(
  provider: ProviderCredential,
  edit: CredentialEdit | undefined
): string {
  if (edit?.kind === 'clear') return 'Will be removed on save';
  if (edit?.kind === 'replace' && edit.value.trim()) return 'Will be replaced on save';
  return provider.is_set ? `Configured (${provider.preview})` : 'Not set';
}
