"use client";

import { useEffect, useRef } from "react";
import { ChatInput } from "@/components/ChatInput";
import { MessageBubble } from "@/components/MessageBubble";
import { StageIndicator } from "@/components/StageIndicator";
import { useAgentStream } from "@/lib/useAgentStream";

export default function Home() {
  const { messages, stage, streaming, send, reset } = useAgentStream();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, stage]);

  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100">
      {/* Sidebar */}
      <aside className="w-60 shrink-0 border-r border-zinc-800 flex flex-col">
        <div className="px-4 py-5 border-b border-zinc-800">
          <span className="font-semibold text-sm tracking-wide text-indigo-400">BI Agent</span>
        </div>
        <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-1">
          <button
            onClick={reset}
            className="w-full text-left px-3 py-2 rounded-lg text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 transition-colors"
          >
            + New conversation
          </button>
        </nav>
        <div className="px-3 py-4 border-t border-zinc-800 space-y-1">
          <a
            href="/connect"
            className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 transition-colors"
          >
            <span>Connect data sources</span>
          </a>
        </div>
      </aside>

      {/* Main chat area */}
      <main className="flex flex-col flex-1 min-w-0">
        <div className="flex-1 overflow-y-auto px-4 py-6">
          {messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-center">
              <h1 className="text-2xl font-semibold text-zinc-200">Ask anything about your data</h1>
              <p className="text-sm text-zinc-500 max-w-sm">
                Connect Google Sheets, Notion, or Gmail and ask natural-language questions.
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
