import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AudioItem, AudioJob, Modality, Voice } from '../../types';
import { api, errorMessage } from '../../api/client';
import { useVLLMStatus, VLLM_LOADING_STATES } from '../../context/VLLMStatusContext';
import { formatBytes } from '../models/modelSizing';
import {
  AudioLines,
  AlertCircle,
  Check,
  Copy,
  Download,
  ChevronDown,
  ChevronUp,
  FileAudio,
  Loader2,
  Mic,
  X,
  Trash2,
  Type,
  Upload,
} from 'lucide-react';

export type Mode = 'synthesize' | 'transcribe';

/** Formats like 1:04. Clips are short; hours would be noise. */
function formatDuration(seconds?: number | null): string {
  if (!seconds || seconds <= 0) return '';
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(Math.round(seconds % 60)).padStart(2, '0')}`;
}

function formatWhen(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleString();
}

export interface AudioViewProps {
  /** Sends the user to Local Models, the one place servers are started. */
  onOpenModels: () => void;
  /** Which half of the page is showing. Comes from the URL. */
  mode: Mode;
  onSelectMode: (mode: Mode) => void;
}

/** States in which a job is still going to produce something. */
const ACTIVE_JOB_STATES: AudioJob['state'][] = ['queued', 'running'];

export const AudioView: React.FC<AudioViewProps> = ({
  onOpenModels,
  mode,
  onSelectMode,
}) => {
  const [items, setItems] = useState<AudioItem[]>([]);
  const [jobs, setJobs] = useState<AudioJob[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refreshItems = useCallback(async () => {
    try {
      setItems(await api.listAudioItems());
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  const refreshJobs = useCallback(async () => {
    try {
      return await api.listAudioJobs().then((next: AudioJob[]) => {
        setJobs(next);
        return next;
      });
    } catch {
      // A missed poll is not worth a banner; the next one will say the same thing.
      return null;
    }
  }, []);

  useEffect(() => {
    refreshItems();
    refreshJobs();
  }, [refreshItems, refreshJobs]);

  // The queue lives on the server, so the page reads it rather than holding it.
  // Polling only while something is in flight keeps an idle tab quiet, and the
  // one poll after the last job finishes is what notices the final clip.
  const hasActiveJobs = jobs.some((job) => ACTIVE_JOB_STATES.includes(job.state));
  const doneCount = jobs.filter((job) => job.state === 'done').length;
  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = window.setInterval(refreshJobs, 1000);
    return () => window.clearInterval(timer);
  }, [hasActiveJobs, refreshJobs]);

  // A finished job means a new clip, which lives in the other list.
  useEffect(() => {
    refreshItems();
  }, [doneCount, refreshItems]);

  const dismissJob = async (job: AudioJob) => {
    // Dropped from the list first: waiting for the request makes clearing a
    // handful of jobs feel broken.
    setJobs((previous) => previous.filter((entry) => entry.id !== job.id));
    try {
      await api.cancelAudioJob(job.id);
    } catch (err) {
      setError(errorMessage(err));
    }
    refreshJobs();
  };

  const handleDelete = async (item: AudioItem) => {
    // Removed from the list first: the request is a formality, and waiting for it
    // makes deleting a dozen clips feel broken.
    setItems((previous) => previous.filter((entry) => entry.id !== item.id));
    try {
      await api.deleteAudioItem(item.id);
    } catch (err) {
      setError(errorMessage(err));
      refreshItems();
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 bg-md-surface overflow-hidden font-sans transition-colors">
      <div className="h-16 border-b border-md-outline-variant px-8 flex items-center justify-between bg-md-surface-container-low shrink-0 transition-colors">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded-xl bg-md-primary-container text-md-on-primary-container border border-md-outline-variant flex items-center justify-center shadow-2xs">
            <AudioLines className="w-4 h-4" />
          </div>
          <div>
            <h2 className="font-bold text-sm text-md-on-surface">Audio</h2>
            <p className="text-xs text-md-on-surface-variant">
              Turn text into speech, and recordings into text, on this machine
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1 p-1 rounded-xl bg-md-surface-container-high border border-md-outline-variant">
          <ModeButton
            active={mode === 'synthesize'}
            onClick={() => onSelectMode('synthesize')}
            icon={<Type className="w-3 h-3" />}
            label="Synthesize"
          />
          <ModeButton
            active={mode === 'transcribe'}
            onClick={() => onSelectMode('transcribe')}
            icon={<Mic className="w-3 h-3" />}
            label="Transcribe"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-8 bg-md-surface">
        {error && (
          <div className="p-3.5 rounded-xl bg-rose-50 dark:bg-rose-950/40 border border-rose-300 dark:border-rose-800/80 text-rose-900 dark:text-rose-100 text-[11px] leading-relaxed flex items-start gap-2">
            <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <p className="flex-1">{error}</p>
            <button
              type="button"
              onClick={() => setError(null)}
              className="text-[11px] font-bold underline cursor-pointer"
            >
              Dismiss
            </button>
          </div>
        )}

        {mode === 'synthesize' ? (
          <SynthesizePanel
            onOpenModels={onOpenModels}
            onQueued={refreshJobs}
            onError={setError}
            jobs={jobs.filter((job) => job.kind === 'speech')}
            clips={items.filter((item) => item.kind === 'clip')}
            onDelete={handleDelete}
            onDismissJob={dismissJob}
          />
        ) : (
          <TranscribePanel
            onOpenModels={onOpenModels}
            onQueued={refreshJobs}
            onError={setError}
            jobs={jobs.filter((job) => job.kind === 'transcription')}
            transcripts={items.filter((item) => item.kind === 'transcript')}
            onDelete={handleDelete}
            onDismissJob={dismissJob}
          />
        )}
      </div>
    </div>
  );
};

const ModeButton: React.FC<{
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}> = ({ active, onClick, icon, label }) => (
  <button
    type="button"
    onClick={onClick}
    className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold transition-colors cursor-pointer ${
      active
        ? 'bg-md-primary text-md-on-primary shadow-2xs'
        : 'text-md-on-surface-variant hover:text-md-on-surface'
    }`}
  >
    {icon}
    {label}
  </button>
);

