import { describe, expect, it } from 'vitest';
import { humanizeToolName, toolCallDetail } from './toolCallLabel';

describe('humanizeToolName', () => {
  it('reads a function name as words', () => {
    expect(humanizeToolName('run_command')).toBe('Run command');
    expect(humanizeToolName('fetch-url')).toBe('Fetch url');
  });

  it('never renders an empty label', () => {
    expect(humanizeToolName('__')).toBe('Step');
  });
});

describe('toolCallDetail', () => {
  it('prefers the argument that says what the step acted on', () => {
    expect(toolCallDetail('{"timeout": "30", "command": "ls -la"}')).toBe('ls -la');
    expect(toolCallDetail('{"path": "report.csv", "content": "a,b"}')).toBe('report.csv');
  });

  it('falls back to the first text argument of a tool it does not know', () => {
    expect(toolCallDetail('{"count": 3, "city": "Paris"}')).toBe('Paris');
  });

  it('keeps the header to one short line', () => {
    const detail = toolCallDetail(JSON.stringify({ command: 'x'.repeat(200) }));
    expect(detail?.length).toBeLessThanOrEqual(61);
    expect(toolCallDetail('{"command": "echo\\n  hi"}')).toBe('echo hi');
  });

  it('shows nothing rather than raw text it cannot read', () => {
    expect(toolCallDetail('{"command": "ls"')).toBeNull();
    expect(toolCallDetail('[1, 2]')).toBeNull();
    expect(toolCallDetail('{"count": 3}')).toBeNull();
    expect(toolCallDetail(undefined)).toBeNull();
  });
});
