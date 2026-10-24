import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '../../api/client';
import { MessageSpeech } from '../../types';
import { forgetMissing, withoutPending } from './speechState';

/**
 * Reading replies aloud, and dictating them.
 *
 * Both go through Kayak rather than the browser's own speech APIs: the models
 * are the ones running on this machine, so the voice is the voice the user chose
 * and the transcription is the model they started, not whatever the browser
 * happens to ship.
 */

/** Said when reading a message aloud fails and the server gave no better reason. */
export const SPEAK_FAILED = "Couldn't read this aloud. Try again.";

/** Said when the microphone is refused, which on a Mac is a System Settings switch. */
export const MICROPHONE_DENIED =
  "Kayak can't use the microphone. Allow it in System Settings → Privacy & Security → " +
  'Microphone, then try again.';

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
  /** Why the last read-aloud this page asked for did not happen, for showing once. */
  const [error, setError] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  // Messages this page asked for. Only these are played when they finish; one
  // synthesised in another tab, or before a reload, waits to be asked for.
  const awaited = useRef<Set<string>>(new Set());
  const watching = useRef<Set<string>>(new Set());

  const anyPending = Object.values(states).some((entry) => entry.state === 'pending');

  /**
   * Reads the state of these messages back from the server.
   *
   * A message asked about and not in the answer is forgotten here too. The server
   * keeps nothing in flight across a restart, so a "Preparing…" it no longer knows
   * about used to be polled for ever.
   */
  const sync = useCallback(async (messageIds: string[]) => {
    if (!available || messageIds.length === 0) return;
    try {
      const found = await api.getMessageSpeech(messageIds);
      const known = new Set(found.map((entry) => entry.message_id));
      const lost = messageIds.filter(
        (id) => !known.has(id) && (awaited.current.has(id) || watching.current.has(id))
      );
      for (const id of lost) {
        awaited.current.delete(id);
        watching.current.delete(id);
      }
      if (lost.length) setError(SPEAK_FAILED);
      setStates((previous) => forgetMissing(previous, messageIds, found));
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
      const fail = () => {
        // Only the clip still meant to be playing reports a failure; one replaced
        // by a newer request is not news.
        if (audio.current === element) setError("Couldn't play this aloud. Try again.");
        finish();
      };
      element.onended = finish;
      element.onerror = fail;
      element.play().catch(fail);
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
      setError(null);
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
      } catch (err) {
        // It used to fail silently: the button simply did nothing.
        awaited.current.delete(messageId);
        setError(errorMessage(err, SPEAK_FAILED));
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

  // Play what this page asked for, once it is ready. Nothing else. A request this
  // page made that failed says why, once.
  useEffect(() => {
    for (const entry of Object.values(states)) {
      if (!watching.current.has(entry.message_id)) continue;
      if (entry.state === 'ready') {
        watching.current.delete(entry.message_id);
        if (awaited.current.delete(entry.message_id)) play(entry.message_id);
      } else if (entry.state === 'failed') {
        watching.current.delete(entry.message_id);
        if (awaited.current.delete(entry.message_id)) setError(entry.error || SPEAK_FAILED);
      }
    }
  }, [states, play]);

  // With no voice model there is nothing to wait for: "Preparing…" chips are
  // dropped rather than left spinning until the model comes back.
  useEffect(() => {
    if (available) return;
    stop();
    awaited.current.clear();
    watching.current.clear();
    setStates((previous) => withoutPending(previous));
  }, [available, stop]);

  useEffect(() => () => { audio.current?.pause(); }, []);

  const clearError = useCallback(() => setError(null), []);

  return { speak, stop, sync, states, playingId, error, clearError };
}

/** The sentence for a microphone that could not be opened. */
function microphoneError(err: unknown): string {
  const name = err instanceof DOMException || err instanceof Error ? err.name : '';
  if (name === 'NotFoundError' || name === 'OverconstrainedError') {
    return "Kayak can't find a microphone. Connect one, then try again.";
  }
  if (name === 'NotReadableError' || name === 'AbortError') {
    return 'Another app is using the microphone. Close it, then try again.';
  }
  // Refused, or unavailable to the page at all: on a Mac both are the switch in
  // System Settings.
  return MICROPHONE_DENIED;
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
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);
  // Set once the component using this is gone: a recording still running then is
  // stopped and thrown away, not transcribed into a composer that no longer exists.
  const unmounted = useRef(false);

  const releaseMicrophone = useCallback(() => {
    // The microphone light stays on until every track is stopped, which looks
    // like the app is still listening.
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
  }, []);

  const stop = useCallback(() => {
    if (recorder.current && recorder.current.state !== 'inactive') recorder.current.stop();
    setIsRecording(false);
  }, []);

  const start = useCallback(async () => {
    setError(null);
    let media: MediaStream;
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new DOMException('', 'NotAllowedError');
      media = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      if (!unmounted.current) setError(microphoneError(err));
      return;
    }
    if (unmounted.current) {
      media.getTracks().forEach((track) => track.stop());
      return;
    }

    try {
      const mediaRecorder = new MediaRecorder(media);
      stream.current = media;
      chunks.current = [];

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size) chunks.current.push(event.data);
      };
      mediaRecorder.onstop = async () => {
        if (stream.current === media) releaseMicrophone();
        else media.getTracks().forEach((track) => track.stop());
        if (recorder.current === mediaRecorder) recorder.current = null;
        if (unmounted.current) return;
        const blob = new Blob(chunks.current, { type: mediaRecorder.mimeType || 'audio/webm' });
        if (!blob.size) return;
        setIsTranscribing(true);
        try {
          const text = await api.listen(blob);
          if (!unmounted.current && text.trim()) onTranscript(text.trim());
        } catch (err) {
          if (!unmounted.current) {
            setError(errorMessage(err, "Couldn't turn your recording into text. Try again."));
          }
        } finally {
          if (!unmounted.current) setIsTranscribing(false);
        }
      };

      recorder.current = mediaRecorder;
      mediaRecorder.start();
      setIsRecording(true);
    } catch {
      media.getTracks().forEach((track) => track.stop());
      stream.current = null;
      setError("Couldn't start recording. Try again.");
    }
  }, [onTranscript, releaseMicrophone]);

  // Leaving the conversation mid-recording used to leave the microphone on.
  useEffect(() => {
    unmounted.current = false;
    return () => {
      unmounted.current = true;
      if (recorder.current && recorder.current.state !== 'inactive') recorder.current.stop();
      recorder.current = null;
      releaseMicrophone();
    };
  }, [releaseMicrophone]);

  const clearError = useCallback(() => setError(null), []);

  return { isRecording, isTranscribing, error, start, stop, clearError };
}
