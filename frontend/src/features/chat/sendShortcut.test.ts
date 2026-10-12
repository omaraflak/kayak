import { describe, expect, it } from 'vitest';
import { isSendShortcut } from './sendShortcut';

function press(overrides: Partial<{ key: string; shiftKey: boolean; keyCode: number; isComposing: boolean }>) {
  const { isComposing = false, ...rest } = overrides;
  return {
    key: 'Enter',
    shiftKey: false,
    keyCode: 13,
    nativeEvent: { isComposing } as KeyboardEvent,
    ...rest,
  };
}

describe('isSendShortcut', () => {
  it('sends on a plain Enter', () => {
    expect(isSendShortcut(press({}))).toBe(true);
  });

  it('keeps Shift+Enter for a new line', () => {
    expect(isSendShortcut(press({ shiftKey: true }))).toBe(false);
  });

  it('does not send while an input method is choosing a candidate', () => {
    expect(isSendShortcut(press({ isComposing: true }))).toBe(false);
    // Safari's way of saying the same thing.
    expect(isSendShortcut(press({ keyCode: 229 }))).toBe(false);
  });

  it('ignores other keys', () => {
    expect(isSendShortcut(press({ key: 'a', keyCode: 65 }))).toBe(false);
  });
});
