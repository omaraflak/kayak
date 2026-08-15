import React, { useEffect, useMemo, useState } from 'react';
import { api, errorMessage } from '../../api/client';
import { ToolDefinition, VerifyToolResponse } from '../../types';
import { CodeBlock } from '../../ui/CodeBlock';
import { AgentChatPrompt } from '../chat/AgentChatPrompt';
import {
  CheckCircle2,
  FileCode,
  Loader2,
  Play,
  RotateCcw,
  Save,
  ShieldCheck,
  Wrench,
} from 'lucide-react';
import { useDialog } from '../../context/DialogContext';

/**
 * Editor for a tool's `tool.py` and `verify.py`, used for both creating a new tool
 * and editing an installed one.
 *
 * This deliberately has no chat panel. Generating a tool by conversation is just a
 * conversation -- the `tool_architect` agent works in any chat -- whereas seeing the
 * real installed source, hand-editing it, and re-running its tests are things a
 * transcript cannot do. Those are what this surface is for.
 */

const NEW_TOOL_TEMPLATE = `def execute(text: str) -> str:
    """One-line description the model will see.

    Args:
        text: Description of this parameter.
    """
    return text.upper()
`;

const NEW_VERIFY_TEMPLATE = `from tool import execute


def test_happy_path():
    assert execute("hello") == "HELLO"


test_happy_path()
print("All tests passed.")
`;

interface ToolEditorProps {
  /** Tool being edited, or null when creating a new one. */
  tool: ToolDefinition | null;
  onSaved: (toolName: string) => void;
  onCancel: () => void;
  onStartAgentChat?: (agentId: string) => void;
}

