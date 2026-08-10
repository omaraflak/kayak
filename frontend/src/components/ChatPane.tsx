import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { Conversation, Message } from '../types';
import { api } from '../api/client';
import { useSSE } from '../hooks/useSSE';
import { ToolCallCard } from './ToolCallCard';
import { ToolCallsAccordion } from './ToolCallsAccordion';
import { CodeBlock } from './CodeBlock';
import { 
  Send, 
  Bot, 
  Loader2, 
  Sparkles,
  Square
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
          toolCalls: [],
        };
      } else {
        if (msg.content) {
          currentAssistantTurn.content = currentAssistantTurn.content 
            ? `${currentAssistantTurn.content}\n\n${msg.content}`
            : msg.content;
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
  const [activeToolExecutions, setActiveToolExecutions] = useState<
    Record<string, { name: string; args: string; output?: string; isError?: boolean }>
  >({});

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
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
      return;
    }
    try {
      const data = await api.getConversation(conversationId);
      setMessages(data.messages);

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
    setActiveToolExecutions({});
    setIsSending(false);
  }, [conversationId]);

  useEffect(() => {
    scrollToBottom();
  }, [messages, streamingTokenText, activeToolExecutions]);

  useSSE(conversationId, {
    onToken: (token) => {
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
      setActiveToolExecutions({});
      loadConversationData();
      onRefreshConversations?.();
    },
    onCancelled: () => {
      setIsSending(false);
      setStreamingTokenText('');
      setActiveToolExecutions({});
      loadConversationData();
      onRefreshConversations?.();
    },
    onUserMessage: (msg) => {
      setMessages((prev) => [...prev, msg]);
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
    if (!inputContent.trim() || isSending) return;

    const text = inputContent.trim();
    setInputContent('');
    setIsSending(true);

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
      return <h1 className="text-lg font-bold text-zinc-950 mt-6 mb-3 tracking-tight" {...props}>{children}</h1>;
    },
    h2({ children, ...props }: any) {
      return <h2 className="text-base font-bold text-zinc-900 mt-5 mb-2 tracking-tight" {...props}>{children}</h2>;
    },
    h3({ children, ...props }: any) {
      return <h3 className="text-sm font-bold text-zinc-900 mt-4 mb-1.5" {...props}>{children}</h3>;
    },
    p({ children, ...props }: any) {
      return <p className="mb-3.5 text-zinc-800 text-[14px] leading-7 font-normal" {...props}>{children}</p>;
    },
    ul({ children, ...props }: any) {
      return <ul className="list-disc pl-5 my-3 space-y-1.5 text-zinc-800 text-[14px] leading-7" {...props}>{children}</ul>;
    },
    ol({ children, ...props }: any) {
      return <ol className="list-decimal pl-5 my-3 space-y-1.5 text-zinc-800 text-[14px] leading-7" {...props}>{children}</ol>;
    },
    li({ children, ...props }: any) {
      return <li className="leading-relaxed" {...props}>{children}</li>;
    },
    blockquote({ children, ...props }: any) {
      return (
        <blockquote className="border-l-3 border-indigo-500 pl-4 py-1.5 my-3.5 text-zinc-700 bg-indigo-50/40 rounded-r-xl italic text-[13.5px] leading-relaxed" {...props}>
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
        <code className="px-1.5 py-0.5 rounded-md bg-zinc-200/70 border border-zinc-300/60 text-zinc-900 font-mono text-[12px]" {...props}>
          {children}
        </code>
      );
    }
  };

  // Markdown renderer for User message bubble (pure white text on dark background)
  const userMarkdownComponents = {
    h1({ children, ...props }: any) {
      return <h1 className="text-base font-bold text-white my-2" {...props}>{children}</h1>;
    },
    h2({ children, ...props }: any) {
      return <h2 className="text-sm font-bold text-white my-1.5" {...props}>{children}</h2>;
    },
    h3({ children, ...props }: any) {
      return <h3 className="text-xs font-bold text-white my-1" {...props}>{children}</h3>;
    },
    p({ children, ...props }: any) {
      return <p className="text-white text-[14px] leading-relaxed mb-2 last:mb-0 font-normal" {...props}>{children}</p>;
    },
    ul({ children, ...props }: any) {
      return <ul className="list-disc pl-5 my-2 text-white text-[14px] space-y-1" {...props}>{children}</ul>;
    },
    ol({ children, ...props }: any) {
      return <ol className="list-decimal pl-5 my-2 text-white text-[14px] space-y-1" {...props}>{children}</ol>;
    },
    li({ children, ...props }: any) {
      return <li className="text-white leading-relaxed" {...props}>{children}</li>;
    },
    strong({ children, ...props }: any) {
      return <strong className="font-bold text-white" {...props}>{children}</strong>;
    },
    code({ node, inline, className, children, ...props }: any) {
      const codeStr = String(children).replace(/\n$/, '');
      return (
        <code className="px-1.5 py-0.5 rounded bg-zinc-800 border border-zinc-700 text-zinc-100 font-mono text-[12px]" {...props}>
          {codeStr}
        </code>
      );
    }
  };

  const groupedTurns = groupMessagesIntoTurns(messages);

  return (
    <div className="flex-1 flex flex-col h-full bg-zinc-50 overflow-hidden">
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

      {/* Messages Scroll Area */}
      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
        {groupedTurns.length === 0 && !streamingTokenText && Object.keys(activeToolExecutions).length === 0 && (
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
                <div className="max-w-2xl bg-zinc-900 text-white rounded-2xl rounded-tr-xs px-5 py-3 text-[14px] shadow-xs leading-relaxed">
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
                {/* Direct Response Text on Background without enclosing white card or icons */}
                {turn.content && (
                  <div className="text-zinc-800 text-[14px] leading-7">
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
        {(streamingTokenText || Object.keys(activeToolExecutions).length > 0) && (
          <div className="w-full space-y-2 pt-2 animate-fade-in">
            {/* Streaming token text directly on background */}
            {streamingTokenText && (
              <div className="text-zinc-800 text-[14px] leading-7">
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

        <div ref={messagesEndRef} />
      </div>

      {/* Input Composer */}
      <div className="p-4 border-t border-zinc-200 bg-white shrink-0">
        <form
          onSubmit={handleSend}
          className="relative bg-zinc-50 border border-zinc-300 rounded-xl overflow-hidden focus-within:bg-white focus-within:border-indigo-600 focus-within:ring-1 focus-within:ring-indigo-600 shadow-xs transition-all"
        >
          <textarea
            value={inputContent}
            onChange={(e) => setInputContent(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={2}
            placeholder={placeholder || `Message ${agentName}... (Enter to send, Shift+Enter for new line)`}
            className="w-full bg-transparent px-3.5 py-2.5 text-xs text-zinc-900 placeholder-zinc-400 focus:outline-none resize-none leading-relaxed"
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
            </div>

            <div className="flex items-center space-x-2">
              {isSending ? (
                <button
                  type="button"
                  onClick={handleCancelGeneration}
                  className="inline-flex items-center space-x-1 px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-rose-600 hover:bg-rose-700 text-white transition-colors shadow-xs"
                >
                  <Square className="w-3 h-3 fill-white" />
                  <span>Stop</span>
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!inputContent.trim()}
                  className="inline-flex items-center space-x-1 px-3.5 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 disabled:hover:bg-indigo-600 text-white transition-colors shadow-xs"
                >
                  <span>Send</span>
                  <Send className="w-3 h-3" />
                </button>
              )}
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};
