import type React from 'react';

/**
 * Whether a key press in a composer means "send".
 *
 * Enter also confirms a candidate in Japanese, Chinese and Korean input methods, and
 * sending then ships a half-typed message. Safari marks those presses with
 * `isComposing`, or in some versions only with the legacy key code 229.
 */
export function isSendShortcut(
  event: Pick<React.KeyboardEvent, 'key' | 'shiftKey' | 'keyCode' | 'nativeEvent'>
): boolean {
  return (
    event.key === 'Enter' &&
    !event.shiftKey &&
    !event.nativeEvent.isComposing &&
    event.keyCode !== 229
  );
}
