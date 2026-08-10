import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { Conversation, Message } from '../types';
import { api } from '../api/client';
import { useSSE } from '../hooks/useSSE';
import { ToolCallCard } from './ToolCallCard';
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

  // Extract python code blocks: ```python ... ``` or ```py ... ``` or unclosed ```python ...
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
      // Try to extract function name
      const fnMatch = /def\s+([a-zA-Z0-9_]+)\s*\(/.exec(block);
      if (fnMatch && fnMatch[1] && !['test_', 'main', 'execute'].includes(fnMatch[1])) {
        result.name = fnMatch[1];
      }
    }
  }

  // Check for explicit tool name: "tool_name: xyz" or "Tool Name: `xyz`"
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

  // Check if text has YAML frontmatter: --- ... ---
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

  // Check if there is a markdown code block containing instructions
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

  // If text itself starts with or contains a top-level markdown heading # Skill
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

      // Scan existing messages for tool calls and text drafts to populate initial editor state
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
          // Regular conversation creation with LLM title computation on first message!
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

  const markdownComponents = {
    code({ node, inline, className, children, ...props }: any) {
      const match = /language-(\w+)/.exec(className || '');
      const codeStr = String(children).replace(/\n$/, '');
      if (!inline && (match || codeStr.includes('\n'))) {
        return (
          <CodeBlock
            language={match ? match[1] : 'text'}
            code={codeStr}
          />
        );
      }
      return (
        <code className="px-1.5 py-0.5 rounded bg-zinc-100 border border-zinc-200 text-zinc-800 font-mono text-[11px]" {...props}>
          {children}
        </code>
      );
    }
  };

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
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && !streamingTokenText && (
          <div className="text-center py-16 space-y-3">
            <div className="w-12 h-12 rounded-2xl bg-indigo-50 border border-indigo-200 flex items-center justify-center text-indigo-600 mx-auto shadow-xs">
              <Sparkles className="w-6 h-6" />
            </div>
            <h3 className="text-sm font-bold text-zinc-900">{headerTitle || `Chat with ${agentName}`}</h3>
            <p className="text-xs text-zinc-500 max-w-sm mx-auto leading-relaxed">
              {headerSubtitle || 'Type your instructions below to begin interactive agent synthesis and refinement.'}
            </p>
          </div>
        )}

        {messages.map((msg, index) => {
          if (msg.role === 'user') {
            return (
              <div key={index} className="flex justify-end">
                <div className="max-w-xl bg-zinc-900 text-white rounded-2xl rounded-tr-xs px-4 py-2.5 text-xs shadow-xs leading-relaxed">
                  <ReactMarkdown 
                    remarkPlugins={[remarkGfm, remarkMath]} 
                    rehypePlugins={[rehypeKatex]}
                    components={markdownComponents}
                  >
                    {msg.content || ''}
                  </ReactMarkdown>
                </div>
              </div>
            );
          }

          if (msg.role === 'assistant') {
            return (
              <div key={index} className="flex space-x-3 text-xs">
                <div className="w-7 h-7 rounded-lg bg-zinc-100 border border-zinc-200 flex items-center justify-center shrink-0 text-zinc-700 text-xs mt-0.5 shadow-xs">
                  <Bot className="w-3.5 h-3.5" />
                </div>
                <div className="flex-1 space-y-2 overflow-hidden min-w-0">
                  {msg.content && (
                    <div className="prose prose-zinc max-w-none text-zinc-800 text-xs leading-relaxed bg-white p-4 rounded-2xl border border-zinc-200 shadow-xs">
                      <ReactMarkdown 
                        remarkPlugins={[remarkGfm, remarkMath]} 
                        rehypePlugins={[rehypeKatex]}
                        components={markdownComponents}
                      >
                        {msg.content}
                      </ReactMarkdown>
                    </div>
                  )}

                  {msg.tool_calls && msg.tool_calls.map((tc) => (
                    <ToolCallCard
                      key={tc.id}
                      name={tc.function?.name || 'tool'}
                      argumentsStr={tc.function?.arguments || '{}'}
                    />
                  ))}
                </div>
              </div>
            );
          }

          if (msg.role === 'tool') {
            return (
              <div key={index} className="pl-10">
                <ToolCallCard
                  name={msg.name || 'tool'}
                  argumentsStr={`Call ID: ${msg.tool_call_id || ''}`}
                  output={msg.content || ''}
                  isError={msg.content?.startsWith('Error:') || msg.content?.startsWith('✗')}
                />
              </div>
            );
          }

          return null;
        })}

        {/* Live Streaming Active Turn */}
        {(streamingTokenText || Object.keys(activeToolExecutions).length > 0) && (
          <div className="flex space-x-3 text-xs animate-fade-in">
            <div className="w-7 h-7 rounded-lg bg-indigo-50 border border-indigo-200 flex items-center justify-center shrink-0 text-indigo-700 text-xs mt-0.5 shadow-xs">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            </div>
            <div className="flex-1 space-y-2 overflow-hidden min-w-0">
              {streamingTokenText && (
                <div className="prose prose-zinc max-w-none text-zinc-800 text-xs leading-relaxed bg-white p-4 rounded-2xl border border-zinc-200 shadow-xs">
                  <ReactMarkdown 
                    remarkPlugins={[remarkGfm, remarkMath]} 
                    rehypePlugins={[rehypeKatex]}
                    components={markdownComponents}
                  >
                    {streamingTokenText}
                  </ReactMarkdown>
                </div>
              )}

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
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input Composer */}
      <div className="p-3 border-t border-zinc-200 bg-white shrink-0">
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
            className="w-full bg-transparent px-3 py-2 text-xs text-zinc-900 placeholder-zinc-400 focus:outline-none resize-none leading-relaxed"
          />

          <div className="flex items-center justify-between px-3 py-1.5 bg-zinc-100/50 border-t border-zinc-200">
            <div className="flex items-center space-x-2 text-[11px] text-zinc-500">
              <Sparkles className="w-3.5 h-3.5 text-indigo-600" />
              <span className="font-medium">{agentName}</span>
              {agentModel && <span className="font-mono text-[10px] text-zinc-400">({agentModel})</span>}
            </div>

            <div className="flex items-center space-x-2">
              {isSending ? (
                <button
                  type="button"
                  onClick={handleCancelGeneration}
                  className="inline-flex items-center space-x-1 px-3 py-1 rounded-lg text-xs font-semibold bg-rose-600 hover:bg-rose-700 text-white transition-colors shadow-xs"
                >
                  <Square className="w-3 h-3 fill-white" />
                  <span>Stop</span>
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!inputContent.trim()}
                  className="inline-flex items-center space-x-1 px-3 py-1 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 disabled:hover:bg-indigo-600 text-white transition-colors shadow-xs"
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
