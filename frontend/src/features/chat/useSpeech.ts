import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import { MessageSpeech } from '../../types';

/**
 * Reading replies aloud, and dictating them.
 *
 * Both go through Kayak rather than the browser's own speech APIs: the models
 * are the ones running on this machine, so the voice is the voice the user chose
 * and the transcription is the model they started, not whatever the browser
 * happens to ship.
 */

/**
 * Reading messages aloud.
 *
 * The audio belongs to the message and lives on the server for a day, so this
 * hook holds only what is local to the page: which message this tab is playing,
 * and which ones it asked for. Everything else -- whether a synthesis is in
 * flight, how far it has got, whether the audio already exists -- is read back
 * from the server, which is why a reload can pick the thread up again.
 *
 * Playback is deliberately not resumed on load. Work continuing is not the same
 * as consent to start talking, and a page that begins speaking because it was
 * refreshed is startling.
 */
export function useMessageSpeech(available: boolean) {
  const [states, setStates] = useState<Record<string, MessageSpeech>>({});
  const [playingId, setPlayingId] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  // Messages this page asked for. Only these are played when they finish; one
  // synthesised in another tab, or before a reload, waits to be asked for.
  const awaited = useRef<Set<string>>(new Set());
  const watching = useRef<Set<string>>(new Set());

  const anyPending = Object.values(states).some((entry) => entry.state === 'pending');

  /** Reads the state of these messages back from the server. */
  const sync = useCallback(async (messageIds: string[]) => {
    if (!available || messageIds.length === 0) return;
    try {
      const found = await api.getMessageSpeech(messageIds);
      setStates((previous) => {
        const next = { ...previous };
        for (const entry of found) next[entry.message_id] = entry;
        return next;
      });
    } catch {
      /* a missed poll changes nothing; the next one will say the same */
    }
  }, [available]);

  const stop = useCallback(() => {
    if (audio.current) {
      audio.current.pause();
      audio.current = null;
    }
    setPlayingId(null);
  }, []);

  const play = useCallback(
    (messageId: string) => {
      stop();
      const element = new Audio(api.messageSpeechUrl(messageId));
      audio.current = element;
      setPlayingId(messageId);
      const finish = () => {
        if (audio.current === element) audio.current = null;
        setPlayingId((current) => (current === messageId ? null : current));
      };
      element.onended = finish;
      element.onerror = finish;
      element.play().catch(finish);
    },
    [stop]
  );

  /**
   * Asks for a message to be spoken, and plays it when it is.
   *
   * Already-synthesised audio plays at once -- that is what keeping it for a day
   * buys. Anything else is requested and watched.
   */
  const speak = useCallback(
    async (messageId: string, text: string) => {
      if (!available || !text.trim()) return;
      awaited.current.add(messageId);
      try {
        const entry = await api.requestMessageSpeech(messageId, text);
        setStates((previous) => ({ ...previous, [messageId]: entry }));
        if (entry.state === 'ready') {
          awaited.current.delete(messageId);
          play(messageId);
        } else {
          watching.current.add(messageId);
        }
      } catch {
        awaited.current.delete(messageId);
      }
    },
    [available, play]
  );

  // While anything is being synthesised, keep asking. This is what makes the
  // chip come back after a reload rather than the page forgetting.
  useEffect(() => {
    if (!available) return;
    const outstanding = Object.values(states)
      .filter((entry) => entry.state === 'pending')
      .map((entry) => entry.message_id);
    if (outstanding.length === 0) return;
    const timer = window.setInterval(() => sync(outstanding), 800);
    return () => window.clearInterval(timer);
  }, [available, anyPending, states, sync]);

  // Play what this page asked for, once it is ready. Nothing else.
  useEffect(() => {
    for (const entry of Object.values(states)) {
      if (entry.state !== 'ready' || !watching.current.has(entry.message_id)) continue;
      watching.current.delete(entry.message_id);
      if (awaited.current.delete(entry.message_id)) play(entry.message_id);
    }
  }, [states, play]);

  useEffect(() => {
    if (!available) stop();
  }, [available, stop]);

  useEffect(() => () => { audio.current?.pause(); }, []);

  return { speak, stop, sync, states, playingId };
}

/**
 * Recording a message instead of typing it.
 *
 * The transcript lands in the composer rather than being sent: dictation
 * mishears things, and a message you cannot read before it goes is a message you
 * cannot correct.
 */
export function useDictation(onTranscript: (text: string) => void) {
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);

  const stop = useCallback(() => {
    recorder.current?.stop();
    setIsRecording(false);
  }, []);

  const start = useCallback(async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const media = new MediaRecorder(stream);
      chunks.current = [];

      media.ondataavailable = (event) => {
        if (event.data.size) chunks.current.push(event.data);
      };
      media.onstop = async () => {
        // The microphone light stays on until every track is stopped, which
        // looks like the app is still listening.
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(chunks.current, { type: media.mimeType || 'audio/webm' });
        if (!blob.size) return;
        setIsTranscribing(true);
        try {
          const text = await api.listen(blob);
          if (text.trim()) onTranscript(text.trim());
        } catch (err) {
          setError(err instanceof Error ? err.message : String(err));
        } finally {
          setIsTranscribing(false);
        }
      };

      recorder.current = media;
      media.start();
      setIsRecording(true);
    } catch {
      setError('Kayak could not use the microphone. Check the browser permission.');
    }
  }, [onTranscript]);

  return { isRecording, isTranscribing, error, start, stop, clearError: () => setError(null) };
}
