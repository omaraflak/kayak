import { describe, expect, it } from 'vitest';
import { ApiError, OFFLINE_MESSAGE, errorMessage, readErrorDetail } from './client';

describe('errorMessage', () => {
  it('drops the class-name prefix String() would add', () => {
    // These sentences are written for someone who does not code; "ApiError:" in
    // front of them is noise they cannot act on.
    const error = new ApiError(
      400,
      'This agent uses OpenAI, and no OpenAI key has been added yet.'
    );

    expect(errorMessage(error)).toBe(
      'This agent uses OpenAI, and no OpenAI key has been added yet.'
    );
    expect(errorMessage(error)).not.toContain('ApiError');
  });

  it('keeps a plain thrown string as it is', () => {
    expect(errorMessage('Docker is not running')).toBe('Docker is not running');
  });

  it('never renders an empty dialog', () => {
    // A rejected fetch with no message would otherwise leave the alert blank.
    expect(errorMessage(new Error(''))).toBeTruthy();
    expect(errorMessage(undefined)).toBeTruthy();
  });

  it('says the server is unreachable rather than "Load failed"', () => {
    expect(errorMessage(new ApiError(0, 'Load failed'))).toBe(OFFLINE_MESSAGE);
  });

  it('replaces text nobody wrote for people with what the caller knows', () => {
    const fallback = "Couldn't save the skill. Try again.";

    // A plain-text error page, and the webview's own parse and network errors.
    expect(errorMessage(new ApiError(500, 'Internal Server Error', false), fallback)).toBe(fallback);
    expect(
      errorMessage(new SyntaxError('The string did not match the expected pattern.'), fallback)
    ).toBe(fallback);
    expect(errorMessage(new TypeError('Load failed'), fallback)).toBe(fallback);
  });

  it("shows the server's own explanation, even for a server error", () => {
    // Kayak answers every failure, crashes included, with a sentence of its own.
    const error = new ApiError(503, "Kayak can't reach Docker Desktop.");

    expect(errorMessage(error, 'fallback')).toBe("Kayak can't reach Docker Desktop.");
  });
});

describe('readErrorDetail', () => {
  it("takes the sentence out of Kayak's error body", () => {
    expect(readErrorDetail('{"detail": "This tool was already deleted."}')).toBe(
      'This tool was already deleted.'
    );
  });

  it('treats anything else as unreadable', () => {
    expect(readErrorDetail('Internal Server Error')).toBeNull();
    expect(readErrorDetail('{"detail": [{"msg": "Field required"}]}')).toBeNull();
    expect(readErrorDetail('')).toBeNull();
  });
});
