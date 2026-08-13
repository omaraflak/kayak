import { AgentConfig, ToolDefinition, ToolPermission } from '../types';

/**
 * Translation between an agent profile's stored fields and the single control the UI
 * presents for them.
 *
 * `allowed_tools` and `tool_permissions` together describe one thing: whether a tool
 * is unavailable, gated behind a prompt, or free to run. Keeping the conversion in
 * one place -- rather than letting a form render half the model and write back the
 * rest from memory -- is what stops a save from dropping the half it never showed.
 */

export type ToolAccess = 'off' | 'ask' | 'auto';

type AccessSource = Pick<AgentConfig, 'allowed_tools' | 'tool_permissions'>;

/** Derives the per-tool tri-state from the two fields that encode it. */
export function deriveToolAccess(
  agent: AccessSource | null,
  tools: Pick<ToolDefinition, 'name'>[]
): Record<string, ToolAccess> {
  const access: Record<string, ToolAccess> = {};

  for (const tool of tools) {
    const allowed = agent?.allowed_tools?.includes(tool.name) ?? false;
    const permission = agent?.tool_permissions?.[tool.name];
    // An explicit `denied` outranks allowlist membership, so a profile that says
    // both reads as off rather than silently granting the tool.
    if (!allowed || permission === 'denied') access[tool.name] = 'off';
    else access[tool.name] = permission === 'ask_user' ? 'ask' : 'auto';
  }

  return access;
}

/** Collapses the tri-state back into allowed_tools plus tool_permissions. */
export function serializeToolAccess(access: Record<string, ToolAccess>): {
  allowed_tools: string[];
  tool_permissions: Record<string, ToolPermission>;
} {
  const allowed_tools: string[] = [];
  const tool_permissions: Record<string, ToolPermission> = {};

  for (const [name, value] of Object.entries(access)) {
    if (value === 'off') continue;
    allowed_tools.push(name);
    // Auto-approval is the resolver's default, so it is omitted to keep the
    // generated YAML readable.
    if (value === 'ask') tool_permissions[name] = 'ask_user';
  }

  return { allowed_tools: allowed_tools.sort(), tool_permissions };
}
