import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { AppSettings, ProviderCredential, SandboxAllocation, SandboxSettings } from '../../types';
import { AboutFooter } from './AboutFooter';
import { api, errorMessage } from '../../api/client';
import { useDialog } from '../../context/DialogContext';
import { useTheme, ThemeMode } from '../../context/ThemeContext';
import {
  CredentialEdit,
  SettingsDraft,
  buildSettingsUpdate,
  describeCredential,
  draftFromSettings,
  isCredentialDirty,
  isDraftDirty,
} from './settingsDraft';
import {
  Settings as SettingsIcon,
  Container,
  Key,
  CheckCircle2,
  Save,
  ExternalLink,
  Loader2,
  Sun,
  Moon,
  Monitor,
  Palette,
  ShieldCheck,
  ShieldAlert,
  Trash2,
  Undo2,
  XCircle,
} from 'lucide-react';

interface SettingsViewProps {
  /** Lets the shell warn before navigating away from unsaved credentials. */
  onDirtyChange?: (isDirty: boolean) => void;
}

export const SettingsView: React.FC<SettingsViewProps> = ({ onDirtyChange }) => {
  const dialog = useDialog();
  const { theme, setTheme } = useTheme();
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [draft, setDraft] = useState<SettingsDraft | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  const [loadError, setLoadError] = useState<string | null>(null);

  // Deliberately depends on nothing: this must run once, on mount. Reloading would
  // replace the draft, so anything that could retrigger it -- a context value that
  // changes identity, a new callback prop -- would throw away a half-typed key.
  const loadSettings = useCallback(async () => {
    try {
      const data = await api.getSettings();
      setSettings(data);
      setDraft(draftFromSettings(data));
      setLoadError(null);
    } catch (error) {
      setLoadError(errorMessage(error));
    }
  }, []);

  useEffect(() => {
    loadSettings();
  }, [loadSettings]);

  const isDirty = useMemo(
    () => (settings && draft ? isDraftDirty(draft, settings) : false),
    [settings, draft]
  );

  useEffect(() => {
    onDirtyChange?.(isDirty);
  }, [isDirty, onDirtyChange]);

  // Covers closing the tab or reloading; in-app navigation is guarded by the shell
  // through onDirtyChange.
  useEffect(() => {
    if (!isDirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [isDirty]);

  const updateCredential = (settingKey: string, edit: CredentialEdit) => {
    setDraft((prev) =>
      prev ? { ...prev, credentials: { ...prev.credentials, [settingKey]: edit } } : prev
    );
  };

  const updateSandbox = (sandbox: SandboxAllocation) => {
    setDraft((prev) => (prev ? { ...prev, sandbox } : prev));
  };

  const handleSave = async (event?: React.FormEvent) => {
    event?.preventDefault();
    if (!settings || !draft || !isDirty || isSaving) return;

    setIsSaving(true);
    setSaveSuccess(false);
    try {
      const response = await api.updateSettings(buildSettingsUpdate(draft, settings));
      // Reset from the server's answer rather than keeping what was typed: the form
      // otherwise held the plaintext key, compared it against the returned mask, and
      // stayed permanently "unsaved".
      setSettings(response.settings);
      setDraft(draftFromSettings(response.settings));
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch (error) {
      dialog.alert({
        title: 'Settings not saved',
        message: `${errorMessage(error)} Your changes are still here — you can try again.`,
        variant: 'danger',
      });
    } finally {
      setIsSaving(false);
    }
  };


  const themeOptions: { mode: ThemeMode; label: string; description: string; icon: React.ReactNode }[] = [
    {
      mode: 'light',
      label: 'Light',
      description: 'High-contrast daytime theme.',
      icon: <Sun className="w-4 h-4 text-amber-500" />,
    },
    {
      mode: 'dark',
      label: 'Dark',
      description: 'Low-light theme, easier on OLED displays.',
      icon: <Moon className="w-4 h-4 text-md-primary" />,
    },
    {
      mode: 'system',
      label: 'System',
      description: 'Follows your operating system appearance.',
      icon: <Monitor className="w-4 h-4 text-md-on-surface-variant" />,
    },
  ];

  if (loadError) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center bg-md-surface gap-3 p-8 text-center">
        <XCircle className="w-6 h-6 text-md-error" />
        <p className="text-xs text-md-on-surface-variant max-w-md leading-relaxed">{loadError}</p>
        <button
          type="button"
          onClick={loadSettings}
          className="px-4 py-2 rounded-xl bg-md-primary text-md-on-primary text-xs font-bold hover:opacity-90 transition-opacity cursor-pointer"
        >
          Try again
        </button>
      </div>
    );
  }

  if (!settings || !draft) {
    return (
      <div className="flex-1 flex items-center justify-center bg-md-surface text-md-on-surface-variant text-xs gap-2">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading settings...
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 bg-md-surface overflow-y-auto transition-colors">
      <div className="h-16 border-b border-md-outline-variant px-8 flex items-center justify-between bg-md-surface-container-low shrink-0 transition-colors sticky top-0 z-10">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded-xl bg-md-primary-container text-md-on-primary-container border border-md-outline-variant flex items-center justify-center shadow-2xs">
            <SettingsIcon className="w-4 h-4" />
          </div>
          <div>
            <h1 className="font-bold text-base text-md-on-surface flex items-center gap-2">
              <span>Platform Settings</span>
              {isDirty && (
                <span className="text-[10px] font-medium text-amber-800 dark:text-amber-200 bg-amber-100 dark:bg-amber-950/80 border border-amber-300 dark:border-amber-800/80 px-2 py-0.5 rounded-full flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" />
                  Unsaved changes
                </span>
              )}
            </h1>
            <p className="text-xs text-md-on-surface-variant">
              Appearance, model provider credentials, and what each agent sandbox may use.
            </p>
          </div>
        </div>

        <button
          onClick={handleSave}
          disabled={isSaving || !isDirty}
          className="px-5 py-2.5 rounded-xl text-xs font-bold bg-md-primary hover:opacity-90 disabled:opacity-40 disabled:hover:opacity-40 disabled:cursor-not-allowed text-md-on-primary flex items-center gap-1.5 shadow-xs transition-opacity focus:ring-2 focus:ring-md-primary cursor-pointer"
        >
          {isSaving ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : saveSuccess ? (
            <CheckCircle2 className="w-3.5 h-3.5 stroke-[2.5]" />
          ) : (
            <Save className="w-3.5 h-3.5" />
          )}
          <span>{saveSuccess ? 'Saved' : 'Save Settings'}</span>
        </button>
      </div>

      {/* Sections are separated by spacing alone, matching every other view. */}
      <div className="p-8 max-w-4xl w-full mx-auto space-y-8">
        {/* Kept as a callout rather than a section: it is a status warning, not a
            group of controls. */}
        <SecurityCard settings={settings} />

        {/* -------------------------------------------------------- Appearance */}
        <Section
          icon={<Palette className="w-4 h-4 text-md-primary" />}
          title="Appearance"
          subtitle="Stored in this browser and applied immediately — the Save button above does not apply to it."
        >
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {themeOptions.map((option) => {
              const isSelected = theme === option.mode;
              return (
                <button
                  key={option.mode}
                  type="button"
                  onClick={() => setTheme(option.mode)}
                  className={`p-4 rounded-xl border text-left transition-all cursor-pointer ${
                    isSelected
                      ? 'bg-md-primary-container border-2 border-md-primary ring-2 ring-md-primary/30 text-md-on-primary-container shadow-xs'
                      : 'bg-md-surface-container-low border-md-outline-variant text-md-on-surface hover:bg-md-surface-container-high hover:border-md-outline shadow-xs'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-2">
                      {option.icon}
                      <span className="font-semibold text-xs text-md-on-surface">{option.label}</span>
                    </div>
                    {isSelected && <span className="w-2 h-2 rounded-full bg-md-primary" />}
                  </div>
                  <p className="text-[11px] text-md-on-surface-variant leading-relaxed">
                    {option.description}
                  </p>
                </button>
              );
            })}
          </div>
        </Section>

        {/* ----------------------------------------------------------- Credentials */}
        <Section
          icon={<Key className="w-4 h-4 text-md-primary" />}
          title="Model provider credentials"
          subtitle="Stored on this Mac, never sent back to the browser."
        >
          <div className="space-y-4">
            {settings.providers.map((provider) => (
              <CredentialRow
                key={provider.id}
                provider={provider}
                edit={draft.credentials[provider.setting_key]}
                onEdit={(edit) => updateCredential(provider.setting_key, edit)}
              />
            ))}
          </div>
        </Section>

        {/* ------------------------------------------------------------- Sandboxes */}
        <Section
          icon={<Container className="w-4 h-4 text-md-primary" />}
          title="Agent sandboxes"
          subtitle="How much of Docker Desktop each conversation's container may use. A change reaches a conversation the next time it runs."
          action={
            // Always laid out, and only shown when there is something to reset:
            // appearing on the first slider move pushed the sliders down mid-drag.
            <button
              type="button"
              onClick={() => updateSandbox({ memory_gb: null, cpus: null })}
              className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border border-md-outline-variant text-[11px] font-semibold text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high transition-colors cursor-pointer ${
                draft.sandbox.memory_gb === null && draft.sandbox.cpus === null ? 'invisible' : ''
              }`}
            >
              <Undo2 className="w-3 h-3" /> Use recommended
            </button>
          }
        >
          <SandboxControls sandbox={settings.sandbox} draft={draft.sandbox} onChange={updateSandbox} />
        </Section>

        <AboutFooter />
      </div>
    </div>
  );
};

/**
 * A settings group, styled like the sections on the agent profile page: a small
 * uppercase heading over its content, with no card around it. Wrapping every group in
 * its own bordered box made the page read as a stack of unrelated widgets.
 */
const Section: React.FC<{
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}> = ({ icon, title, subtitle, action, children }) => (
  <section className="space-y-3">
    <div className="flex items-start justify-between gap-3 flex-wrap">
      <div className="min-w-0">
        <h3 className="text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
          {icon} {title}
        </h3>
        {subtitle && (
          <p className="text-[11px] text-md-on-surface-variant leading-relaxed mt-1">
            {subtitle}
          </p>
        )}
      </div>
      {action}
    </div>
    {children}
  </section>
);

/**
 * Memory and CPU sliders for the sandbox allocation, bounded by what Docker has.
 *
 * An untouched slider shows the recommendation and saves nothing, so a Mac given more
 * to Docker later gives its sandboxes more without anyone revisiting this page.
 */
const SandboxControls: React.FC<{
  sandbox: SandboxSettings;
  draft: SandboxAllocation;
  onChange: (next: SandboxAllocation) => void;
}> = ({ sandbox, draft, onChange }) => {
  if (!sandbox.total_memory_gb || !sandbox.total_cpus) {
    return (
      <p className="text-[11px] text-md-on-surface-variant leading-relaxed">
        Start Docker Desktop to choose how much each sandbox may use. Until you do, a
        sandbox gets half of Docker&apos;s memory and every core.
      </p>
    );
  }

  const maxMemory = Math.max(1, Math.floor(sandbox.total_memory_gb));
  const memory = Math.min(draft.memory_gb ?? sandbox.recommended_memory_gb ?? 1, maxMemory);
  const cpus = Math.min(draft.cpus ?? sandbox.recommended_cpus ?? 1, sandbox.total_cpus);

  return (
    <div className="space-y-5">
      <RangeField
        label={`Memory — ${memory} GB${draft.memory_gb === null ? ' (recommended)' : ''}`}
        hint="A command that needs more is stopped with an out-of-memory error. Docker Desktop's own total is set in its Settings > Resources."
        min={1}
        max={maxMemory}
        value={memory}
        onChange={(value) => onChange({ ...draft, memory_gb: value })}
        minLabel="1 GB"
        maxLabel={`${maxMemory} GB available to Docker`}
      />
      <RangeField
        label={`CPU — ${cpus} core${cpus === 1 ? '' : 's'}${draft.cpus === null ? ' (recommended)' : ''}`}
        hint="Cores one conversation's commands may use at once. Fewer keeps the Mac responsive while an agent builds or runs tests."
        min={1}
        max={sandbox.total_cpus}
        value={cpus}
        onChange={(value) => onChange({ ...draft, cpus: value })}
        minLabel="1 core"
        maxLabel={`${sandbox.total_cpus} cores available`}
      />
    </div>
  );
};

const RangeField: React.FC<{
  label: string;
  hint: string;
  min: number;
  max: number;
  value: number;
  onChange: (value: number) => void;
  minLabel: string;
  maxLabel: string;
}> = ({ label, hint, min, max, value, onChange, minLabel, maxLabel }) => (
  <div className="space-y-1.5">
    <label className="text-[11px] font-bold uppercase tracking-wider text-md-on-surface block">
      {label}
    </label>
    <p className="text-[11px] text-md-on-surface-variant leading-relaxed">{hint}</p>
    <input
      type="range"
      min={min}
      max={max}
      step={1}
      value={value}
      onChange={(event) => onChange(Number.parseInt(event.target.value, 10))}
      disabled={max <= min}
      className="w-full accent-md-primary cursor-pointer disabled:opacity-50"
    />
    <div className="flex items-center justify-between text-[10px] text-md-on-surface-variant font-mono">
      <span>{minLabel}</span>
      <span>{maxLabel}</span>
    </div>
  </div>
);

/**
 * The bind address and token state, stated plainly.
 *
 * Kayak gives agents shell access, so an unauthenticated server reachable off this
 * machine is the single most consequential configuration there is — and nothing in
 * the UI used to mention it.
 */
const SecurityCard: React.FC<{ settings: AppSettings }> = ({ settings }) => {
  const { security } = settings;
  const isRisky = !security.is_loopback && !security.auth_required;

  return (
    <div
      className={`rounded-2xl border p-5 flex items-start gap-3 transition-colors ${
        isRisky
          ? 'bg-rose-50 dark:bg-rose-950/40 border-rose-300 dark:border-rose-800/80'
          : 'bg-md-surface border-md-outline-variant shadow-xs'
      }`}
    >
      <div
        className={`w-9 h-9 rounded-xl border flex items-center justify-center shrink-0 ${
          isRisky
            ? 'bg-rose-100 dark:bg-rose-950/80 border-rose-300 dark:border-rose-800/80 text-rose-800 dark:text-rose-200'
            : 'bg-emerald-100 dark:bg-emerald-950/80 border-emerald-300 dark:border-emerald-800/80 text-emerald-800 dark:text-emerald-200'
        }`}
      >
        {isRisky ? <ShieldAlert className="w-5 h-5" /> : <ShieldCheck className="w-5 h-5" />}
      </div>

      <div className="min-w-0 space-y-1">
        <h2 className="text-sm font-bold text-md-on-surface">
          {isRisky ? 'This server is exposed' : 'Access'}
        </h2>
        <p className="text-[11px] text-md-on-surface-variant leading-relaxed">
          {security.warning ??
            `Reachable only from this Mac (${security.host}).${
              security.auth_required ? ' An access token is required.' : ''
            }`}
        </p>
        <div className="flex items-center gap-1.5 pt-1 flex-wrap">
          <Pill label={`Host ${security.host}`} />
          <Pill
            label={security.auth_required ? 'Token required' : 'No token'}
            tone={security.auth_required ? 'good' : security.is_loopback ? 'neutral' : 'bad'}
          />
        </div>
      </div>
    </div>
  );
};

const Pill: React.FC<{ label: string; tone?: 'good' | 'bad' | 'neutral' }> = ({
  label,
  tone = 'neutral',
}) => (
  <span
    className={`text-[10px] font-mono font-semibold px-2 py-0.5 rounded-full border ${
      tone === 'good'
        ? 'bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-200 border-emerald-300 dark:border-emerald-800/80'
        : tone === 'bad'
        ? 'bg-rose-100 dark:bg-rose-950/80 text-rose-800 dark:text-rose-200 border-rose-300 dark:border-rose-800/80'
        : 'bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant'
    }`}
  >
    {label}
  </span>
);

/**
 * One provider's credential.
 *
 * The input is empty unless you are replacing the key: an empty field means "leave
 * what is stored alone", which is what makes the stored state legible instead of
 * showing a mask you cannot meaningfully edit.
 */
const CredentialRow: React.FC<{
  provider: ProviderCredential;
  edit: CredentialEdit | undefined;
  onEdit: (edit: CredentialEdit) => void;
}> = ({ provider, edit, onEdit }) => {
  const pendingClear = edit?.kind === 'clear';
  const typed = edit?.kind === 'replace' ? edit.value : '';
  const isDirty = isCredentialDirty(edit, provider);

  return (
    <div className="p-4 rounded-xl border border-md-outline-variant bg-md-surface-container-lowest space-y-2.5">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-base leading-none">{provider.icon}</span>
          <span className="text-xs font-bold text-md-on-surface">{provider.name}</span>
          <span
            className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${
              isDirty
                ? 'bg-amber-100 dark:bg-amber-950/80 text-amber-800 dark:text-amber-200 border-amber-300 dark:border-amber-800/80'
                : provider.is_set
                ? 'bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-200 border-emerald-300 dark:border-emerald-800/80'
                : 'bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant'
            }`}
          >
            {describeCredential(provider, edit)}
          </span>
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          {pendingClear || (edit?.kind === 'replace' && edit.value) ? (
            <button
              type="button"
              onClick={() => onEdit({ kind: 'unchanged' })}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border border-md-outline-variant text-[11px] font-semibold text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high transition-colors cursor-pointer"
            >
              <Undo2 className="w-3 h-3" /> Undo
            </button>
          ) : (
            provider.is_set && (
              <button
                type="button"
                onClick={() => onEdit({ kind: 'clear' })}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border border-md-outline-variant text-[11px] font-semibold text-md-on-surface-variant hover:text-md-error hover:border-md-error/50 transition-colors cursor-pointer"
              >
                <Trash2 className="w-3 h-3" /> Remove
              </button>
            )
          )}
          <a
            href={provider.console_url}
            target="_blank"
            rel="noreferrer"
            className="text-[11px] text-md-primary hover:underline flex items-center gap-1 font-semibold px-1"
          >
            <span>Get key</span>
            <ExternalLink className="w-3 h-3" />
          </a>
        </div>
      </div>

      <input
        type="password"
        value={typed}
        disabled={pendingClear}
        onChange={(event) =>
          onEdit(event.target.value ? { kind: 'replace', value: event.target.value } : { kind: 'unchanged' })
        }
        placeholder={
          pendingClear
            ? 'Will be removed when you save'
            : provider.is_set
            ? 'Enter a new key to replace the stored one'
            : provider.key_hint
        }
        autoComplete="off"
        className="w-full bg-md-surface border border-md-outline-variant rounded-lg px-3 py-2 text-xs font-mono text-md-on-surface focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary disabled:opacity-50 transition-all"
      />
    </div>
  );
};

