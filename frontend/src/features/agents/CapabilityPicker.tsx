import React, { useMemo, useState } from 'react';
import { ToolCategoryInfo, ToolDefinition, ToolRisk } from '../../types';
import { ToolAccess } from './agentAccess';
import { AlertTriangle, Search, Wrench } from 'lucide-react';

/**
 * Per-tool access control for an agent profile.
 *
 * `allowed_tools` and `tool_permissions` describe one thing between them: whether a
 * tool is unavailable, gated behind a prompt, or free to run. Presenting them as a
 * single tri-state keeps the two in sync by construction -- the previous checkbox
 * modelled only the first bit and silently discarded the second on every save.
 *
 * Grouping comes from each tool's own declared category, served by the API, so adding
 * a tool never requires touching this component.
 */

const ACCESS_OPTIONS: { value: ToolAccess; label: string; hint: string }[] = [
  { value: 'off', label: 'Off', hint: 'The agent cannot call this tool.' },
  { value: 'ask', label: 'Ask', hint: 'Each call waits for your approval in the chat.' },
  { value: 'auto', label: 'Auto', hint: 'The agent calls this tool freely.' },
];

const RISK_STYLES: Record<ToolRisk, string> = {
  low: 'bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant',
  moderate: 'bg-md-surface-container-high text-md-on-surface-variant border-md-outline-variant',
  high: 'bg-amber-100 dark:bg-amber-950/80 text-amber-800 dark:text-amber-200 border-amber-300 dark:border-amber-800/80',
};

interface CapabilityPickerProps {
  tools: ToolDefinition[];
  categories: ToolCategoryInfo[];
  access: Record<string, ToolAccess>;
  onChange: (toolName: string, value: ToolAccess) => void;
  onBulkChange: (toolNames: string[], value: ToolAccess) => void;
}

export const CapabilityPicker: React.FC<CapabilityPickerProps> = ({
  tools,
  categories,
  access,
  onChange,
  onBulkChange,
}) => {
  const [query, setQuery] = useState('');

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return tools;
    return tools.filter(
      (tool) =>
        tool.name.toLowerCase().includes(needle) ||
        tool.description.toLowerCase().includes(needle)
    );
  }, [tools, query]);

  // Categories arrive in presentation order; any category the backend did not
  // describe still renders rather than silently dropping its tools.
  const groups = useMemo(() => {
    const known = categories.map((category) => ({
      category,
      tools: filtered.filter((tool) => tool.category === category.value),
    }));
    const describedValues = new Set(categories.map((c) => c.value));
    const orphans = filtered.filter((tool) => !describedValues.has(tool.category));
    if (orphans.length) {
      known.push({
        category: { value: 'custom', label: 'Other', description: '' } as ToolCategoryInfo,
        tools: orphans,
      });
    }
    return known.filter((group) => group.tools.length > 0);
  }, [filtered, categories]);

  const counts = useMemo(() => {
    const values = tools.map((tool) => access[tool.name] ?? 'off');
    return {
      auto: values.filter((v) => v === 'auto').length,
      ask: values.filter((v) => v === 'ask').length,
      off: values.filter((v) => v === 'off').length,
    };
  }, [tools, access]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <label className="text-xs font-bold uppercase tracking-wider text-md-on-surface flex items-center gap-1.5">
          <Wrench className="w-3.5 h-3.5 text-md-primary" /> Tool Access
        </label>
        <span className="text-[11px] text-md-on-surface-variant font-mono">
          {counts.auto} auto · {counts.ask} ask · {counts.off} off
        </span>
      </div>

      <div className="relative">
        <Search className="w-3.5 h-3.5 text-md-on-surface-variant absolute left-3 top-1/2 -translate-y-1/2" />
        <input
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search tools by name or description..."
          className="w-full bg-md-surface-container-lowest border border-md-outline-variant rounded-xl pl-9 pr-3 py-2 text-xs text-md-on-surface focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary transition-colors"
        />
      </div>

      {groups.length === 0 && (
        <p className="text-xs text-md-on-surface-variant py-6 text-center">
          No tools match “{query}”.
        </p>
      )}

      {groups.map(({ category, tools: groupTools }) => {
        const names = groupTools.map((tool) => tool.name);
        return (
          <div key={category.value} className="space-y-1.5">
            <div className="flex items-center justify-between gap-3 pt-2">
              <div className="min-w-0">
                <h4 className="text-[11px] font-bold uppercase tracking-wider text-md-on-surface">
                  {category.label}
                  <span className="ml-1.5 font-mono font-normal text-md-on-surface-variant">
                    ({groupTools.length})
                  </span>
                </h4>
                {category.description && (
                  <p className="text-[10px] text-md-on-surface-variant leading-relaxed">
                    {category.description}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                {(['off', 'auto'] as ToolAccess[]).map((value) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => onBulkChange(names, value)}
                    className="text-[10px] font-semibold px-2 py-1 rounded-lg border border-md-outline-variant text-md-on-surface-variant hover:text-md-on-surface hover:bg-md-surface-container-high transition-colors cursor-pointer"
                  >
                    All {value}
                  </button>
                ))}
              </div>
            </div>

            {groupTools.map((tool) => {
              const current = access[tool.name] ?? 'off';
              return (
                <div
                  key={tool.name}
                  className={`p-3 rounded-xl border flex items-center justify-between gap-4 transition-colors ${
                    current === 'off'
                      ? 'bg-md-surface border-md-outline-variant'
                      : 'bg-md-primary-container/40 border-md-primary/50'
                  }`}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-semibold text-xs font-mono text-md-on-surface">
                        {tool.name}
                      </span>
                      {!tool.is_builtin && (
                        <span className="text-[9px] font-semibold uppercase px-1.5 py-0.5 rounded bg-md-tertiary-container text-md-on-tertiary-container border border-md-outline-variant">
                          Custom
                        </span>
                      )}
                      {tool.risk === 'high' && (
                        <span
                          className={`text-[9px] font-semibold uppercase px-1.5 py-0.5 rounded border inline-flex items-center gap-1 ${RISK_STYLES[tool.risk]}`}
                          title="This tool can execute code or change what the platform runs."
                        >
                          <AlertTriangle className="w-2.5 h-2.5" /> High risk
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-md-on-surface-variant mt-0.5 leading-normal line-clamp-2">
                      {tool.description}
                    </p>
                  </div>

                  <div className="flex bg-md-surface-container-high border border-md-outline-variant rounded-lg p-0.5 shrink-0">
                    {ACCESS_OPTIONS.map((option) => (
                      <button
                        key={option.value}
                        type="button"
                        title={option.hint}
                        onClick={() => onChange(tool.name, option.value)}
                        className={`px-2.5 py-1 rounded-md text-[11px] font-semibold transition-colors cursor-pointer ${
                          current === option.value
                            ? 'bg-md-primary text-md-on-primary shadow-xs'
                            : 'text-md-on-surface-variant hover:text-md-on-surface'
                        }`}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
};
