"use client";

import { UserButton } from "@clerk/nextjs";
import { useEffect, useRef, useState } from "react";
import { ChatInput } from "@/components/ChatInput";
import { MessageBubble } from "@/components/MessageBubble";
import { StageIndicator } from "@/components/StageIndicator";
import { useAgentStream, Message } from "@/lib/useAgentStream";

interface ConversationSummary {
  id: string;
  title: string;
  updated_at: string;
}

export default function Home() {
  const { messages, stage, streaming, send, reset, loadConversation, activeConversationId } = useAgentStream();
  const bottomRef = useRef<HTMLDivElement>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, stage]);

  // Fetch conversation list on mount
  useEffect(() => {
    fetchConversations();
  }, []);

  // Refresh sidebar list whenever a new conversation completes
  useEffect(() => {
    if (activeConversationId) {
      fetchConversations();
    }
  }, [activeConversationId]);

  async function fetchConversations() {
    try {
      const res = await fetch("/api/agent/v1/conversations");
      if (res.ok) {
        setConversations(await res.json());
      }
    } catch {
      // sidebar list is non-critical
    }
  }

  async function openConversation(id: string) {
    if (id === activeConversationId) return;
    setLoadingHistory(true);
    try {
      const res = await fetch(`/api/agent/v1/conversations/${id}/messages`);
      if (res.ok) {
        const raw: { role: string; content: string }[] = await res.json();
        const history: Message[] = raw.map((m) => ({
          role: m.role as "user" | "assistant",
          content: m.content,
          conversationId: id,
        }));
        loadConversation(id, history);
      }
    } finally {
      setLoadingHistory(false);
    }
  }

  async function deleteConversation(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    await fetch(`/api/agent/v1/conversations/${id}`, { method: "DELETE" });
    setConversations((prev) => prev.filter((c) => c.id !== id));
    if (activeConversationId === id) reset();
  }

  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100">
      {/* Sidebar */}
      <aside className="w-60 shrink-0 border-r border-zinc-800 flex flex-col">
        <div className="px-4 py-5 border-b border-zinc-800">
          <span className="font-semibold text-sm tracking-wide text-indigo-400">BI Agent</span>
        </div>

        <div className="px-3 pt-3">
          <button
            onClick={reset}
            className="w-full text-left px-3 py-2 rounded-lg text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 transition-colors"
          >
            + New conversation
          </button>
        </div>

        {/* Conversation history list */}
        <nav className="flex-1 overflow-y-auto px-3 py-2 space-y-0.5">
          {conversations.map((conv) => (
            <div
              key={conv.id}
              onClick={() => openConversation(conv.id)}
              className={`group flex items-center justify-between gap-1 w-full px-3 py-2 rounded-lg text-sm cursor-pointer transition-colors ${
                activeConversationId === conv.id
                  ? "bg-zinc-800 text-zinc-100"
                  : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
              }`}
            >
              <span className="truncate flex-1">{conv.title}</span>
              <button
                onClick={(e) => deleteConversation(conv.id, e)}
                className="opacity-0 group-hover:opacity-100 shrink-0 text-zinc-600 hover:text-red-400 transition-opacity"
                title="Delete"
              >
                ×
              </button>
            </div>
          ))}
        </nav>

        <div className="px-3 py-4 border-t border-zinc-800 space-y-1">
          <a
            href="/connect"
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 transition-colors"
          >
            <span>Connect data sources</span>
          </a>
          <a
            href="/settings"
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 transition-colors"
          >
            <span>Settings & billing</span>
          </a>
          <div className="flex items-center gap-2 px-3 py-2">
            <UserButton />
          </div>
        </div>
      </aside>

      {/* Main chat area */}
      <main className="flex flex-col flex-1 min-w-0">
        <div className="flex-1 overflow-y-auto px-4 py-6">
          {loadingHistory ? (
            <div className="flex items-center justify-center h-full text-zinc-500 text-sm">
              Loading…
            </div>
          ) : messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-center">
              <h1 className="text-2xl font-semibold text-zinc-200">Ask anything about your data</h1>
              <p className="text-sm text-zinc-500 max-w-sm">
                Connect Google Sheets or Gmail and ask natural-language questions.
              </p>
              <div className="grid grid-cols-1 gap-2 mt-4 w-full max-w-sm">
                {[
                  "What were Q4 sales by month?",
                  "Summarise my unread emails from last week",
                  "What's on the product roadmap?",
                ].map((q) => (
                  <button
                    key={q}
                    onClick={() => send(q)}
                    className="text-left px-4 py-3 rounded-xl bg-zinc-900 border border-zinc-800 text-sm text-zinc-300 hover:border-indigo-500 hover:text-white transition-colors"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="max-w-3xl mx-auto">
              {messages.map((msg, i) => (
                <MessageBubble key={i} message={msg} />
              ))}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {streaming && (
          <div className="max-w-3xl mx-auto w-full px-4">
            <StageIndicator stage={stage} />
          </div>
        )}

        <ChatInput onSend={send} disabled={streaming} />
      </main>
    </div>
  );
}
