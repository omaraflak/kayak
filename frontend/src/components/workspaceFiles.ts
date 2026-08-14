import { TurnToolCall } from './conversationTurns';

/**
 * Pure logic behind the workspace panel and file rendering in chat: path
 * handling, choosing how to preview a file, and finding the files a turn wrote.
 */

/** How a file should be presented when opened. */
export type FileKind = 'image' | 'pdf' | 'text' | 'binary';

const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico', 'avif']);

const TEXT_EXTENSIONS = new Set([
  'txt', 'md', 'markdown', 'log', 'csv', 'tsv', 'json', 'yaml', 'yml', 'toml',
  'ini', 'cfg', 'conf', 'xml', 'html', 'htm', 'css', 'js', 'jsx', 'ts', 'tsx',
  'py', 'rb', 'go', 'rs', 'java', 'c', 'h', 'cpp', 'hpp', 'cs', 'sh', 'bash',
  'zsh', 'sql', 'r', 'php', 'swift', 'kt', 'scala', 'lua', 'pl', 'env',
  'gitignore', 'dockerfile', 'makefile', 'lock',
]);

/** Classifies a file by name for preview purposes. */
export function fileKind(name: string): FileKind {
  const base = name.toLowerCase().split('/').pop() || '';
  const extension = base.includes('.') ? base.split('.').pop() || '' : base;
  if (IMAGE_EXTENSIONS.has(extension)) return 'image';
  if (extension === 'pdf') return 'pdf';
  if (TEXT_EXTENSIONS.has(extension)) return 'text';
  return 'binary';
}

/** Joins a directory path and an entry name into a normalised relative path. */
export function joinPath(parent: string, name: string): string {
  if (!parent || parent === '.') return name;
  return `${parent.replace(/\/+$/, '')}/${name}`;
}

/** Parent of a relative path; '.' at the root. */
export function parentPath(path: string): string {
  const cleaned = path.replace(/\/+$/, '');
  const index = cleaned.lastIndexOf('/');
  return index === -1 ? '.' : cleaned.slice(0, index);
}

/** Breadcrumb segments for a relative path, each with the path to jump to. */
export function breadcrumbs(path: string): Array<{ label: string; path: string }> {
  const crumbs = [{ label: 'workspace', path: '.' }];
  if (!path || path === '.') return crumbs;

  let cursor = '';
  for (const segment of path.split('/').filter(Boolean)) {
    cursor = cursor ? `${cursor}/${segment}` : segment;
    crumbs.push({ label: segment, path: cursor });
  }
  return crumbs;
}

/** Human-readable file size. */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

/**
 * Maps a path the agent mentioned to a workspace-relative one, or null when it
 * points elsewhere.
 *
 * The agent sees its workspace mounted at /workspace, so transcripts reference
 * both `plot.png` and `/workspace/plot.png`; either should resolve to the file.
 * External URLs and unknown absolute paths are left alone.
 */
export function workspaceRelativePath(src: string | null | undefined): string | null {
  if (!src) return null;
  if (/^[a-z][a-z0-9+.-]*:/i.test(src)) return null; // http:, https:, data:, mailto:...
  if (src.startsWith('#') || src.startsWith('//')) return null;

  let path = src;
  if (path.startsWith('/workspace/')) path = path.slice('/workspace/'.length);
  else if (path === '/workspace') path = '.';
  else if (path.startsWith('/')) return null;

  path = path.replace(/^\.\//, '');
  return path || null;
}

/** Prism grammar for a file, so previews get the same highlighting as chat code. */
const PRISM_BY_EXTENSION: Record<string, string> = {
  py: 'python',
  js: 'javascript',
  jsx: 'javascript',
  mjs: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  json: 'json',
  md: 'markdown',
  markdown: 'markdown',
  html: 'markup',
  htm: 'markup',
  xml: 'markup',
  svg: 'markup',
  css: 'css',
  sh: 'bash',
  bash: 'bash',
  zsh: 'bash',
  yml: 'yaml',
  yaml: 'yaml',
  sql: 'sql',
};

export function prismLanguageForFile(name: string): string {
  const base = name.toLowerCase().split('/').pop() || '';
  const extension = base.includes('.') ? base.split('.').pop() || '' : '';
  return PRISM_BY_EXTENSION[extension] || 'text';
}

/** Width bounds for the container side panel. */
export const PANEL_MIN_WIDTH = 300;
export const PANEL_MAX_WIDTH = 800;
export const PANEL_DEFAULT_WIDTH = 400;

/** Clamps a panel width into its usable range. */
export function clampPanelWidth(width: number): number {
  return Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, width));
}

/**
 * Restores a persisted panel width, falling back to the default when the
 * stored value is missing or nonsense (corrupt storage, older formats).
 */
export function restorePanelWidth(raw: string | null): number {
  const parsed = Number(raw);
  if (!raw || !Number.isFinite(parsed) || parsed <= 0) return PANEL_DEFAULT_WIDTH;
  return clampPanelWidth(parsed);
}

/** Tools whose calls leave a file behind that is worth surfacing in the chat. */
const FILE_WRITING_TOOLS = new Set(['write_file', 'edit_file']);

/**
 * Extracts the workspace files a turn's tool calls created or modified.
 *
 * Only successful calls count -- a failed write left nothing to open -- and each
 * file appears once even when it was touched repeatedly during the turn.
 */
export function extractWrittenFiles(toolCalls: TurnToolCall[]): string[] {
  const seen = new Set<string>();
  const files: string[] = [];

  for (const call of toolCalls) {
    if (!FILE_WRITING_TOOLS.has(call.name) || call.isError) continue;
    let path: unknown;
    try {
      path = JSON.parse(call.argumentsStr || '{}')?.path;
    } catch {
      continue;
    }
    if (typeof path !== 'string' || !path) continue;

    const relative = workspaceRelativePath(path);
    if (!relative || seen.has(relative)) continue;
    seen.add(relative);
    files.push(relative);
  }

  return files;
}
