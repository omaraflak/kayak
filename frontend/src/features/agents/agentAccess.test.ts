import { describe, expect, it } from 'vitest';
import { AgentConfig, ToolDefinition } from '../../types';
import { ToolAccess, deriveToolAccess, serializeToolAccess } from './agentAccess';

/**
 * These guard the bug that motivated the tri-state control: the agent form rendered
 * `allowed_tools` as checkboxes, had no representation of `tool_permissions`, and
 * wrote `{}` for it on every save -- silently deleting approval gates.
 */

const TOOLS = [
  { name: 'read_file' },
  { name: 'run_command' },
  { name: 'web_search' },
] as Pick<ToolDefinition, 'name'>[];

function agent(overrides: Partial<AgentConfig>): AgentConfig {
  return {
    id: 'tester',
    name: 'Tester',
    description: '',
    model: 'gemini/gemini-3.6-flash',
    temperature: 0.7,
    system_prompt: '',
    allowed_tools: [],
    allowed_skills: [],
    preloaded_skills: [],
    tool_permissions: {},
    ...overrides,
  };
}

describe('deriveToolAccess', () => {
  it('treats a tool absent from the allowlist as off', () => {
    const access = deriveToolAccess(agent({ allowed_tools: ['read_file'] }), TOOLS);
    expect(access).toEqual({ read_file: 'auto', run_command: 'off', web_search: 'off' });
  });

  it('reads an ask_user permission as the ask state', () => {
    const access = deriveToolAccess(
      agent({
        allowed_tools: ['run_command'],
        tool_permissions: { run_command: 'ask_user' },
      }),
      TOOLS
    );
    expect(access.run_command).toBe('ask');
  });

  it('treats an explicit denial as off even when allowlisted', () => {
    const access = deriveToolAccess(
      agent({
        allowed_tools: ['run_command'],
        tool_permissions: { run_command: 'denied' },
      }),
      TOOLS
    );
    expect(access.run_command).toBe('off');
  });

  it('defaults a new profile to granting nothing', () => {
    const access = deriveToolAccess(null, TOOLS);
    expect(Object.values(access).every((value) => value === 'off')).toBe(true);
  });

  it('ignores grants for tools that are no longer installed', () => {
    const access = deriveToolAccess(
      agent({ allowed_tools: ['read_file', 'deleted_tool'] }),
      TOOLS
    );
    expect(access).not.toHaveProperty('deleted_tool');
  });
});

describe('serializeToolAccess', () => {
  it('omits tools that are off', () => {
    const { allowed_tools } = serializeToolAccess({
      read_file: 'auto',
      run_command: 'off',
    });
    expect(allowed_tools).toEqual(['read_file']);
  });

  it('records only the non-default permission', () => {
    const { tool_permissions } = serializeToolAccess({
      read_file: 'auto',
      run_command: 'ask',
    });
    // auto_approve is the resolver's default, so it stays out of the YAML.
    expect(tool_permissions).toEqual({ run_command: 'ask_user' });
  });

  it('produces an empty allowlist when everything is off', () => {
    // The backend reads this as "no tools", which is why turning everything off
    // must not be confused with leaving the field unset.
    const result = serializeToolAccess({ read_file: 'off', run_command: 'off' });
    expect(result.allowed_tools).toEqual([]);
    expect(result.tool_permissions).toEqual({});
  });

  it('sorts the allowlist for a stable diff', () => {
    const { allowed_tools } = serializeToolAccess({
      web_search: 'auto',
      read_file: 'auto',
      run_command: 'ask',
    });
    expect(allowed_tools).toEqual(['read_file', 'run_command', 'web_search']);
  });
});

describe('round trip', () => {
  it('preserves an approval gate through derive and serialize', () => {
    const original = agent({
      allowed_tools: ['read_file', 'run_command'],
      tool_permissions: { run_command: 'ask_user' },
    });

    const restored = serializeToolAccess(deriveToolAccess(original, TOOLS));

    expect(restored.allowed_tools).toEqual(['read_file', 'run_command']);
    expect(restored.tool_permissions).toEqual({ run_command: 'ask_user' });
  });

  it('is stable across repeated edits', () => {
    let access: Record<string, ToolAccess> = deriveToolAccess(
      agent({
        allowed_tools: ['read_file', 'run_command'],
        tool_permissions: { run_command: 'ask_user' },
      }),
      TOOLS
    );

    for (let round = 0; round < 3; round += 1) {
      const serialized = serializeToolAccess(access);
      access = deriveToolAccess(agent(serialized), TOOLS);
    }

    expect(serializeToolAccess(access)).toEqual({
      allowed_tools: ['read_file', 'run_command'],
      tool_permissions: { run_command: 'ask_user' },
    });
  });
});
