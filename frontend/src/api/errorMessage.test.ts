import { describe, expect, it } from 'vitest';
import { ApiError, errorMessage } from './client';

describe('errorMessage', () => {
  it('drops the class-name prefix String() would add', () => {
    // These sentences are written for someone who does not code; "ApiError:" in
    // front of them is noise they cannot act on.
    const error = new ApiError(
      400,
      "No Google Gemini API key is configured, so 'gemini/gemini-3.6-flash' cannot run. Add one in Settings."
    );

    expect(errorMessage(error)).toBe(
      "No Google Gemini API key is configured, so 'gemini/gemini-3.6-flash' cannot run. Add one in Settings."
    );
    expect(errorMessage(error)).not.toContain('ApiError');
  });

  it('keeps a plain thrown string as it is', () => {
    expect(errorMessage('Docker is not running')).toBe('Docker is not running');
  });

  it('never renders an empty dialog', () => {
    // A rejected fetch with no message would otherwise leave the alert blank.
    expect(errorMessage(new Error(''))).toBe('Something went wrong.');
    expect(errorMessage(undefined)).toBeTruthy();
  });
});