/**
 * The state of one audio server, and what to do when it is not running.
 *
 * Models are started from Local Models and nowhere else: a second place to start
 * them would be a second surface to keep truthful.
 */
const ServerState: React.FC<{
  modality: Modality;
  what: string;
  onOpenModels: () => void;
}> = ({ modality, what, onOpenModels }) => {
  const { statuses } = useVLLMStatus();
  const status = statuses[modality];
  const isReady = status?.state === 'ready';
  const isBusy = Boolean(status && VLLM_LOADING_STATES.includes(status.state));

  if (isReady) {
    return (
      <div className="flex items-center gap-2 text-[11px] text-md-on-surface-variant">
        <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-200 border-emerald-300 dark:border-emerald-800/80">
          ONLINE
        </span>
        <span className="font-mono">{status?.model_id}</span>
      </div>
    );
  }

  if (isBusy) {
    return (
      <div className="flex items-center gap-2 text-[11px] text-md-on-surface-variant">
        <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border bg-amber-100 dark:bg-amber-950/80 text-amber-800 dark:text-amber-200 border-amber-300 dark:border-amber-800/80">
          <Loader2 className="w-2.5 h-2.5 animate-spin" />
          STARTING
        </span>
        <span className="font-mono">{status?.model_id}</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 text-[11px] text-md-on-surface-variant">
      <span>No {what} model is running.</span>
      <button
        type="button"
        onClick={onOpenModels}
        className="text-md-primary font-bold hover:underline cursor-pointer"
      >
        Start one
      </button>
    </div>
  );
};

const SynthesizePanel: React.FC<{
  onOpenModels: () => void;
  onQueued: () => void;
  onError: (message: string) => void;
  jobs: AudioJob[];
  clips: AudioItem[];
  onDelete: (item: AudioItem) => void;
  onDismissJob: (job: AudioJob) => void;
}> = ({ onOpenModels, onQueued, onError, jobs, clips, onDelete, onDismissJob }) => {
  const { statuses } = useVLLMStatus();
  const speechStatus = statuses.speech;
  const isReady = speechStatus?.state === 'ready';

  const [text, setText] = useState('');
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voice, setVoice] = useState<string>('');
  const [speed, setSpeed] = useState(1);
  const [isQueueing, setIsQueueing] = useState(false);

  // Voices belong to the loaded model, so they are re-read whenever it changes
  // rather than once: a list from the previous model offers voices the current
  // one has never heard of.
  useEffect(() => {
    let cancelled = false;
    if (!isReady) {
      setVoices([]);
      return;
    }
    api
      .getVoices()
      .then((response) => {
        if (cancelled) return;
        setVoices(response.voices);
        setVoice((current) => {
          // A voice the user picked survives a refetch of the same model's list.
          if (response.voices.some((entry) => entry.id === current)) return current;
          // Otherwise open on the server's own default rather than whichever
          // voice happens to sort first.
          const preferred = response.default_voice;
          if (preferred && response.voices.some((entry) => entry.id === preferred)) {
            return preferred;
          }
          return response.voices[0]?.id ?? '';
        });
      })
      .catch(() => {
        if (!cancelled) setVoices([]);
      });
    return () => {
      cancelled = true;
    };
  }, [isReady, speechStatus?.model_id]);

  const characters = text.trim().length;

  const handleGenerate = async () => {
    if (!text.trim() || isQueueing) return;
    setIsQueueing(true);
    try {
      await api.synthesizeSpeech({
        text,
        voice: voice || null,
        speed,
        response_format: 'wav',
      });
      // Cleared so the next thing can be queued straight away, which is the
      // point of a queue: the box is free while the last one is still speaking.
      setText('');
      onQueued();
    } catch (err) {
      onError(errorMessage(err));
    } finally {
      setIsQueueing(false);
    }
  };

  // Only work still in flight, or a failure worth reporting. A finished job has
  // become a clip in this same list, and leaving its row up rendered the clip
  // twice -- the second one wearing a "waiting" chip for work already done.
  const visibleJobs = jobs.filter(
    (job) => ACTIVE_JOB_STATES.includes(job.state) || job.state === 'failed'
  );

  const grouped = useMemo(() => groupVoicesByLanguage(voices), [voices]);

  return (
    <>
      <section className="space-y-3">
        <SectionHeading
          icon={<AudioLines className="w-3.5 h-3.5 text-md-primary" />}
          title="Speech model"
        />
        <ServerState modality="speech" what="speech" onOpenModels={onOpenModels} />
      </section>

      <section className="space-y-3">
        <SectionHeading
          icon={<Type className="w-3.5 h-3.5 text-md-primary" />}
          title="Text"
          note={characters ? `${characters.toLocaleString()} characters` : undefined}
        />

        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="Paste or type anything to be spoken. Long text is split into sentences automatically."
          rows={8}
          className="w-full bg-md-surface-container-lowest border border-md-outline-variant rounded-xl px-4 py-3 text-xs text-md-on-surface placeholder:text-md-on-surface-variant/70 focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary shadow-xs transition-colors resize-y font-sans leading-relaxed"
        />

        <div className="flex items-end gap-3 flex-wrap">
          {voices.length > 0 && (
            <label className="flex flex-col gap-1">
              <span className="text-[10px] font-bold uppercase tracking-wider text-md-on-surface-variant">
                Voice
              </span>
              <select
                value={voice}
                onChange={(event) => setVoice(event.target.value)}
                className="bg-md-surface-container-lowest border border-md-outline-variant rounded-xl px-3 py-2 text-xs text-md-on-surface focus:outline-none focus:border-md-primary cursor-pointer"
              >
                {grouped.map(([language, entries]) => (
                  <optgroup key={language} label={language}>
                    {entries.map((entry) => (
                      <option key={entry.id} value={entry.id}>
                        {entry.label}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
          )}

          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-bold uppercase tracking-wider text-md-on-surface-variant">
              Speed · {speed.toFixed(2)}×
            </span>
            <input
              type="range"
              min={0.5}
              max={2}
              step={0.05}
              value={speed}
              onChange={(event) => setSpeed(Number(event.target.value))}
              className="w-40 accent-md-primary cursor-pointer"
            />
          </label>

          <button
            type="button"
            onClick={handleGenerate}
            disabled={!isReady || !text.trim() || isQueueing}
            className="px-5 py-2.5 rounded-xl bg-md-primary hover:opacity-90 disabled:opacity-40 text-md-on-primary text-xs font-bold flex items-center gap-1.5 transition-opacity shadow-xs cursor-pointer"
          >
            {isQueueing ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <AudioLines className="w-3.5 h-3.5" />
            )}
            <span>Generate speech</span>
          </button>
        </div>
      </section>

      <section className="space-y-3">
        <SectionHeading
          icon={<FileAudio className="w-3.5 h-3.5 text-md-primary" />}
          title="Clips"
          note={clips.length ? `${clips.length} saved` : undefined}
        />

        {visibleJobs.length === 0 && clips.length === 0 ? (
          <p className="text-[11px] text-md-on-surface-variant">
            Nothing generated yet. Clips are saved on this machine and stay here until
            you delete them.
          </p>
        ) : (
          <div className="space-y-2.5">
            {/* Work in flight sits above the library and turns into a clip in
                place, rather than in a section of its own that empties itself. */}
            {visibleJobs.map((job) => (
              <PendingRow key={job.id} job={job} onDismiss={onDismissJob} />
            ))}
            {clips.map((clip) => (
              <ClipRow key={clip.id} clip={clip} onDelete={onDelete} />
            ))}
          </div>
        )}
      </section>
    </>
  );
};

/**
 * Progress as a ring, sized to sit inside a chip.
 *
 * Two modes, because there are two honest states: a job waiting for the server has
 * no progress to report and spins, and one being spoken has a real fraction. An
 * indeterminate bar that creeps forward on a timer would be inventing a number.
 */
const ProgressRing: React.FC<{ fraction?: number }> = ({ fraction }) => {
  const radius = 5;
  const circumference = 2 * Math.PI * radius;

  if (fraction === undefined) {
    return (
      <svg viewBox="0 0 14 14" className="w-3 h-3 animate-spin" aria-hidden="true">
        <circle
          cx="7"
          cy="7"
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeDasharray={`${circumference * 0.3} ${circumference}`}
          opacity="0.9"
        />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 14 14" className="w-3 h-3 -rotate-90" aria-hidden="true">
      <circle cx="7" cy="7" r={radius} fill="none" stroke="currentColor" strokeWidth="2" opacity="0.25" />
      <circle
        cx="7"
        cy="7"
        r={radius}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - Math.min(1, Math.max(0, fraction)))}
        className="transition-[stroke-dashoffset] duration-500 ease-out"
      />
    </svg>
  );
};

/**
 * Text that can be opened out.
 *
 * Clamped to three lines so a list of clips stays scannable, with the toggle shown
 * only when there is actually something hidden -- measured rather than guessed from
 * the character count, which is wrong for anything but average line lengths.
 */
const ExpandableText: React.FC<{ children: string }> = ({ children }) => {
  const [expanded, setExpanded] = useState(false);
  const [clamped, setClamped] = useState(false);
  const ref = useRef<HTMLParagraphElement | null>(null);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    setClamped(element.scrollHeight > element.clientHeight + 1);
  }, [children]);

  return (
    <div className="flex-1 min-w-0">
      <p
        ref={ref}
        className={`text-xs text-md-on-surface leading-relaxed whitespace-pre-wrap ${
          expanded ? '' : 'line-clamp-3'
        }`}
      >
        {children}
      </p>
      {(clamped || expanded) && (
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="mt-1 inline-flex items-center gap-0.5 text-[10px] font-bold text-md-primary hover:underline cursor-pointer"
        >
          {expanded ? (
            <>
              <ChevronUp className="w-3 h-3" /> Show less
            </>
          ) : (
            <>
              <ChevronDown className="w-3 h-3" /> Show more
            </>
          )}
        </button>
      )}
    </div>
  );
};

/**
 * A synthesis that has not produced its clip yet.
 *
 * Rendered in the same list as finished clips, in the same shape, so a submission
 * turns into a clip where it already sits rather than moving between sections.
 */
const PendingRow: React.FC<{
  job: AudioJob;
  onDismiss: (job: AudioJob) => void;
}> = ({ job, onDismiss }) => {
  const isRunning = job.state === 'running';
  const isFailed = job.state === 'failed';

  return (
    <div
      className={`p-3.5 rounded-2xl border shadow-xs space-y-2.5 transition-colors ${
        isFailed
          ? 'bg-rose-50 dark:bg-rose-950/30 border-rose-300 dark:border-rose-800/80'
          : 'bg-md-surface border-md-outline-variant'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        {/* A transcription has no text of its own until it finishes, so the
            recording's name is what identifies it while it waits. */}
        {job.kind === 'transcription' ? (
          <p className="text-xs text-md-on-surface leading-relaxed flex-1 min-w-0 truncate font-mono">
            {job.source_filename}
          </p>
        ) : (
          <ExpandableText>{job.text}</ExpandableText>
        )}

        <div className="flex items-center gap-1.5 shrink-0">
          {isFailed ? (
            <span className="inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border bg-rose-100 dark:bg-rose-950/80 text-rose-800 dark:text-rose-200 border-rose-300 dark:border-rose-800/80">
              FAILED
            </span>
          ) : isRunning ? (
            <span className="inline-flex items-center gap-1.5 text-[10px] font-bold px-2 py-0.5 rounded-full border bg-md-primary-container text-md-on-primary-container border-md-primary/40">
              <ProgressRing
                fraction={job.chunks_total > 0 ? job.chunks_done / job.chunks_total : 0}
              />
              {job.chunks_total > 0 ? `${job.chunks_done}/${job.chunks_total}` : '0/0'}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 text-[10px] font-bold px-2 py-0.5 rounded-full border bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant">
              <ProgressRing />
              WAITING
            </span>
          )}

          {!isRunning && (
            <button
              type="button"
              onClick={() => onDismiss(job)}
              title={isFailed ? 'Dismiss' : 'Remove from the queue'}
              className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high transition-colors cursor-pointer"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {isFailed && job.error && (
        <p className="text-[10px] text-rose-900 dark:text-rose-100 leading-relaxed">
          {job.error}
        </p>
      )}
    </div>
  );
};

const ClipRow: React.FC<{ clip: AudioItem; onDelete: (item: AudioItem) => void }> = ({
  clip,
  onDelete,
}) => (
  <div className="p-3.5 rounded-2xl border border-md-outline-variant bg-md-surface shadow-xs space-y-2.5">
    <div className="flex items-start justify-between gap-3">
      <ExpandableText>{clip.text}</ExpandableText>
      <div className="flex items-center gap-1 shrink-0">
        <a
          href={api.audioFileUrl(clip.id)}
          download={clip.filename ?? undefined}
          className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high transition-colors"
          title="Download"
        >
          <Download className="w-3.5 h-3.5" />
        </a>
        <button
          type="button"
          onClick={() => onDelete(clip)}
          className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors cursor-pointer"
          title="Delete"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>

    <audio controls preload="none" src={api.audioFileUrl(clip.id)} className="w-full h-8" />

    <div className="flex items-center gap-2 text-[10px] text-md-on-surface-variant flex-wrap">
      <span className="font-mono">{clip.model_id}</span>
      {clip.voice && <Tag>{clip.voice}</Tag>}
      {clip.duration_seconds ? <Tag>{formatDuration(clip.duration_seconds)}</Tag> : null}
      <Tag>{formatBytes(clip.size_bytes)}</Tag>
      <span className="ml-auto">{formatWhen(clip.created_at)}</span>
    </div>
  </div>
);

const TranscribePanel: React.FC<{
  onOpenModels: () => void;
  onQueued: () => void;
  onError: (message: string) => void;
  jobs: AudioJob[];
  transcripts: AudioItem[];
  onDelete: (item: AudioItem) => void;
  onDismissJob: (job: AudioJob) => void;
}> = ({ onOpenModels, onQueued, onError, jobs, transcripts, onDelete, onDismissJob }) => {
  const { statuses } = useVLLMStatus();
  const isReady = statuses.transcription?.state === 'ready';

  const [files, setFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isQueueing, setIsQueueing] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Added rather than replaced, so a second drop extends the batch instead of
  // discarding what was already chosen. Same name and size means the same file.
  const addFiles = (incoming: FileList | null) => {
    if (!incoming?.length) return;
    setFiles((previous) => {
      const seen = new Set(previous.map((file) => `${file.name}:${file.size}`));
      const added = Array.from(incoming).filter(
        (file) => !seen.has(`${file.name}:${file.size}`)
      );
      return [...previous, ...added];
    });
  };

  const handleTranscribe = async () => {
    if (!files.length || isQueueing) return;
    setIsQueueing(true);
    try {
      await api.transcribeAudio(files);
      setFiles([]);
      if (inputRef.current) inputRef.current.value = '';
      onQueued();
    } catch (err) {
      onError(errorMessage(err));
    } finally {
      setIsQueueing(false);
    }
  };

  const visibleJobs = jobs.filter(
    (job) => ACTIVE_JOB_STATES.includes(job.state) || job.state === 'failed'
  );

  return (
    <>
      <section className="space-y-3">
        <SectionHeading
          icon={<Mic className="w-3.5 h-3.5 text-md-primary" />}
          title="Transcription model"
        />
        <ServerState
          modality="transcription"
          what="transcription"
          onOpenModels={onOpenModels}
        />
      </section>

      <section className="space-y-3">
        <SectionHeading
          icon={<Upload className="w-3.5 h-3.5 text-md-primary" />}
          title="Recordings"
          note={files.length ? `${files.length} selected` : undefined}
        />

        <div
          onDragOver={(event) => {
            event.preventDefault();
            setIsDragging(true);
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setIsDragging(false);
            addFiles(event.dataTransfer.files);
          }}
          onClick={() => inputRef.current?.click()}
          className={`p-8 rounded-2xl border border-dashed text-center cursor-pointer transition-colors ${
            isDragging
              ? 'border-md-primary bg-md-primary-container/40'
              : 'border-md-outline-variant bg-md-surface-container-lowest hover:bg-md-surface-container'
          }`}
        >
          <input
            ref={inputRef}
            type="file"
            accept="audio/*,video/*"
            multiple
            className="hidden"
            onChange={(event) => addFiles(event.target.files)}
          />
          <Upload className="w-5 h-5 text-md-on-surface-variant mx-auto mb-2" />
          <p className="text-xs text-md-on-surface-variant">
            Drop audio files here, or click to choose them
          </p>
        </div>

        {files.length > 0 && (
          <div className="space-y-1.5">
            {files.map((file) => (
              <div
                key={`${file.name}:${file.size}`}
                className="flex items-center justify-between gap-3 px-3 py-2 rounded-xl border border-md-outline-variant bg-md-surface"
              >
                <span className="text-[11px] text-md-on-surface truncate">
                  {file.name}
                  <span className="text-md-on-surface-variant"> · {formatBytes(file.size)}</span>
                </span>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    setFiles((previous) => previous.filter((entry) => entry !== file));
                  }}
                  className="p-1 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high transition-colors cursor-pointer shrink-0"
                  title="Remove"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}

        <button
          type="button"
          onClick={handleTranscribe}
          disabled={!isReady || !files.length || isQueueing}
          className="px-5 py-2.5 rounded-xl bg-md-primary hover:opacity-90 disabled:opacity-40 text-md-on-primary text-xs font-bold flex items-center gap-1.5 transition-opacity shadow-xs cursor-pointer"
        >
          {isQueueing ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Mic className="w-3.5 h-3.5" />
          )}
          <span>
            {files.length > 1 ? `Transcribe ${files.length} files` : 'Transcribe'}
          </span>
        </button>
      </section>

      <section className="space-y-3">
        <SectionHeading
          icon={<Type className="w-3.5 h-3.5 text-md-primary" />}
          title="Transcripts"
          note={transcripts.length ? `${transcripts.length} saved` : undefined}
        />

        {visibleJobs.length === 0 && transcripts.length === 0 ? (
          <p className="text-[11px] text-md-on-surface-variant">
            Nothing transcribed yet. The recording itself is not kept — only the text.
          </p>
        ) : (
          <div className="space-y-2.5">
            {visibleJobs.map((job) => (
              <PendingRow key={job.id} job={job} onDismiss={onDismissJob} />
            ))}
            {transcripts.map((item) => (
              <TranscriptRow key={item.id} item={item} onDelete={onDelete} />
            ))}
          </div>
        )}
      </section>
    </>
  );
};

const TranscriptRow: React.FC<{
  item: AudioItem;
  onDelete: (item: AudioItem) => void;
}> = ({ item, onDelete }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(item.text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* a browser that refuses clipboard access is not worth an error banner */
    }
  };

  return (
    <div className="p-3.5 rounded-2xl border border-md-outline-variant bg-md-surface shadow-xs space-y-2.5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs text-md-on-surface leading-relaxed flex-1 whitespace-pre-wrap">
          {item.text || <span className="text-md-on-surface-variant">(silence)</span>}
        </p>
        <div className="flex items-center gap-1 shrink-0">
          <button
            type="button"
            onClick={handleCopy}
            className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high transition-colors cursor-pointer"
            title="Copy"
          >
            {copied ? (
              <Check className="w-3.5 h-3.5 text-emerald-600" />
            ) : (
              <Copy className="w-3.5 h-3.5" />
            )}
          </button>
          <button
            type="button"
            onClick={() => onDelete(item)}
            className="p-1.5 rounded-lg text-md-on-surface-variant hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/40 transition-colors cursor-pointer"
            title="Delete"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2 text-[10px] text-md-on-surface-variant flex-wrap">
        {item.source_filename && <Tag>{item.source_filename}</Tag>}
        {item.language && <Tag>{item.language}</Tag>}
        <span className="font-mono">{item.model_id}</span>
        <span className="ml-auto">{formatWhen(item.created_at)}</span>
      </div>
    </div>
  );
};

const Tag: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span className="px-1.5 py-0.5 rounded border border-md-outline-variant bg-md-surface-container-high text-md-on-surface">
    {children}
  </span>
);

const SectionHeading: React.FC<{ icon: React.ReactNode; title: string; note?: string }> = ({
  icon,
  title,
  note,
}) => (
  <div className="flex items-center justify-between gap-3 flex-wrap">
    <h4 className="text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
      {icon} {title}
    </h4>
    {note && <span className="text-[11px] text-md-on-surface-variant">{note}</span>}
  </div>
);

/**
 * Groups voices by the language they speak.
 *
 * Kokoro alone offers 54 across nine languages, which as one flat list is unusable.
 * The grouping comes from what the server reported, so a model with no languages
 * simply yields one group.
 */
export function groupVoicesByLanguage(voices: Voice[]): Array<[string, Voice[]]> {
  const groups = new Map<string, Voice[]>();
  for (const voice of voices) {
    const key = voice.language || 'Voices';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(voice);
  }
  return Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b));
}
