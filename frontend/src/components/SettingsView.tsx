import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { AppSettings, ProviderCredential } from '../types';
import { api } from '../api/client';
import { useDialog } from '../context/DialogContext';
import { useTheme, ThemeMode } from '../context/ThemeContext';
import { ModelSelectorModal } from './ModelSelectorModal';
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
  Key,
  Server,
  Cpu,
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
  /** Opens the Local Models page, which owns the vLLM server. */
  onOpenLocalModels?: () => void;
}

export const SettingsView: React.FC<SettingsViewProps> = ({
  onDirtyChange,
  onOpenLocalModels,
}) => {
  const dialog = useDialog();
  const { theme, setTheme } = useTheme();
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [draft, setDraft] = useState<SettingsDraft | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [isModelPickerOpen, setIsModelPickerOpen] = useState(false);

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
      setLoadError(String(error));
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
        message: `${error}. Your changes are still here — you can try again.`,
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
              Provider credentials, local inference endpoint, and the default model for new agents.
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
          subtitle="Stored on this machine, never sent back to the browser."
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

        {/* ---------------------------------------------------------- Default model */}
        <Section
          icon={<Cpu className="w-4 h-4 text-md-primary" />}
          title="Default model"
          subtitle="Used by new agents and for generating conversation titles."
        >
          <div className="flex items-center gap-2">
            <code className="flex-1 bg-md-surface-container-lowest border border-md-outline-variant rounded-xl px-3.5 py-2 text-xs font-mono text-md-on-surface truncate">
              {draft.defaultModel || 'None selected'}
            </code>
            {/* Picked from the catalog rather than typed: a typo here is only
                discovered later, as a provider error on every new conversation. */}
            <button
              type="button"
              onClick={() => setIsModelPickerOpen(true)}
              className="px-4 py-2 rounded-xl border border-md-outline-variant bg-md-surface-container-low hover:bg-md-surface-container-high text-xs font-semibold text-md-on-surface transition-colors cursor-pointer shrink-0"
            >
              Change
            </button>
          </div>
        </Section>

        {/* --------------------------------------------------------- Local endpoint */}
        <Section
          icon={<Server className="w-4 h-4 text-md-primary" />}
          title="Local inference endpoint"
          subtitle="Where Kayak reaches the vLLM server."
          action={
            onOpenLocalModels && (
              <button
                type="button"
                onClick={onOpenLocalModels}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-bold bg-md-primary-container border border-md-outline-variant text-md-on-primary-container hover:opacity-90 transition-opacity shadow-2xs cursor-pointer"
              >
                <Server className="w-3.5 h-3.5" />
                <span>Open Local Models</span>
              </button>
            )
          }
        >
          <label className="block text-xs font-semibold text-md-on-surface mb-1.5">
            vLLM endpoint base URL
          </label>
          <input
            type="text"
            value={draft.vllmBase}
            onChange={(event) =>
              setDraft((prev) => (prev ? { ...prev, vllmBase: event.target.value } : prev))
            }
            // No hardcoded fallback: the previous placeholder suggested port 8000,
            // which is Kayak's own API, not the model server.
            placeholder={settings.VLLM_API_BASE}
            className="w-full bg-md-surface-container-lowest border border-md-outline-variant rounded-xl px-3.5 py-2 text-xs font-mono text-md-on-surface focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary transition-all"
          />
          <p className="text-[11px] text-md-on-surface-variant mt-1.5">
            Starting and stopping the server, and managing downloaded weights, lives on the
            Local Models page.
          </p>
        </Section>

        {/* ---------------------------------------------------------- Environment */}
        <Section
          icon={<Cpu className="w-4 h-4 text-md-primary" />}
          title="Environment"
          subtitle="Set through the environment, shown here for reference."
        >
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <ReadOnlyStat
              label="Docker sandbox"
              value={settings.DOCKER_AVAILABLE ? 'Available' : 'Host mode'}
              detail={
                settings.DOCKER_AVAILABLE
                  ? 'Conversations can run in an isolated container.'
                  : 'Docker socket not reachable; agents run in the host workspace.'
              }
              tone={settings.DOCKER_AVAILABLE ? 'good' : 'warn'}
            />
            <ReadOnlyStat
              label="Max tool steps per turn"
              value={String(settings.AGENT_MAX_ITERATIONS)}
              detail="KAYAK_AGENT_MAX_ITERATIONS"
            />
          </div>
        </Section>

      </div>

      <ModelSelectorModal
        isOpen={isModelPickerOpen}
        onClose={() => setIsModelPickerOpen(false)}
        selectedModel={draft.defaultModel}
        onSelectModel={(modelId) =>
          setDraft((prev) => (prev ? { ...prev, defaultModel: modelId } : prev))
        }
      />
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
            `Bound to ${security.host}, reachable only from this machine.${
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

const ReadOnlyStat: React.FC<{
  label: string;
  value: string;
  detail: string;
  tone?: 'good' | 'warn';
}> = ({ label, value, detail, tone }) => (
  <div className="p-3.5 rounded-xl border border-md-outline-variant bg-md-surface-container-lowest">
    <span className="text-[10px] text-md-on-surface-variant uppercase font-bold block">{label}</span>
    <span
      className={`text-xs font-semibold block mt-0.5 ${
        tone === 'warn' ? 'text-amber-800 dark:text-amber-200' : 'text-md-on-surface'
      }`}
    >
      {value}
    </span>
    <span className="text-[11px] text-md-on-surface-variant font-mono">{detail}</span>
  </div>
);