export const ToolEditor: React.FC<ToolEditorProps> = ({
  tool,
  onSaved,
  onCancel,
  onStartAgentChat,
}) => {
  const dialog = useDialog();
  const isNew = tool === null;

  const [toolName, setToolName] = useState('');
  const [toolCode, setToolCode] = useState('');
  const [verifyCode, setVerifyCode] = useState('');
  const [activeTab, setActiveTab] = useState<'tool' | 'verify'>('tool');
  const [isVerifying, setIsVerifying] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [verifyResult, setVerifyResult] = useState<VerifyToolResponse | null>(null);

  // Reset the form whenever the target tool changes, so switching entries in the
  // list never leaves another tool's code in the editor.
  useEffect(() => {
    setToolName(tool?.name ?? '');
    setToolCode(tool?.source_code ?? NEW_TOOL_TEMPLATE);
    setVerifyCode(tool?.verify_code ?? NEW_VERIFY_TEMPLATE);
    setVerifyResult(null);
    setActiveTab('tool');
  }, [tool]);

  const isDirty = useMemo(() => {
    if (isNew) return true;
    return toolCode !== (tool?.source_code ?? '') || verifyCode !== (tool?.verify_code ?? '');
  }, [isNew, tool, toolCode, verifyCode]);

  const canSubmit = Boolean(toolName.trim() && toolCode.trim() && verifyCode.trim());

  const handleReset = () => {
    setToolName(tool?.name ?? '');
    setToolCode(tool?.source_code ?? NEW_TOOL_TEMPLATE);
    setVerifyCode(tool?.verify_code ?? NEW_VERIFY_TEMPLATE);
    setVerifyResult(null);
  };

  const handleVerify = async () => {
    if (!canSubmit) {
      dialog.alert({
        title: 'Missing Tool Implementation',
        message: 'A tool name, tool.py source, and verify.py test suite are all required.',
        variant: 'info',
      });
      return;
    }

    setIsVerifying(true);
    setVerifyResult(null);
    try {
      setVerifyResult(
        await api.verifyTool({
          tool_name: toolName.trim(),
          tool_code: toolCode,
          verify_code: verifyCode,
        })
      );
    } catch (error) {
      const reason = errorMessage(error);
      setVerifyResult({
        success: false,
        stdout: '',
        stderr: reason || 'Verification execution failed',
        error: reason,
      });
    } finally {
      setIsVerifying(false);
    }
  };

  const handleSave = async () => {
    if (!canSubmit) return;

    setIsSaving(true);
    try {
      // The server re-runs verification and refuses to install failing code, so this
      // is not gated on a prior local run having happened.
      const saved = await api.activateTool({
        tool_name: toolName.trim(),
        tool_code: toolCode,
        verify_code: verifyCode,
      });
      onSaved(saved.name);
    } catch (error) {
      const reason = errorMessage(error);
      setVerifyResult({
        success: false,
        stdout: '',
        stderr: reason || 'Activation failed',
        error: reason,
      });
      dialog.alert({
        title: 'Not Installed',
        message:
          'The tool was not installed because its verification tests did not pass. ' +
          'See the verification output for details.',
        variant: 'danger',
      });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 bg-md-surface overflow-hidden transition-colors">
      <div className="h-16 border-b border-md-outline-variant px-8 flex items-center justify-between bg-md-surface-container-low shrink-0 transition-colors">
        <div className="flex items-center space-x-3">
          <Wrench className="w-5 h-5 text-md-primary" />
          <div>
            <h2 className="font-bold text-sm text-md-on-surface">
              {isNew ? 'New Tool' : `Editing ${tool?.name}`}
            </h2>
            <p className="text-[11px] text-md-on-surface-variant">
              Tools run inside the Kayak server process, so verify.py must pass before a tool is installed.
            </p>
          </div>
        </div>

        <div className="flex items-center space-x-2">
          <button
            onClick={onCancel}
            className="px-3.5 py-2 rounded-xl text-xs font-semibold text-md-on-surface bg-md-surface-container-high border border-md-outline-variant hover:opacity-90 transition-opacity cursor-pointer"
          >
            Cancel
          </button>
          {!isNew && (
            <button
              onClick={handleReset}
              disabled={!isDirty}
              className="px-3.5 py-2 rounded-xl text-xs font-semibold text-md-on-surface bg-md-surface-container-high border border-md-outline-variant hover:opacity-90 disabled:opacity-40 transition-opacity flex items-center gap-1.5 cursor-pointer"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>Revert</span>
            </button>
          )}
          <button
            onClick={handleVerify}
            disabled={isVerifying || !toolCode.trim()}
            className="px-4 py-2 rounded-xl text-xs font-bold text-md-on-primary bg-md-primary hover:opacity-90 disabled:opacity-40 flex items-center gap-1.5 transition-opacity shadow-xs cursor-pointer"
          >
            {isVerifying ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Play className="w-3.5 h-3.5 fill-current" />
            )}
            <span>{isVerifying ? 'Running Tests...' : 'Run verify.py'}</span>
          </button>
          <button
            onClick={handleSave}
            disabled={!canSubmit || isSaving || (!isNew && !isDirty)}
            className="px-4 py-2 rounded-xl text-xs font-bold bg-emerald-700 hover:bg-emerald-800 dark:bg-emerald-600 dark:hover:bg-emerald-500 disabled:opacity-40 text-white flex items-center gap-1.5 shadow-xs transition-colors cursor-pointer"
          >
            {isSaving ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Save className="w-3.5 h-3.5" />
            )}
            <span>{isNew ? 'Verify & Install' : 'Verify & Save'}</span>
          </button>
        </div>
      </div>

      {isNew && (
        <AgentChatPrompt
          agentId="tool_architect"
          agentLabel="Tool Architect"
          description="Rather describe the tool than write it? The Tool Architect can draft, test, and install it for you."
          onStartAgentChat={onStartAgentChat}
        />
      )}

      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="p-3 border-b border-md-outline-variant flex items-center justify-between bg-md-surface-container-low shrink-0">
          <div className="flex items-center space-x-3">
            <label className="text-xs font-semibold text-md-on-surface">Tool Name:</label>
            <input
              type="text"
              value={toolName}
              disabled={!isNew}
              placeholder="e.g. generate_uuidv4"
              onChange={(e) => setToolName(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '_'))}
              className="bg-md-surface-container-lowest border border-md-outline-variant rounded-lg px-2.5 py-1 font-mono text-xs text-md-on-surface focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary disabled:opacity-60 transition-colors"
            />
            {!isNew && (
              <span className="text-[10px] text-md-on-surface-variant">
                Renaming installs a separate tool — delete the old one if you meant to rename.
              </span>
            )}
          </div>

          <div className="flex bg-md-surface-container-high border border-md-outline-variant rounded-lg p-0.5 text-xs">
            <button
              onClick={() => setActiveTab('tool')}
              className={`px-3 py-1 rounded-md font-mono text-xs transition-colors flex items-center gap-1.5 cursor-pointer ${
                activeTab === 'tool'
                  ? 'bg-md-primary text-md-on-primary font-semibold shadow-xs'
                  : 'text-md-on-surface-variant hover:text-md-on-surface'
              }`}
            >
              <FileCode className="w-3.5 h-3.5" />
              <span>tool.py</span>
            </button>
            <button
              onClick={() => setActiveTab('verify')}
              className={`px-3 py-1 rounded-md font-mono text-xs transition-colors flex items-center gap-1.5 cursor-pointer ${
                activeTab === 'verify'
                  ? 'bg-md-primary text-md-on-primary font-semibold shadow-xs'
                  : 'text-md-on-surface-variant hover:text-md-on-surface'
              }`}
            >
              <ShieldCheck className="w-3.5 h-3.5" />
              <span>verify.py</span>
            </button>
          </div>
        </div>

        <div className="flex-1 min-h-0 p-3 bg-md-surface-container-lowest flex flex-col transition-colors">
          <textarea
            value={activeTab === 'tool' ? toolCode : verifyCode}
            onChange={(e) => {
              if (activeTab === 'tool') setToolCode(e.target.value);
              else setVerifyCode(e.target.value);
            }}
            spellCheck={false}
            className="flex-1 w-full bg-md-surface-container-lowest border border-md-outline-variant rounded-xl p-3 font-mono text-[12px] text-md-on-surface focus:outline-none focus:border-md-primary focus:ring-1 focus:ring-md-primary resize-none leading-relaxed transition-colors"
          />
        </div>

        <div className="h-64 border-t border-md-outline-variant p-4 bg-md-surface overflow-y-auto space-y-3 shrink-0 transition-colors">
          <div className="flex items-center justify-between text-xs font-bold text-md-on-surface uppercase tracking-wider">
            <span>Verification Output</span>
            {verifyResult && (
              <span
                className={`inline-flex items-center gap-1 text-[10px] px-2.5 py-0.5 rounded-full font-bold ${
                  verifyResult.success
                    ? 'bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-300 border border-emerald-300 dark:border-emerald-800/80'
                    : 'bg-rose-100 dark:bg-rose-950/80 text-rose-800 dark:text-rose-300 border border-rose-300 dark:border-rose-800/80'
                }`}
              >
                {verifyResult.success ? (
                  <>
                    <CheckCircle2 className="w-3 h-3" /> TESTS PASSED
                  </>
                ) : (
                  '✗ TESTS FAILED'
                )}
              </span>
            )}
          </div>

          {verifyResult ? (
            <>
              <CodeBlock
                code={verifyResult.stdout || verifyResult.stderr || verifyResult.error || 'No console output.'}
                language="bash"
                showHeader={false}
              />
              {verifyResult.parsed_schema && (
                <div>
                  <div className="text-[10px] font-bold text-md-on-surface uppercase tracking-wider mb-1">
                    Auto-extracted JSON Schema
                  </div>
                  <CodeBlock code={JSON.stringify(verifyResult.parsed_schema, null, 2)} language="json" />
                </div>
              )}
            </>
          ) : (
            <div className="text-center py-8 text-md-on-surface-variant text-xs leading-relaxed">
              Run verify.py to test this implementation before installing it.
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
