import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { Message } from '../types';
import { api } from '../api/client';
import { useSSE, ToolApprovalRequest } from '../hooks/useSSE';
import { useVLLMStatus, VLLM_LOADING_STATES } from '../context/VLLMStatusContext';
import { ToolCallCard } from './ToolCallCard';
import { ToolCallsAccordion } from './ToolCallsAccordion';
import { ThinkingAccordion } from './ThinkingAccordion';
import { CodeBlock } from './CodeBlock';
import { 
  Send, 
  Bot, 
  Loader2, 
  Sparkles,
  Square,
  Play,
  CheckCircle2,
  AlertCircle,
  ShieldQuestion,
  Check,
  X
} from 'lucide-react';
import { useDialog } from '../context/DialogContext';

export interface ChatPaneProps {
  conversationId: string | null;
  agentId?: string;
  agentName?: string;
  agentModel?: string;
  placeholder?: string;
  showHeader?: boolean;
  headerTitle?: string;
  headerBadge?: string;
  headerSubtitle?: string;
  fullWidthInput?: boolean;
  onSendMessage?: (content: string) => Promise<string | void>;
  onConversationCreated?: (newId: string) => void;
  onToolDraftDetected?: (tool: { name?: string; toolCode?: string; verifyCode?: string; verifyOutput?: string; isSuccess?: boolean }) => void;
  onSkillDraftDetected?: (skill: { name?: string; description?: string; instructions?: string }) => void;
  onRefreshConversations?: () => void;
}

