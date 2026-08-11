import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { Conversation, Message, VLLMDeploymentProgress } from '../types';
import { api } from '../api/client';
import { useSSE } from '../hooks/useSSE';
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
  AlertCircle
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

  const isVllmAgent = Boolean(agentModel?.startsWith('vllm/'));
  const vllmModelId = isVllmAgent && agentModel ? agentModel.slice('vllm/'.length) : null;
  const [vllmStatus, setVllmStatus] = useState<VLLMDeploymentProgress | null>(null);
  const [isStartingVllm, setIsStartingVllm] = useState(false);

  useEffect(() => {
    if (!isVllmAgent) return;
    api.getVLLMStatus()
      .then(setVllmStatus)
      .catch(() => {});

    const es = new EventSource('/api/vllm/events');
    const handleStatus = (e: MessageEvent) => {
      try {
        const payload = JSON.parse(e.data);
        if (payload.data) setVllmStatus(payload.data);
      } catch {}
    };
    es.addEventListener('status', handleStatus);
    es.addEventListener('update', handleStatus);
    return () => {
      es.close();
    };
  }, [isVllmAgent, agentModel]);

  const loadingStates = ['pulling_image', 'starting_container', 'loading'];
  const isVllmModelReady = !isVllmAgent || (vllmStatus?.state === 'ready' && vllmStatus?.model_id === vllmModelId);
  const isVllmModelLoading = isVllmAgent && (isStartingVllm || (vllmStatus !== null && loadingStates.includes(vllmStatus.state) && (vllmStatus.model_id === vllmModelId || !vllmStatus.model_id)));
  const isVllmModelOffline = isVllmAgent && !isVllmModelReady && !isVllmModelLoading;

  const handleStartVllmModel = async () => {
    if (!vllmModelId) return;
    setIsStartingVllm(true);
    try {
      const st = await api.deployVLLMModel({ model_id: vllmModelId });
      setVllmStatus(st);
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
      setMessages((prev) => {
        const dbMsgs = data.messages || [];
        const pendingOptimistic = prev.filter(
          (pm) => pm.id?.startsWith('optimistic_') && !dbMsgs.some((dm) => dm.role === 'user' && dm.content === pm.content)
        );
        return [...dbMsgs, ...pendingOptimistic];
      });

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
    onToolCallResult: (data) => {
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
      loadConversationData();
      onRefreshConversations?.();
    },
    onCancelled: () => {
      setIsSending(false);
      setStreamingTokenText('');
      setStreamingThinkingText('');
      setActiveToolExecutions({});
      loadConversationData();
      onRefreshConversations?.();
    },
    onUserMessage: (msg) => {
      setMessages((prev) => {
        if (prev.some((m) => m.id === msg.id)) return prev;
        const optIndex = prev.findIndex(
          (m) => m.id?.startsWith('optimistic_') && m.content === msg.content
        );
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

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Markdown renderer for Assistant messages (on light background)
  const assistantMarkdownComponents = {
    h1({ children, ...props }: any) {
      return <h1 className="text-xl font-bold text-zinc-950 mt-6 mb-3 tracking-tight" {...props}>{children}</h1>;
    },
    h2({ children, ...props }: any) {
      return <h2 className="text-lg font-bold text-zinc-900 mt-5 mb-2 tracking-tight" {...props}>{children}</h2>;
    },
    h3({ children, ...props }: any) {
      return <h3 className="text-base font-semibold text-zinc-900 mt-4 mb-1.5" {...props}>{children}</h3>;
    },
    p({ children, ...props }: any) {
      return <p className="mb-3.5 text-zinc-850 text-[15px] leading-relaxed font-normal" {...props}>{children}</p>;
    },
    ul({ children, ...props }: any) {
      return <ul className="list-disc pl-5 my-3 space-y-1.5 text-zinc-850 text-[15px] leading-relaxed" {...props}>{children}</ul>;
    },
    ol({ children, ...props }: any) {
      return <ol className="list-decimal pl-5 my-3 space-y-1.5 text-zinc-850 text-[15px] leading-relaxed" {...props}>{children}</ol>;
    },
    li({ children, ...props }: any) {
      return <li className="leading-relaxed text-[15px]" {...props}>{children}</li>;
    },
    blockquote({ children, ...props }: any) {
      return (
        <blockquote className="border-l-3 border-indigo-500 pl-4 py-1.5 my-3.5 text-zinc-700 bg-indigo-50/40 rounded-r-xl italic text-[14px] leading-relaxed" {...props}>
          {children}
        </blockquote>
      );
    },
    table({ children, ...props }: any) {
      return (
        <div className="my-4 overflow-x-auto rounded-xl border border-zinc-200 shadow-xs">
          <table className="w-full text-left text-xs border-collapse" {...props}>
            {children}
          </table>
        </div>
      );
    },
    th({ children, ...props }: any) {
      return <th className="bg-zinc-100/80 p-3 font-bold text-zinc-800 border-b border-zinc-200" {...props}>{children}</th>;
    },
    td({ children, ...props }: any) {
      return <td className="p-3 border-b border-zinc-100 text-zinc-700" {...props}>{children}</td>;
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
        <code className="px-1.5 py-0.5 rounded-md bg-zinc-200/70 border border-zinc-300/60 text-zinc-900 font-mono text-[13px]" {...props}>
          {children}
        </code>
      );
    }
  };

  // Markdown renderer for User message bubble (pure white text on dark background)
  const userMarkdownComponents = {
    h1({ children, ...props }: any) {
      return <h1 className="text-lg font-bold text-white my-2" {...props}>{children}</h1>;
    },
    h2({ children, ...props }: any) {
      return <h2 className="text-base font-bold text-white my-1.5" {...props}>{children}</h2>;
    },
    h3({ children, ...props }: any) {
      return <h3 className="text-sm font-bold text-white my-1" {...props}>{children}</h3>;
    },
    p({ children, ...props }: any) {
      return <p className="text-white text-[15px] leading-relaxed mb-2 last:mb-0 font-normal" {...props}>{children}</p>;
    },
    ul({ children, ...props }: any) {
      return <ul className="list-disc pl-5 my-2 text-white text-[15px] space-y-1" {...props}>{children}</ul>;
    },
    ol({ children, ...props }: any) {
      return <ol className="list-decimal pl-5 my-2 text-white text-[15px] space-y-1" {...props}>{children}</ol>;
    },
    li({ children, ...props }: any) {
      return <li className="text-white leading-relaxed text-[15px]" {...props}>{children}</li>;
    },
    strong({ children, ...props }: any) {
      return <strong className="font-bold text-white" {...props}>{children}</strong>;
    },
    code({ node, inline, className, children, ...props }: any) {
      const codeStr = String(children).replace(/\n$/, '');
      return (
        <code className="px-1.5 py-0.5 rounded bg-zinc-800 border border-zinc-700 text-zinc-100 font-mono text-[13px]" {...props}>
          {codeStr}
        </code>
      );
    }
  };

  const groupedTurns = groupMessagesIntoTurns(messages);

  return (
    <div className="flex-1 flex flex-col h-full min-h-0 bg-zinc-50 overflow-hidden">
      {/* Optional Sub-Header */}
      {showHeader && (
        <div className="p-3 border-b border-zinc-200 bg-white flex items-center justify-between text-xs font-bold uppercase tracking-wider text-zinc-700 shrink-0">
          <div className="flex items-center space-x-2">
            <Bot className="w-4 h-4 text-indigo-600" />
            <span>{headerTitle || agentName}</span>
          </div>
          {headerBadge && (
            <span className="text-[10px] font-mono text-zinc-500 font-normal">{headerBadge}</span>
          )}
        </div>
      )}

      {/* Messages Scroll Area - Constrained to max-w-3xl for optimal line length and reading ergonomics */}
      <div ref={messagesContainerRef} className="flex-1 min-h-0 overflow-y-auto px-4 sm:px-6 py-6">
        <div className="max-w-3xl mx-auto w-full space-y-6">
          {groupedTurns.length === 0 && !streamingTokenText && !streamingThinkingText && Object.keys(activeToolExecutions).length === 0 && (
            <div className="text-center py-20 space-y-3">
              <div className="w-12 h-12 rounded-2xl bg-indigo-50 border border-indigo-200 flex items-center justify-center text-indigo-600 mx-auto shadow-xs">
                <Sparkles className="w-6 h-6" />
              </div>
              <h3 className="text-sm font-bold text-zinc-900">{headerTitle || `Chat with ${agentName}`}</h3>
              <p className="text-xs text-zinc-500 max-w-sm mx-auto leading-relaxed">
                {headerSubtitle || 'Type your instructions below to begin interactive agent synthesis and refinement.'}
              </p>
            </div>
          )}

          {groupedTurns.map((turn) => {
            if (turn.role === 'user') {
              return (
                <div key={turn.id} className="flex justify-end pt-2">
                  <div className="max-w-2xl bg-zinc-900 text-white rounded-2xl rounded-tr-xs px-5 py-3 text-[15px] shadow-xs leading-relaxed">
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
                    <div className="text-zinc-800 text-[15px] leading-relaxed">
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
                <div className="flex items-center space-x-2.5 py-3 text-zinc-500 animate-pulse">
                  <div className="w-7 h-7 rounded-xl bg-indigo-50 border border-indigo-200/80 flex items-center justify-center text-indigo-600 shrink-0">
                    <Loader2 className="w-4 h-4 animate-spin" />
                  </div>
                  <span className="text-xs font-medium text-zinc-500">Reaching model...</span>
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
                <div className="text-zinc-800 text-[15px] leading-relaxed">
                  <ReactMarkdown 
                    remarkPlugins={[remarkGfm, remarkMath]} 
                    rehypePlugins={[rehypeKatex]}
                    components={assistantMarkdownComponents}
                  >
                    {streamingTokenText}
                  </ReactMarkdown>
                </div>
              )}

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

      {/* Input Composer - Constrained to match max-w-3xl */}
      <div className="p-4 border-t border-zinc-200 bg-white shrink-0">
        {/* Model Status Banner for local vLLM agents */}
        {isVllmModelOffline && (
          <div className="max-w-3xl mx-auto mb-3 p-3 rounded-xl bg-amber-50 border border-amber-200 text-amber-900 text-xs flex items-center justify-between shadow-2xs">
            <div className="flex items-center space-x-2">
              <AlertCircle className="w-4 h-4 text-amber-600 shrink-0" />
              <span>
                Local model <code className="font-mono font-bold text-amber-950">{vllmModelId}</code> is offline.
              </span>
            </div>
            <button
              type="button"
              onClick={handleStartVllmModel}
              disabled={isStartingVllm}
              className="inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white text-xs font-bold transition-colors shadow-xs cursor-pointer"
            >
              {isStartingVllm ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5 fill-white" />}
              <span>Start Model</span>
            </button>
          </div>
        )}

        {isVllmModelLoading && (
          <div className="max-w-3xl mx-auto mb-3 p-3 rounded-xl bg-indigo-50 border border-indigo-200 text-indigo-900 text-xs flex items-center space-x-2.5 shadow-2xs">
            <Loader2 className="w-4 h-4 animate-spin text-indigo-600 shrink-0" />
            <span className="truncate">
              Local model <code className="font-mono font-bold text-indigo-950">{vllmModelId}</code> is starting up ({vllmStatus?.message || 'Provisioning container...'})
            </span>
          </div>
        )}

        <form
          onSubmit={handleSend}
          className="max-w-3xl mx-auto relative bg-zinc-50 border border-zinc-300 rounded-xl overflow-hidden focus-within:bg-white focus-within:border-indigo-600 focus-within:ring-1 focus-within:ring-indigo-600 shadow-xs transition-all"
        >
          <textarea
            value={inputContent}
            onChange={(e) => setInputContent(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={2}
            disabled={isVllmModelOffline || isVllmModelLoading}
            placeholder={
              isVllmModelOffline
                ? `Local model ${vllmModelId} is offline. Click 'Start Model' above to begin...`
                : isVllmModelLoading
                ? `Local model ${vllmModelId} is initializing...`
                : placeholder || `Message ${agentName}... (Enter to send, Shift+Enter for new line)`
            }
            className="w-full bg-transparent px-3.5 py-2.5 text-xs text-zinc-900 placeholder-zinc-400 focus:outline-none resize-none leading-relaxed disabled:opacity-50 disabled:cursor-not-allowed"
          />

          <div className="flex items-center justify-between px-3.5 py-2 bg-zinc-100/50 border-t border-zinc-200">
            <div className="flex items-center space-x-2 text-[11px] text-zinc-500">
              <Sparkles className="w-3.5 h-3.5 text-indigo-600" />
              <span className="font-semibold text-zinc-700">{agentName}</span>
              {agentModel && (
                <span className="font-mono text-[10px] text-zinc-400">
                  ({agentModel.split('/')[1] || agentModel})
                </span>
              )}
              {isVllmAgent && (
                isVllmModelReady ? (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded-full">
                    <CheckCircle2 className="w-2.5 h-2.5" /> Serving
                  </span>
                ) : isVllmModelLoading ? (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold text-amber-700 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded-full">
                    <Loader2 className="w-2.5 h-2.5 animate-spin" /> Loading
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold text-zinc-600 bg-zinc-200 border border-zinc-300 px-2 py-0.5 rounded-full">
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
                  className="inline-flex items-center space-x-1 px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-rose-600 hover:bg-rose-700 text-white transition-colors shadow-xs cursor-pointer"
                >
                  <Square className="w-3 h-3 fill-white" />
                  <span>Stop</span>
                </button>
              ) : isVllmModelOffline ? (
                <button
                  type="button"
                  onClick={handleStartVllmModel}
                  disabled={isStartingVllm}
                  className="inline-flex items-center space-x-1.5 px-3.5 py-1.5 rounded-lg text-xs font-bold bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white transition-colors shadow-xs cursor-pointer"
                >
                  {isStartingVllm ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5 fill-white" />}
                  <span>Start Model</span>
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!inputContent.trim() || isVllmModelLoading}
                  className="inline-flex items-center space-x-1 px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 disabled:hover:bg-indigo-600 text-white transition-colors shadow-xs cursor-pointer"
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
