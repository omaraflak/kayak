import { describe, expect, it } from 'vitest';
import {
  breadcrumbs,
  extractWrittenFiles,
  fileKind,
  formatFileSize,
  joinPath,
  parentPath,
  workspaceRelativePath,
} from './workspaceFiles';
import { TurnToolCall } from './conversationTurns';

describe('fileKind', () => {
  it('classifies common types', () => {
    expect(fileKind('plot.png')).toBe('image');
    expect(fileKind('photo.JPEG')).toBe('image');
    expect(fileKind('report.pdf')).toBe('pdf');
    expect(fileKind('notes.md')).toBe('text');
    expect(fileKind('main.py')).toBe('text');
    expect(fileKind('model.bin')).toBe('binary');
  });

  it('uses only the basename of a nested path', () => {
    expect(fileKind('out/charts/plot.png')).toBe('image');
  });

  it('treats well-known extensionless names as text', () => {
    expect(fileKind('Dockerfile')).toBe('text');
    expect(fileKind('Makefile')).toBe('text');
  });
});

describe('path helpers', () => {
  it('joins against the workspace root without a leading dot', () => {
    expect(joinPath('.', 'src')).toBe('src');
    expect(joinPath('src', 'main.py')).toBe('src/main.py');
  });

  it('walks back up to the root', () => {
    expect(parentPath('src/main.py')).toBe('src');
    expect(parentPath('src')).toBe('.');
  });

  it('builds breadcrumbs from the workspace root', () => {
    expect(breadcrumbs('.')).toEqual([{ label: 'workspace', path: '.' }]);
    expect(breadcrumbs('a/b')).toEqual([
      { label: 'workspace', path: '.' },
      { label: 'a', path: 'a' },
      { label: 'b', path: 'a/b' },
    ]);
  });

  it('formats sizes for humans', () => {
    expect(formatFileSize(512)).toBe('512 B');
    expect(formatFileSize(2048)).toBe('2.0 KB');
    expect(formatFileSize(5 * 1024 * 1024)).toBe('5.0 MB');
  });
});

describe('workspaceRelativePath', () => {
  it('keeps relative paths as they are', () => {
    expect(workspaceRelativePath('plot.png')).toBe('plot.png');
    expect(workspaceRelativePath('./out/plot.png')).toBe('out/plot.png');
  });

  it('maps the container mount point back to the workspace', () => {
    // Agents see the workspace at /workspace, and transcripts use that form.
    expect(workspaceRelativePath('/workspace/out/plot.png')).toBe('out/plot.png');
  });

  it('leaves external URLs alone', () => {
    expect(workspaceRelativePath('https://example.com/a.png')).toBeNull();
    expect(workspaceRelativePath('data:image/png;base64,AAAA')).toBeNull();
    expect(workspaceRelativePath('mailto:someone@example.com')).toBeNull();
    expect(workspaceRelativePath('//cdn.example.com/a.png')).toBeNull();
  });

  it('refuses absolute paths outside the mount', () => {
    expect(workspaceRelativePath('/etc/passwd')).toBeNull();
  });

  it('handles empty and anchor inputs', () => {
    expect(workspaceRelativePath('')).toBeNull();
    expect(workspaceRelativePath('#section')).toBeNull();
    expect(workspaceRelativePath(undefined)).toBeNull();
  });
});

describe('extractWrittenFiles', () => {
  const call = (
    name: string,
    args: unknown,
    isError = false
  ): TurnToolCall => ({
    id: `${name}_${JSON.stringify(args)}`,
    name,
    argumentsStr: typeof args === 'string' ? args : JSON.stringify(args),
    isError,
  });

  it('collects files from write and edit calls', () => {
    const files = extractWrittenFiles([
      call('write_file', { path: 'report.md', content: '# hi' }),
      call('edit_file', { path: 'src/main.py', target: 'a', replacement: 'b' }),
    ]);
    expect(files).toEqual(['report.md', 'src/main.py']);
  });

  it('ignores failed calls: a failed write left nothing to open', () => {
    const files = extractWrittenFiles([
      call('write_file', { path: 'broken.txt' }, true),
    ]);
    expect(files).toEqual([]);
  });

  it('ignores tools that do not produce files', () => {
    const files = extractWrittenFiles([
      call('run_command', { command: 'ls' }),
      call('read_file', { path: 'notes.md' }),
    ]);
    expect(files).toEqual([]);
  });

  it('deduplicates a file touched repeatedly in one turn', () => {
    const files = extractWrittenFiles([
      call('write_file', { path: 'app.py' }),
      call('edit_file', { path: 'app.py', target: 'x', replacement: 'y' }),
    ]);
    expect(files).toEqual(['app.py']);
  });

  it('normalises container-mount paths', () => {
    const files = extractWrittenFiles([
      call('write_file', { path: '/workspace/out/plot.png' }),
    ]);
    expect(files).toEqual(['out/plot.png']);
  });

  it('survives malformed argument JSON from a streaming turn', () => {
    const files = extractWrittenFiles([call('write_file', '{"path": "trunc')]);
    expect(files).toEqual([]);
  });
});
