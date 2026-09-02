import { describe, expect, it } from 'vitest';
import { parseRoutePath, routePathFor } from './router';

/**
 * Local Models became an addressable tab so that Audio's "Start one" could open
 * the speech or transcription catalogue directly instead of dropping the user on
 * the text one to find the right tab themselves. These tests pin that round trip
 * and the fallbacks that keep older links working.
 */

describe('parseRoutePath', () => {
  it('reads the runtime out of a Local Models URL', () => {
    expect(parseRoutePath('/models/speech')).toEqual({
      tab: 'models',
      itemId: 'speech',
    });
    expect(parseRoutePath('/models/transcription')).toEqual({
      tab: 'models',
      itemId: 'transcription',
    });
  });

  it('leaves a bare /models without a runtime, for the caller to default', () => {
    expect(parseRoutePath('/models')).toEqual({ tab: 'models', itemId: null });
  });

  it('still parses the tabs that already carried ids', () => {
    expect(parseRoutePath('/audio/transcribe')).toEqual({
      tab: 'audio',
      itemId: 'transcribe',
    });
    expect(parseRoutePath('/tools/find_files')).toEqual({
      tab: 'tools',
      itemId: 'find_files',
    });
  });

  it('keeps settings and memories free of ids', () => {
    expect(parseRoutePath('/settings')).toEqual({ tab: 'settings' });
    expect(parseRoutePath('/memories')).toEqual({ tab: 'memories' });
  });

  it('reads conversations from both spellings', () => {
    expect(parseRoutePath('/c/abc')).toEqual({ tab: 'chat', conversationId: 'abc' });
    expect(parseRoutePath('/chat')).toEqual({ tab: 'chat', conversationId: null });
    expect(parseRoutePath('/')).toEqual({ tab: 'chat', conversationId: null });
  });

  it('sends an unknown path back to chat rather than a blank tab', () => {
    expect(parseRoutePath('/nonsense')).toEqual({ tab: 'chat', conversationId: null });
  });
});

describe('routePathFor', () => {
  it('addresses a runtime under Local Models', () => {
    expect(routePathFor('models', 'speech')).toBe('/models/speech');
    expect(routePathFor('models', null)).toBe('/models');
  });

  it('round trips with the parser', () => {
    for (const modality of ['text', 'speech', 'transcription']) {
      expect(parseRoutePath(routePathFor('models', modality)).itemId).toBe(modality);
    }
  });

  it('leaves the tabs that take no id alone', () => {
    expect(routePathFor('settings')).toBe('/settings');
    expect(routePathFor('memories')).toBe('/memories');
  });
});
