"use client";

import type { Message } from "@/lib/useAgentStream";

export function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";

  return (
    <div className={`flex flex-col ${isUser ? "items-end" : "items-start"} mb-4`}>
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap ${
          isUser
            ? "bg-indigo-600 text-white rounded-br-sm"
            : message.failed
              // A partial answer must not look like a finished one — the model
              // stops mid-sentence and the user has no way to tell.
              ? "bg-zinc-800 text-zinc-100 rounded-bl-sm border border-red-800/60"
              : "bg-zinc-800 text-zinc-100 rounded-bl-sm"
        }`}
      >
        {message.content || (
          <span className="inline-flex gap-1 items-center text-zinc-400">
            <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce [animation-delay:0ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce [animation-delay:150ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 animate-bounce [animation-delay:300ms]" />
          </span>
        )}
      </div>

      {!isUser && message.failed && (
        <div className="max-w-[80%] mt-1.5 flex items-start gap-1.5 px-3 py-1.5 rounded-lg bg-red-950/50 border border-red-800/50 text-xs text-red-300">
          <span className="shrink-0 mt-px">✕</span>
          <span>This answer is incomplete — the request failed partway through. Your daily quota was not charged.</span>
        </div>
      )}

      {!isUser && message.scheduled && (
        <div className="max-w-[80%] mt-1.5 flex items-start gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-950/50 border border-indigo-700/50 text-xs text-indigo-300">
          <span className="shrink-0 mt-px">🗓</span>
          <span>
            Scheduled — <strong>{message.scheduled.workflow.replace(/_/g, " ")}</strong>
            {message.scheduled.cron ? ` (${message.scheduled.cron})` : ""}
            {/* The stored next run, echoed back from the database — the old
                confirmation quoted a cron the backend never actually saved. */}
            {message.scheduled.nextRunAt
              ? ` · next ${new Date(message.scheduled.nextRunAt).toLocaleString()}`
              : ""}
          </span>
        </div>
      )}

      {!isUser && message.warnings && message.warnings.length > 0 && (
        <div className="max-w-[80%] mt-1.5 space-y-1">
          {message.warnings.map((w, i) => (
            <div
              key={i}
              className="flex items-start gap-1.5 px-3 py-1.5 rounded-lg bg-amber-950/50 border border-amber-800/50 text-xs text-amber-300"
            >
              <span className="shrink-0 mt-px">⚠</span>
              <span>Could not reach <strong>{w.split(":")[0]}</strong> — {w.split(": ").slice(1).join(": ") || "connection failed"}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
