import React, { useState } from 'react';
import { api } from '../api/client';
import { VerifyToolResponse } from '../types';
import { CodeBlock } from './CodeBlock';
import { ChatPane } from './ChatPane';
import { 
  Sparkles, 
  Play, 
  CheckCircle2, 
  ShieldCheck, 
  Save, 
  FileCode, 
  Loader2 
} from 'lucide-react';
import { useDialog } from '../context/DialogContext';

interface ToolBuilderProps {
  onToolActivated: () => void;
  onRefreshConversations?: () => void;
}

export const ToolBuilder: React.FC<ToolBuilderProps> = ({ 
  onToolActivated,
  onRefreshConversations
}) => {
  const dialog = useDialog();
  const [conversationId, setConversationId] = useState<string | null>(null);

  // Tool Code State
  const [toolName, setToolName] = useState('');
  const [toolCode, setToolCode] = useState('');
  const [verifyCode, setVerifyCode] = useState('');

  const [activeCodeTab, setActiveCodeTab] = useState<'tool' | 'verify'>('tool');
  const [isVerifying, setIsVerifying] = useState(false);
  const [verifyResult, setVerifyResult] = useState<VerifyToolResponse | null>(null);
  const [isActivating, setIsActivating] = useState(false);
  const [activatedSuccess, setActivatedSuccess] = useState(false);

  const handleToolDraftDetected = (draft: {
    name?: string;
    toolCode?: string;
    verifyCode?: string;
    verifyOutput?: string;
    isSuccess?: boolean;
  }) => {
    if (draft.name && !toolName) {
      setToolName(draft.name);
    } else if (draft.name && toolName !== draft.name) {
      setToolName(draft.name);
    }
    if (draft.toolCode) {
      setToolCode(draft.toolCode);
    }
    if (draft.verifyCode) {
      setVerifyCode(draft.verifyCode);
    }
    if (draft.verifyOutput) {
      setVerifyResult({
        success: draft.isSuccess ?? draft.verifyOutput.includes('Verification Passed'),
        stdout: draft.verifyOutput,
        stderr: '',
        error: !draft.isSuccess && draft.verifyOutput.includes('Verification Failed') ? draft.verifyOutput : undefined,
      });
    }
  };

  const handleVerify = async () => {
    if (!toolName.trim() || !toolCode.trim() || !verifyCode.trim()) {
      dialog.alert({
        title: 'Missing Tool Implementation',
        message: 'Please provide a tool name, tool.py code, and verify.py verification tests before running verification.',
        variant: 'info',
      });
      return;
    }

    setIsVerifying(true);
    setVerifyResult(null);
    setActivatedSuccess(false);
    try {
      const res = await api.verifyTool({
        tool_name: toolName.trim(),
        tool_code: toolCode,
        verify_code: verifyCode,
      });
      setVerifyResult(res);
    } catch (e: any) {
      setVerifyResult({
        success: false,
        stdout: '',
        stderr: e.message || 'Verification execution failed',
        error: e.message,
      });
    } finally {
      setIsVerifying(false);
    }
  };

  const handleActivate = async () => {
    if (!toolName.trim() || !toolCode.trim() || !verifyCode.trim()) return;

    setIsActivating(true);
    try {
      await api.activateTool({
        tool_name: toolName.trim(),
        tool_code: toolCode,
        verify_code: verifyCode,
      });
      setActivatedSuccess(true);
      setTimeout(() => {
        onToolActivated();
      }, 1000);
    } catch (e: any) {
      dialog.alert({
        title: 'Activation Failed',
        message: e.message || 'Failed to activate and register tool.',
        variant: 'danger',
      });
    } finally {
      setIsActivating(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 bg-zinc-50 dark:bg-zinc-950 overflow-hidden transition-colors">
      {/* Studio Header Bar */}
      <div className="h-16 border-b border-zinc-200 dark:border-zinc-800 px-8 flex items-center justify-between bg-white dark:bg-zinc-900 shrink-0 transition-colors">
        <div className="flex items-center space-x-3">
          <Sparkles className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
          <div>
            <h2 className="font-bold text-sm text-zinc-900 dark:text-zinc-100">
              Tool Studio & Live Verification Runner
            </h2>
            <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
              Powered by the <span className="text-zinc-800 dark:text-zinc-200 font-mono font-semibold">tool_architect</span> agent. Chat on the left to synthesize code, edit on the right, and verify tests.
            </p>
          </div>
        </div>

        <div className="flex items-center space-x-3">
          <button
            onClick={handleVerify}
            disabled={isVerifying || !toolCode.trim()}
            className="px-4 py-2 rounded-xl text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 flex items-center gap-1.5 transition-colors shadow-sm focus:ring-2 focus:ring-indigo-500"
          >
            {isVerifying ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Play className="w-3.5 h-3.5 fill-white stroke-white" />
            )}
            <span>{isVerifying ? 'Running Tests...' : 'Run Verification (verify.py)'}</span>
          </button>

          <button
            onClick={handleActivate}
            disabled={!verifyResult?.success || isActivating || activatedSuccess || !toolName.trim()}
            className="px-4 py-2 rounded-xl text-xs font-bold bg-emerald-600 hover:bg-emerald-700 disabled:opacity-40 text-white flex items-center gap-1.5 shadow-sm transition-colors focus:ring-2 focus:ring-emerald-500"
          >
            {isActivating ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : activatedSuccess ? (
              <CheckCircle2 className="w-3.5 h-3.5 text-white" />
            ) : (
              <Save className="w-3.5 h-3.5" />
            )}
            <span>{activatedSuccess ? 'Tool Activated!' : 'Activate Tool'}</span>
          </button>
        </div>
      </div>

      {/* Main Workspace Split (50% Chat | 50% Code & Test Runner) */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Column: Tool Architect Chat */}
        <div className="w-1/2 border-r border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-950 flex flex-col overflow-hidden transition-colors">
          <ChatPane
            conversationId={conversationId}
            agentId="tool_architect"
            agentName="Tool Architect"
            agentModel="gemini/gemini-3.6-flash"
            headerTitle="Tool Architect Assistant"
            headerBadge="Autonomous Synthesis"
            headerSubtitle="Describe the tool you want to build. The agent will draft tool.py, test it with verify.py, and sync code to the live editor."
            placeholder="e.g. Create a uuidv4() generator tool with formatting options..."
            fullWidthInput={true}
            onConversationCreated={(newId) => setConversationId(newId)}
            onToolDraftDetected={handleToolDraftDetected}
            onRefreshConversations={onRefreshConversations}
          />
        </div>

        {/* Right Column: Code Editor & Execution Runner */}
        <div className="w-1/2 flex flex-col bg-white dark:bg-zinc-900 overflow-hidden transition-colors">
          {/* File Tab Selector */}
          <div className="p-3 border-b border-zinc-200 dark:border-zinc-800 flex items-center justify-between bg-zinc-50 dark:bg-zinc-800/80 shrink-0">
            <div className="flex items-center space-x-3">
              <label className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">Tool Name:</label>
              <input
                type="text"
                value={toolName}
                placeholder="e.g. generate_uuidv4"
                onChange={(e) => setToolName(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '_'))}
                className="bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-600 rounded-lg px-2.5 py-1 font-mono text-xs text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-indigo-600 dark:focus:border-indigo-400 focus:ring-1 focus:ring-indigo-500 transition-colors"
              />
            </div>

            <div className="flex bg-zinc-200/80 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded-lg p-0.5 text-xs">
              <button
                onClick={() => setActiveCodeTab('tool')}
                className={`px-3 py-1 rounded-md font-mono text-xs transition-colors flex items-center gap-1.5 cursor-pointer ${
                  activeCodeTab === 'tool' ? 'bg-white dark:bg-zinc-700 text-zinc-900 dark:text-zinc-100 font-semibold shadow-xs' : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100'
                }`}
              >
                <FileCode className="w-3.5 h-3.5" />
                <span>tool.py</span>
              </button>
              <button
                onClick={() => setActiveCodeTab('verify')}
                className={`px-3 py-1 rounded-md font-mono text-xs transition-colors flex items-center gap-1.5 cursor-pointer ${
                  activeCodeTab === 'verify' ? 'bg-white dark:bg-zinc-700 text-zinc-900 dark:text-zinc-100 font-semibold shadow-xs' : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100'
                }`}
              >
                <ShieldCheck className="w-3.5 h-3.5" />
                <span>verify.py</span>
              </button>
            </div>
          </div>

          {/* Textarea Code Area */}
          <div className="h-64 p-3 bg-white dark:bg-zinc-900 border-b border-zinc-200 dark:border-zinc-800 flex flex-col shrink-0 transition-colors">
            <textarea
              value={activeCodeTab === 'tool' ? toolCode : verifyCode}
              onChange={(e) => {
                if (activeCodeTab === 'tool') setToolCode(e.target.value);
                else setVerifyCode(e.target.value);
              }}
              placeholder={
                activeCodeTab === 'tool'
                  ? "# tool.py source code will sync here from the agent or you can type directly...\ndef my_tool(...) -> str:\n    ..."
                  : "# verify.py test suite will sync here from the agent or you can type directly...\ndef test_happy_path():\n    assert ..."
              }
              spellCheck={false}
              className="flex-1 w-full bg-zinc-50 dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-700 rounded-xl p-3 font-mono text-[11px] text-zinc-900 dark:text-zinc-100 focus:bg-white dark:focus:bg-zinc-950 focus:outline-none focus:border-indigo-600 dark:focus:border-indigo-400 focus:ring-1 focus:ring-indigo-500 resize-none leading-relaxed transition-colors"
            />
          </div>

          {/* Verification Results Panel */}
          <div className="flex-1 p-4 bg-zinc-50/50 dark:bg-zinc-950/80 overflow-y-auto space-y-3 transition-colors">
            <div className="flex items-center justify-between text-xs font-bold text-zinc-800 dark:text-zinc-200 uppercase tracking-wider">
              <span>Verification Output</span>
              {verifyResult && (
                <span className={`inline-flex items-center gap-1 text-[10px] px-2.5 py-0.5 rounded-full font-bold ${
                  verifyResult.success
                    ? 'bg-emerald-100 dark:bg-emerald-950/80 text-emerald-800 dark:text-emerald-300 border border-emerald-300 dark:border-emerald-800/80'
                    : 'bg-rose-100 dark:bg-rose-950/80 text-rose-800 dark:text-rose-300 border border-rose-300 dark:border-rose-800/80'
                }`}>
                  {verifyResult.success ? '✓ TESTS PASSED' : '✗ TESTS FAILED'}
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
                    <div className="text-[10px] font-bold text-zinc-700 dark:text-zinc-300 uppercase tracking-wider mb-1">
                      Auto-extracted JSON Schema
                    </div>
                    <CodeBlock
                      code={JSON.stringify(verifyResult.parsed_schema, null, 2)}
                      language="json"
                    />
                  </div>
                )}
              </>
            ) : (
              <div className="text-center py-10 text-zinc-400 dark:text-zinc-600 text-xs">
                {toolCode.trim() 
                  ? "Click Run Verification (verify.py) to test the implementation."
                  : "Ask the Tool Architect to generate a tool, or enter code above."}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