export function extractToolDraftFromText(text: string): {
  name?: string;
  toolCode?: string;
  verifyCode?: string;
} {
  const result: { name?: string; toolCode?: string; verifyCode?: string } = {};

  const codeBlockRegex = /```(?:python|py)?\n([\s\S]*?)(?:```|$)/g;
  const blocks: string[] = [];
  let match;
  while ((match = codeBlockRegex.exec(text)) !== null) {
    if (match[1] && match[1].trim()) {
      blocks.push(match[1]);
    }
  }

  for (const block of blocks) {
    if (block.includes('assert ') || block.includes('def test_') || block.includes('verify.py')) {
      result.verifyCode = block;
    } else if (block.includes('def ') || block.includes('import ')) {
      result.toolCode = block;
      const fnMatch = /def\s+([a-zA-Z0-9_]+)\s*\(/.exec(block);
      if (fnMatch && fnMatch[1] && !['test_', 'main', 'execute'].includes(fnMatch[1])) {
        result.name = fnMatch[1];
      }
    }
  }

  const nameMatch = /(?:tool\s*name|tool_name)\s*[:=]\s*[`"']?([a-zA-Z0-9_-]+)[`"']?/i.exec(text);
  if (nameMatch && nameMatch[1]) {
    result.name = nameMatch[1];
  }

  return result;
}

export function extractSkillDraftFromText(text: string): {
  name?: string;
  description?: string;
  instructions?: string;
} {
  const result: { name?: string; description?: string; instructions?: string } = {};

  const fmMatch = /^---\s*\n([\s\S]*?)\n---\s*\n([\s\S]*)$/m.exec(text);
  if (fmMatch) {
    const yaml = fmMatch[1];
    result.instructions = text;
    const nameMatch = /name:\s*([a-zA-Z0-9_-]+)/i.exec(yaml);
    if (nameMatch) result.name = nameMatch[1];
    const descMatch = /description:\s*([^\n]+)/i.exec(yaml);
    if (descMatch) result.description = descMatch[1];
    return result;
  }

  const mdBlockMatch = /```(?:markdown|md)?\n([\s\S]*?)(?:```|$)/.exec(text);
  if (mdBlockMatch && mdBlockMatch[1] && (mdBlockMatch[1].includes('# ') || mdBlockMatch[1].includes('---'))) {
    const content = mdBlockMatch[1];
    result.instructions = content;
    const nameMatch = /(?:name|skill_name)\s*[:=]\s*[`"']?([a-zA-Z0-9_-]+)[`"']?/i.exec(content) ||
                      /(?:name|skill_name)\s*[:=]\s*[`"']?([a-zA-Z0-9_-]+)[`"']?/i.exec(text);
    if (nameMatch) result.name = nameMatch[1];
    const descMatch = /description:\s*([^\n]+)/i.exec(content);
    if (descMatch) result.description = descMatch[1];
    return result;
  }

  if (text.includes('# ')) {
    const headingIndex = text.indexOf('# ');
    const candidateInstructions = text.slice(headingIndex);
    if (candidateInstructions.length > 30) {
      result.instructions = candidateInstructions;
      const headingMatch = /#\s+([^\n]+)/.exec(candidateInstructions);
      if (headingMatch && headingMatch[1]) {
        result.name = headingMatch[1].toLowerCase().replace(/[^a-z0-9_-]/g, '_');
      }
    }
  }

  return result;
}

interface GroupedTurn {
  id: string;
  role: 'user' | 'assistant';
  content?: string;
  thinking?: string;
  toolCalls: {
    id: string;
    name: string;
    argumentsStr: string;
    output?: string;
    isError?: boolean;
  }[];
}

export function groupMessagesIntoTurns(messages: Message[]): GroupedTurn[] {
  const turns: GroupedTurn[] = [];
  let currentAssistantTurn: GroupedTurn | null = null;
  const toolOutputsMap: Record<string, { output: string; name?: string; isError?: boolean }> = {};

  // First pass: collect tool outputs by tool_call_id
  for (const msg of messages) {
    if (msg.role === 'tool' && msg.tool_call_id) {
      toolOutputsMap[msg.tool_call_id] = {
        output: msg.content || '',
        name: msg.name || undefined,
        isError: msg.content?.startsWith('Error:') || msg.content?.startsWith('✗'),
      };
    }
  }

  for (let index = 0; index < messages.length; index++) {
    const msg = messages[index];
    if (msg.role === 'user') {
      if (currentAssistantTurn) {
        turns.push(currentAssistantTurn);
        currentAssistantTurn = null;
      }
      turns.push({
        id: msg.id || `user_${index}`,
        role: 'user',
        content: msg.content || '',
        toolCalls: [],
      });
    } else if (msg.role === 'assistant') {
      if (!currentAssistantTurn) {
        currentAssistantTurn = {
          id: msg.id || `assistant_${index}`,
          role: 'assistant',
          content: msg.content || '',
          thinking: msg.thinking || undefined,
          toolCalls: [],
        };
      } else {
        if (msg.content) {
          currentAssistantTurn.content = currentAssistantTurn.content 
            ? `${currentAssistantTurn.content}\n\n${msg.content}`
            : msg.content;
        }
        if (msg.thinking) {
          currentAssistantTurn.thinking = currentAssistantTurn.thinking
            ? `${currentAssistantTurn.thinking}\n\n${msg.thinking}`
            : msg.thinking;
        }
      }

      if (msg.tool_calls) {
        for (const tc of msg.tool_calls) {
          const matched = toolOutputsMap[tc.id];
          currentAssistantTurn.toolCalls.push({
            id: tc.id,
            name: tc.function.name,
            argumentsStr: tc.function.arguments,
            output: matched?.output,
            isError: matched?.isError,
          });
        }
      }
    }
  }

  if (currentAssistantTurn) {
    turns.push(currentAssistantTurn);
  }

  return turns;
}

export const ChatPane: React.FC<ChatPaneProps> = ({
  conversationId,
  agentId = 'general',
  agentName = 'Kayak Agent',
  agentModel,
  placeholder,
  showHeader = true,
  headerTitle,
  headerBadge,
  headerSubtitle,
  fullWidthInput = false,
  onSendMessage,
  onConversationCreated,
  onToolDraftDetected,
  onSkillDraftDetected,
  onRefreshConversations,
}) => {
  const dialog = useDialog();
  const [messages, setMessages] = useState<Message[]>([]);
  const [inputContent, setInputContent] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [streamingTokenText, setStreamingTokenText] = useState('');
  const [streamingThinkingText, setStreamingThinkingText] = useState('');
  const [activeToolExecutions, setActiveToolExecutions] = useState<
    Record<string, { name: string; args: string; output?: string; isError?: boolean }>
  >({});

  const [pendingApprovals, setPendingApprovals] = useState<ToolApprovalRequest[]>([]);
  const [streamWarning, setStreamWarning] = useState<string | null>(null);

  const isVllmAgent = Boolean(agentModel?.startsWith('vllm/'));
  const vllmModelId = isVllmAgent && agentModel ? agentModel.slice('vllm/'.length) : null;
  const { status: vllmStatus, refresh: refreshVllmStatus } = useVLLMStatus();
  const [isStartingVllm, setIsStartingVllm] = useState(false);

  const loadingStates = VLLM_LOADING_STATES;
  const isVllmModelReady = !isVllmAgent || (vllmStatus?.state === 'ready' && vllmStatus?.model_id === vllmModelId);
  const isVllmModelLoading = isVllmAgent && (isStartingVllm || (vllmStatus !== null && loadingStates.includes(vllmStatus.state) && (vllmStatus.model_id === vllmModelId || !vllmStatus.model_id)));
  const isVllmModelOffline = isVllmAgent && !isVllmModelReady && !isVllmModelLoading;

  const handleStartVllmModel = async () => {
    if (!vllmModelId) return;
    setIsStartingVllm(true);
    try {
      await api.deployVLLMModel({ model_id: vllmModelId });
      await refreshVllmStatus();
    } catch (err) {
      console.error('Failed to start vLLM model:', err);
      dialog.alert({
        title: 'Model Startup Failed',
        message: `Could not launch ${vllmModelId}: ${err}`,
        variant: 'danger',
      });
    } finally {
      setIsStartingVllm(false);
    }
  };

  const messagesContainerRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = (smooth = true) => {
    if (messagesContainerRef.current) {
      messagesContainerRef.current.scrollTo({
        top: messagesContainerRef.current.scrollHeight,
        behavior: smooth ? 'smooth' : 'auto',
      });
    }
  };

  const parseToolArguments = (name: string, argsStr: string, output?: string, isError?: boolean) => {
    try {
      const parsed = typeof argsStr === 'string' ? JSON.parse(argsStr) : argsStr;
      if (name === 'verify_tool' || name === 'activate_tool') {
        if (parsed.tool_name || parsed.tool_code || parsed.verify_code) {
          onToolDraftDetected?.({
            name: parsed.tool_name,
            toolCode: parsed.tool_code,
            verifyCode: parsed.verify_code,
            verifyOutput: output,
            isSuccess: output ? !isError && output.includes('Verification Passed') : undefined,
          });
        }
      } else if (name === 'create_or_update_skill') {
        if (parsed.name || parsed.instructions) {
          onSkillDraftDetected?.({
            name: parsed.name,
            description: parsed.description,
            instructions: parsed.instructions,
          });
        }
      }
    } catch {
      // JSON arguments might still be streaming
    }
  };

  const loadConversationData = async () => {
    if (!conversationId) {
      setMessages([]);
      setIsSending(false);
      return;
    }
    try {
      const data = await api.getConversation(conversationId);
      // The database is authoritative here: a reload only happens after the server
      // has persisted the turn, so any optimistic placeholder is already represented.
      // Matching placeholders by content instead would drop a genuine repeat message.
      setMessages(data.messages || []);

      if (data.conversation?.status === 'running') {
        setIsSending(true);
      }

      // Scan existing messages for tool calls and text drafts
      for (const msg of data.messages) {
        if (msg.tool_calls) {
          for (const tc of msg.tool_calls) {
            parseToolArguments(tc.function.name, tc.function.arguments);
          }
        }
        if (msg.role === 'assistant' && msg.content) {
          if (onToolDraftDetected) {
            const extracted = extractToolDraftFromText(msg.content);
            if (extracted.toolCode || extracted.verifyCode || extracted.name) {
              onToolDraftDetected(extracted);
            }
          }
          if (onSkillDraftDetected) {
            const extracted = extractSkillDraftFromText(msg.content);
            if (extracted.instructions || extracted.name || extracted.description) {
              onSkillDraftDetected(extracted);
            }
          }
        }
      }
    } catch (err) {
      console.error('Failed to load conversation messages:', err);
    }
  };

  useEffect(() => {
    loadConversationData();
    setStreamingTokenText('');
    setStreamingThinkingText('');
    setActiveToolExecutions({});
    setPendingApprovals([]);
    setStreamWarning(null);
  }, [conversationId]);

  useEffect(() => {
    scrollToBottom();
  }, [messages, streamingTokenText, streamingThinkingText, activeToolExecutions]);

  useSSE(conversationId, {
    onThinking: (chunk) => {
      setIsSending(true);
      setStreamingThinkingText((prev) => prev + chunk);
    },
    onToken: (token) => {
      setIsSending(true);
      setStreamingTokenText((prev) => {
        const next = prev + token;
        if (onToolDraftDetected) {
          const extracted = extractToolDraftFromText(next);
          if (extracted.toolCode || extracted.verifyCode || extracted.name) {
            onToolDraftDetected(extracted);
          }
        }
        if (onSkillDraftDetected) {
          const extracted = extractSkillDraftFromText(next);
          if (extracted.instructions || extracted.name || extracted.description) {
            onSkillDraftDetected(extracted);
          }
        }
        return next;
      });
    },
    onToolCallDelta: (delta) => {
      setIsSending(true);
      setActiveToolExecutions((prev) => {
        const existing = prev[delta.id] || { name: delta.name || '', args: '' };
        const combinedArgs = existing.args + (delta.arguments || '');
        const updatedName = delta.name || existing.name;

        parseToolArguments(updatedName, combinedArgs);

        return {
          ...prev,
          [delta.id]: {
            ...existing,
            name: updatedName,
            args: combinedArgs,
          },
        };
      });
    },
    onToolCallExecuting: (data) => {
      setIsSending(true);
      parseToolArguments(data.name, data.arguments);
      setActiveToolExecutions((prev) => ({
        ...prev,
        [data.id]: {
          name: data.name,
          args: data.arguments,
        },
      }));
    },
    onToolApprovalRequired: (request) => {
      setIsSending(true);
      setPendingApprovals((prev) =>
        prev.some((item) => item.id === request.id) ? prev : [...prev, request]
      );
    },
    onWarning: (warning) => {
      setStreamWarning(warning);
    },
    onMaxIterations: () => {
      setStreamWarning(
        'The agent hit the maximum number of tool-use steps for one turn and stopped early.'
      );
    },
    onTitleUpdated: () => {
      onRefreshConversations?.();
    },
    onToolCallResult: (data) => {
      setPendingApprovals((prev) => prev.filter((item) => item.id !== data.id));
      setActiveToolExecutions((prev) => {
        const existing = prev[data.id];
        const existingArgs = existing?.args || '';
        parseToolArguments(data.name, existingArgs, data.output, data.is_error);
        return {
          ...prev,
          [data.id]: {
            ...existing,
            name: data.name,
            output: data.output,
            isError: data.is_error,
          },
        };
      });
    },
    onDone: () => {
      setIsSending(false);
      setStreamingTokenText('');
      setStreamingThinkingText('');
      setActiveToolExecutions({});
      setPendingApprovals([]);
      loadConversationData();
      onRefreshConversations?.();
    },
    onCancelled: () => {
      setIsSending(false);
      setStreamingTokenText('');
      setStreamingThinkingText('');
      setActiveToolExecutions({});
      setPendingApprovals([]);
      loadConversationData();
      onRefreshConversations?.();
    },
    onUserMessage: (msg) => {
      setMessages((prev) => {
        if (prev.some((m) => m.id === msg.id)) return prev;
        // Placeholders are confirmed in the order they were sent, so the oldest
        // outstanding one belongs to this echo regardless of its text.
        const optIndex = prev.findIndex((m) => m.id?.startsWith('optimistic_'));
        if (optIndex !== -1) {
          const next = [...prev];
          next[optIndex] = msg;
          return next;
        }
        return [...prev, msg];
      });
    },
    onError: (err) => {
      setIsSending(false);
      dialog.alert({
        title: 'Execution Error',
        message: `Agent encountered an error: ${err}`,
        variant: 'danger',
      });
    },
  });

  const handleSend = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!inputContent.trim() || isSending || (isVllmAgent && !isVllmModelReady)) return;

    const text = inputContent.trim();
    setInputContent('');
    setIsSending(true);

    // Optimistically insert user message immediately for instant UI feedback
    const tempUserMsg: Message = {
      id: `optimistic_${Date.now()}`,
      conversation_id: conversationId || '',
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, tempUserMsg]);

    try {
      if (!conversationId) {
        if (onSendMessage) {
          const newCid = await onSendMessage(text);
          if (newCid) {
            onConversationCreated?.(newCid);
            onRefreshConversations?.();
          }
        } else {
          const conv = await api.createConversation({
            agent_id: agentId,
            initial_message: text,
          });
          onConversationCreated?.(conv.id);
          onRefreshConversations?.();
        }
      } else {
        await api.sendMessage(conversationId, text);
        onRefreshConversations?.();
      }
    } catch (err) {
      setIsSending(false);
      console.error('Failed to send message:', err);
    }
  };

  const handleCancelGeneration = async () => {
    if (!conversationId) return;
    try {
      await api.cancelConversation(conversationId);
      setIsSending(false);
      setStreamingTokenText('');
      setStreamingThinkingText('');
      setActiveToolExecutions({});
      loadConversationData();
      onRefreshConversations?.();
    } catch (err) {
      console.error('Failed to cancel conversation:', err);
    }
  };

  const handleResolveApproval = async (callId: string, approved: boolean) => {
    if (!conversationId) return;
    setPendingApprovals((prev) => prev.filter((item) => item.id !== callId));
    try {
      await api.resolveToolApproval(conversationId, callId, approved);
    } catch (err) {
      console.error('Failed to submit tool approval:', err);
      dialog.alert({
        title: 'Approval Failed',
        message: `Could not record your decision: ${err}`,
        variant: 'danger',
      });
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Markdown renderer for Assistant messages
  const assistantMarkdownComponents = {
    h1({ children, ...props }: any) {
      return <h1 className="text-xl font-bold text-md-on-surface mt-6 mb-3 tracking-tight" {...props}>{children}</h1>;
    },
    h2({ children, ...props }: any) {
      return <h2 className="text-lg font-bold text-md-on-surface mt-5 mb-2 tracking-tight" {...props}>{children}</h2>;
    },
    h3({ children, ...props }: any) {
      return <h3 className="text-base font-semibold text-md-on-surface mt-4 mb-1.5" {...props}>{children}</h3>;
    },
    p({ children, ...props }: any) {
      return <p className="mb-3.5 text-md-on-surface text-[15px] leading-relaxed font-normal" {...props}>{children}</p>;
    },
    ul({ children, ...props }: any) {
      return <ul className="list-disc pl-5 my-3 space-y-1.5 text-md-on-surface text-[15px] leading-relaxed" {...props}>{children}</ul>;
    },
    ol({ children, ...props }: any) {
      return <ol className="list-decimal pl-5 my-3 space-y-1.5 text-md-on-surface text-[15px] leading-relaxed" {...props}>{children}</ol>;
    },
    li({ children, ...props }: any) {
      return <li className="leading-relaxed text-[15px] text-md-on-surface" {...props}>{children}</li>;
    },
    blockquote({ children, ...props }: any) {
      return (
        <blockquote className="border-l-3 border-md-primary pl-4 py-1.5 my-3.5 text-md-on-surface bg-md-primary-container/30 rounded-r-xl italic text-[14px] leading-relaxed" {...props}>
          {children}
        </blockquote>
      );
    },
    table({ children, ...props }: any) {
      return (
        <div className="my-4 overflow-x-auto rounded-xl border border-md-outline-variant shadow-xs">
          <table className="w-full text-left text-xs border-collapse" {...props}>
            {children}
          </table>
        </div>
      );
    },
    th({ children, ...props }: any) {
      return <th className="bg-md-surface-container-high p-3 font-bold text-md-on-surface border-b border-md-outline-variant" {...props}>{children}</th>;
    },
    td({ children, ...props }: any) {
      return <td className="p-3 border-b border-md-outline-variant text-md-on-surface" {...props}>{children}</td>;
    },
    code({ node, inline, className, children, ...props }: any) {
      const match = /language-(\w+)/.exec(className || '');
      const codeStr = String(children).replace(/\n$/, '');
      if (!inline && (match || codeStr.includes('\n'))) {
        return (
          <div className="my-3.5">
            <CodeBlock
              language={match ? match[1] : 'text'}
              code={codeStr}
            />
          </div>
        );
      }
      return (
        <code className="px-1.5 py-0.5 rounded-md bg-md-surface-container-high border border-md-outline-variant text-md-on-surface font-mono text-[13px]" {...props}>
          {children}
        </code>
      );
    }
  };

  // Markdown renderer for User message bubble
  const userMarkdownComponents = {
    h1({ children, ...props }: any) {
      return <h1 className="text-lg font-bold text-md-on-primary my-2" {...props}>{children}</h1>;
    },
    h2({ children, ...props }: any) {
      return <h2 className="text-base font-bold text-md-on-primary my-1.5" {...props}>{children}</h2>;
    },
    h3({ children, ...props }: any) {
      return <h3 className="text-sm font-bold text-md-on-primary my-1" {...props}>{children}</h3>;
    },
    p({ children, ...props }: any) {
      return <p className="text-md-on-primary text-[15px] leading-relaxed mb-2 last:mb-0 font-normal" {...props}>{children}</p>;
    },
    ul({ children, ...props }: any) {
      return <ul className="list-disc pl-5 my-2 text-md-on-primary text-[15px] space-y-1" {...props}>{children}</ul>;
    },
    ol({ children, ...props }: any) {
      return <ol className="list-decimal pl-5 my-2 text-md-on-primary text-[15px] space-y-1" {...props}>{children}</ol>;
    },
    li({ children, ...props }: any) {
      return <li className="text-md-on-primary leading-relaxed text-[15px]" {...props}>{children}</li>;
    },
    strong({ children, ...props }: any) {
      return <strong className="font-bold text-md-on-primary" {...props}>{children}</strong>;
    },
    code({ node, inline, className, children, ...props }: any) {
      const codeStr = String(children).replace(/\n$/, '');
      return (
        <code className="px-1.5 py-0.5 rounded bg-md-on-primary/20 border border-md-on-primary/30 text-md-on-primary font-mono text-[13px]" {...props}>
          {codeStr}
        </code>
      );
    }
  };

  const groupedTurns = groupMessagesIntoTurns(messages);

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 bg-md-surface overflow-hidden transition-colors">
      {/* Optional Sub-Header */}
      {showHeader && (
        <div className="p-3 border-b border-md-outline-variant bg-md-surface-container-low flex items-center justify-between text-xs font-bold uppercase tracking-wider text-md-on-surface shrink-0 transition-colors">
          <div className="flex items-center space-x-2">
            <Bot className="w-4 h-4 text-md-primary" />
            <span>{headerTitle || agentName}</span>
          </div>
          {headerBadge && (
            <span className="text-[10px] font-mono text-md-on-surface-variant font-normal">{headerBadge}</span>
          )}
        </div>
      )}

      {/* Messages Scroll Area - Constrained to max-w-3xl for optimal line length and reading ergonomics */}
      <div ref={messagesContainerRef} className="flex-1 min-h-0 overflow-y-auto px-4 sm:px-6 py-6">
        <div className="max-w-3xl mx-auto w-full space-y-6">
          {groupedTurns.length === 0 && !streamingTokenText && !streamingThinkingText && Object.keys(activeToolExecutions).length === 0 && (
            <div className="text-center py-20 space-y-3">
              <div className="w-12 h-12 rounded-2xl bg-md-primary-container text-md-on-primary-container border border-md-outline-variant flex items-center justify-center mx-auto shadow-xs">
                <Sparkles className="w-6 h-6" />
              </div>
              <h3 className="text-sm font-bold text-md-on-surface">{headerTitle || `Chat with ${agentName}`}</h3>
              <p className="text-xs text-md-on-surface-variant max-w-sm mx-auto leading-relaxed">
                {headerSubtitle || 'Type your instructions below to begin interactive agent synthesis and refinement.'}
              </p>
            </div>
          )}

          {groupedTurns.map((turn) => {
            if (turn.role === 'user') {
              return (
                <div key={turn.id} className="flex justify-end pt-2">
                  <div className="max-w-2xl bg-md-primary text-md-on-primary rounded-2xl rounded-tr-xs px-5 py-3 text-[15px] shadow-xs leading-relaxed">
                    <ReactMarkdown 
                      remarkPlugins={[remarkGfm, remarkMath]} 
                      rehypePlugins={[rehypeKatex]}
                      components={userMarkdownComponents}
                    >
                      {turn.content || ''}
                    </ReactMarkdown>
                  </div>
                </div>
              );
            }

            if (turn.role === 'assistant') {
              return (
                <div key={turn.id} className="w-full space-y-2 pt-2">
                  {/* Collapsible Accordion for Thinking / Reasoning Tokens */}
                  {turn.thinking && (
                    <ThinkingAccordion content={turn.thinking} />
                  )}

                  {/* Direct Response Text on Background without enclosing white card or icons */}
                  {turn.content && (
                    <div className="text-md-on-surface text-[15px] leading-relaxed">
                      <ReactMarkdown 
                        remarkPlugins={[remarkGfm, remarkMath]} 
                        rehypePlugins={[rehypeKatex]}
                        components={assistantMarkdownComponents}
                      >
                        {turn.content}
                      </ReactMarkdown>
                    </div>
                  )}

                  {/* Collapsible Dropdown Button for Tool Calls */}
                  {turn.toolCalls && turn.toolCalls.length > 0 && (
                    <ToolCallsAccordion
                      toolCalls={turn.toolCalls.map((tc) => ({
                        id: tc.id,
                        name: tc.name,
                        argumentsStr: tc.argumentsStr,
                        output: tc.output,
                        isError: tc.isError,
                      }))}
                    />
                  )}
                </div>
              );
            }

            return null;
          })}

          {/* Live Streaming Active Turn */}
          {(isSending || streamingTokenText || streamingThinkingText || Object.keys(activeToolExecutions).length > 0) && (
            <div className="w-full space-y-2 pt-2 animate-fade-in">
              {/* Agent Preparing / Thinking Loader before first token arrives */}
              {isSending && !streamingTokenText && !streamingThinkingText && Object.keys(activeToolExecutions).length === 0 && (
                <div className="flex items-center space-x-2.5 py-3 text-md-on-surface-variant animate-pulse">
                  <div className="w-7 h-7 rounded-xl bg-md-primary-container border border-md-outline-variant flex items-center justify-center text-md-on-primary-container shrink-0">
                    <Loader2 className="w-4 h-4 animate-spin" />
                  </div>
                  <span className="text-xs font-medium text-md-on-surface-variant">Reaching model...</span>
                </div>
              )}

              {/* Live Streaming Thinking Process */}
              {streamingThinkingText && (
                <ThinkingAccordion
                  content={streamingThinkingText}
                  isThinking={!streamingTokenText}
                  defaultExpanded={true}
                />
              )}

              {/* Streaming token text directly on background */}
              {streamingTokenText && (
                <div className="text-md-on-surface text-[15px] leading-relaxed">
                  <ReactMarkdown 
                    remarkPlugins={[remarkGfm, remarkMath]} 
                    rehypePlugins={[rehypeKatex]}
                    components={assistantMarkdownComponents}
                  >
                    {streamingTokenText}
                  </ReactMarkdown>
                </div>
              )}

              {/* Tool calls held for explicit user approval */}
              {pendingApprovals.map((approval) => (
                <div
                  key={approval.id}
                  className="rounded-xl border border-amber-300 dark:border-amber-800/80 bg-amber-50 dark:bg-amber-950/40 p-3.5 space-y-2.5"
                >
                  <div className="flex items-center gap-2 text-xs font-bold text-amber-900 dark:text-amber-100">
                    <ShieldQuestion className="w-4 h-4 shrink-0" />
                    <span>Approval required to run</span>
                    <code className="font-mono px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-900/60 border border-amber-300 dark:border-amber-800">
                      {approval.name}
                    </code>
                  </div>
                  {approval.arguments && (
                    <pre className="text-[11px] font-mono text-amber-900 dark:text-amber-100/90 bg-amber-100/70 dark:bg-amber-900/40 rounded-lg p-2.5 overflow-x-auto max-h-40">
                      {approval.arguments}
                    </pre>
                  )}
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => handleResolveApproval(approval.id, true)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold bg-md-primary text-md-on-primary hover:opacity-90 transition-opacity cursor-pointer"
                    >
                      <Check className="w-3.5 h-3.5 stroke-[3]" /> Approve
                    </button>
                    <button
                      type="button"
                      onClick={() => handleResolveApproval(approval.id, false)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold bg-md-surface-container-high text-md-on-surface border border-md-outline-variant hover:opacity-90 transition-opacity cursor-pointer"
                    >
                      <X className="w-3.5 h-3.5 stroke-[3]" /> Deny
                    </button>
                  </div>
                </div>
              ))}

              {/* Active Executing Tool Calls Live */}
              {Object.keys(activeToolExecutions).length > 0 && (
                <div className="space-y-2 pt-1">
                  {Object.entries(activeToolExecutions).map(([id, t]) => (
                    <ToolCallCard
                      key={id}
                      name={t.name}
                      argumentsStr={t.args}
                      output={t.output}
                      isExecuting={!t.output}
                      isError={t.isError}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Degraded-run notice: a tool-less retry or an iteration ceiling changes what
          the agent was actually able to do, so it is surfaced rather than logged. */}
      {streamWarning && (
        <div className="px-4 pt-3 shrink-0">
          <div className={`${fullWidthInput ? 'w-full' : 'max-w-3xl mx-auto'} flex items-start gap-2 rounded-xl border border-amber-300 dark:border-amber-800/80 bg-amber-50 dark:bg-amber-950/40 px-3.5 py-2.5`}>
            <AlertCircle className="w-4 h-4 text-amber-700 dark:text-amber-300 shrink-0 mt-0.5" />
            <p className="text-xs text-amber-900 dark:text-amber-100 leading-relaxed flex-1">{streamWarning}</p>
            <button
              type="button"
              onClick={() => setStreamWarning(null)}
              className="text-amber-700 dark:text-amber-300 hover:opacity-70 transition-opacity cursor-pointer"
              title="Dismiss"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}

      {/* Input Composer - Constrained to max-w-3xl for standard chat, full width in studio mode */}
      <div className="p-4 border-t border-md-outline-variant bg-md-surface shrink-0 transition-colors">
        <form
          onSubmit={handleSend}
          className={`${fullWidthInput ? 'w-full' : 'max-w-3xl mx-auto'} relative bg-md-surface-container-lowest border border-md-outline-variant rounded-2xl overflow-hidden focus-within:border-md-primary focus-within:ring-2 focus-within:ring-md-primary/20 shadow-xs transition-all`}
        >
          <textarea
            value={inputContent}
            onChange={(e) => setInputContent(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={2}
            disabled={isVllmModelOffline || isVllmModelLoading}
            placeholder={
              isVllmModelOffline
                ? `Local model ${vllmModelId} is offline. Click 'Start Model' to begin...`
                : isVllmModelLoading
                ? `Local model ${vllmModelId} is initializing...`
                : placeholder || `Message ${agentName}... (Enter to send, Shift+Enter for new line)`
            }
            className="w-full bg-transparent px-4 py-3 text-xs text-md-on-surface placeholder:text-md-on-surface-variant/70 focus:outline-none resize-none leading-relaxed disabled:opacity-60 disabled:cursor-not-allowed"
          />

          <div className="flex items-center justify-between px-3.5 py-2 bg-md-surface-container-low border-t border-md-outline-variant">
            <div className="flex items-center space-x-2 text-[11px] text-md-on-surface-variant">
              <Sparkles className="w-3.5 h-3.5 text-md-primary" />
              <span className="font-semibold text-md-on-surface">{agentName}</span>
              {agentModel && (
                <span className="font-mono text-[10px] text-md-on-surface-variant">
                  ({agentModel.split('/')[1] || agentModel})
                </span>
              )}
              {isVllmAgent && (
                isVllmModelReady ? (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold text-emerald-800 dark:text-emerald-200 bg-emerald-100 dark:bg-emerald-950/80 border border-emerald-300 dark:border-emerald-800/80 px-2 py-0.5 rounded-full">
                    <CheckCircle2 className="w-2.5 h-2.5 stroke-[2.5]" /> Serving
                  </span>
                ) : isVllmModelLoading ? (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold text-amber-800 dark:text-amber-200 bg-amber-100 dark:bg-amber-950/80 border border-amber-300 dark:border-amber-800/80 px-2 py-0.5 rounded-full">
                    <Loader2 className="w-2.5 h-2.5 animate-spin" /> Loading
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold text-md-on-surface-variant bg-md-surface-container-highest border border-md-outline-variant px-2 py-0.5 rounded-full">
                    Offline
                  </span>
                )
              )}
            </div>

            <div className="flex items-center space-x-2">
              {isSending ? (
                <button
                  type="button"
                  onClick={handleCancelGeneration}
                  className="inline-flex items-center space-x-1 px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-md-error text-md-on-error hover:opacity-90 transition-opacity shadow-xs cursor-pointer"
                >
                  <Square className="w-3 h-3 fill-current" />
                  <span>Stop</span>
                </button>
              ) : isVllmModelOffline ? (
                <button
                  type="button"
                  onClick={handleStartVllmModel}
                  disabled={isStartingVllm}
                  className="inline-flex items-center space-x-1.5 px-3.5 py-1.5 rounded-lg text-xs font-bold bg-md-primary text-md-on-primary hover:opacity-90 disabled:opacity-50 transition-opacity shadow-xs cursor-pointer"
                >
                  {isStartingVllm ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5 fill-current" />}
                  <span>Start Model</span>
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!inputContent.trim() || isVllmModelLoading}
                  className="inline-flex items-center space-x-1 px-4 py-1.5 rounded-lg text-xs font-semibold bg-md-primary text-md-on-primary hover:opacity-90 disabled:opacity-40 disabled:hover:opacity-40 transition-opacity shadow-xs cursor-pointer"
                >
                  <span>{isVllmModelLoading ? 'Model Loading...' : 'Send'}</span>
                  {isVllmModelLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
                </button>
              )}
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
