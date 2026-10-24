import { describe, expect, it } from 'vitest';
import { isOtherLocalPort } from './Markdown';

/** Kayak as the launcher opens it. */
const here = new URL('http://127.0.0.1:8000/c/abc') as unknown as Location;

describe('isOtherLocalPort', () => {
  it('catches another port on this Mac, however it is written', () => {
    // A model's own server: following the link would hand it Kayak's session.
    expect(isOtherLocalPort('http://127.0.0.1:8001/v1/models', here)).toBe(true);
    expect(isOtherLocalPort('http://localhost:3000/', here)).toBe(true);
    expect(isOtherLocalPort('http://[::1]:8100/', here)).toBe(true);
    expect(isOtherLocalPort('http://0.0.0.0:5000/', here)).toBe(true);
  });

  it("leaves Kayak's own pages and relative links alone", () => {
    expect(isOtherLocalPort('http://127.0.0.1:8000/settings', here)).toBe(false);
    expect(isOtherLocalPort('/c/other', here)).toBe(false);
    expect(isOtherLocalPort('report.csv', here)).toBe(false);
  });

  it('leaves the rest of the web alone', () => {
    expect(isOtherLocalPort('https://huggingface.co/Qwen', here)).toBe(false);
    expect(isOtherLocalPort('mailto:someone@example.com', here)).toBe(false);
    expect(isOtherLocalPort(undefined, here)).toBe(false);
  });
});
