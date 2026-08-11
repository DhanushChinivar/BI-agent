"use client";

import { useCallback, useRef, useState } from "react";

export type Stage = "planning" | "retrieving" | "analyzing" | "summarizing" | "done" | null;

export interface Message {
  role: "user" | "assistant";
  content: string;
  conversationId?: string;
  warnings?: string[];
  scheduled?: { workflow: string; cron: string; nextRunAt?: string };
  /** The pipeline errored before finishing this answer. */
  failed?: boolean;
}

export function useAgentStream() {
  const [stage, setStage] = useState<Stage>(null);
  // The agent sends a human-readable message with every `stage` event. It used
  // to be dropped here, leaving StageIndicator to invent its own labels — two
  // sources of truth for the same thing, and the more specific one discarded.
  // It matters most where one stage covers several steps: `compute_node` runs
  // inside "analyzing", so on an aggregation question that stage means
  // "writing SQL and running it", not "analyzing".
  const [stageMessage, setStageMessage] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [activeConversationId, setActiveConversationId] = useState<string | undefined>(undefined);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(async (question: string, conversationId?: string) => {
    // Cancel any in-flight stream
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const convId = conversationId ?? activeConversationId;

    // Optimistic user message
    setMessages((prev) => [...prev, { role: "user", content: question }]);
    setStreaming(true);
    setStage("planning");
    setStageMessage(null);

    // Placeholder assistant message we'll fill in as chunks arrive
    setMessages((prev) => [...prev, { role: "assistant", content: "", warnings: [] }]);

    try {
      const res = await fetch("/api/agent/v1/query/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question, conversation_id: convId }),
        signal: controller.signal,
      });

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let finalConversationId: string | undefined;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        for (const line of lines) {
          if (line.startsWith("event: ")) continue;
          if (!line.startsWith("data: ")) continue;

          const raw = line.slice(6).trim();
          if (!raw) continue;

          try {
            const payload = JSON.parse(raw);

            if (payload.error) {
              // The pipeline failed mid-stream. Checked first: without it the
              // socket just closed and the UI sat on the last stage forever.
              setMessages((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last.role === "assistant") {
                  next[next.length - 1] = { ...last, content: last.content || payload.error, failed: true };
                }
                return next;
              });
            } else if (payload.stage) {
              setStage(payload.stage as Stage);
              setStageMessage(payload.message ?? null);
            } else if (payload.status === "scheduled") {
              setMessages((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last.role === "assistant") {
                  next[next.length - 1] = {
                    ...last,
                    scheduled: {
                      workflow: payload.workflow,
                      cron: payload.cron,
                      nextRunAt: payload.next_run_at,
                    },
                  };
                }
                return next;
              });
            } else if (payload.connector && payload.message) {
              // Connector warning — attach to the in-progress assistant message
              const warn = `${payload.connector}: ${payload.message}`;
              setMessages((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last.role === "assistant") {
                  next[next.length - 1] = {
                    ...last,
                    warnings: [...(last.warnings ?? []), warn],
                  };
                }
                return next;
              });
            } else if (payload.content !== undefined) {
              setMessages((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last.role === "assistant") {
                  next[next.length - 1] = { ...last, content: last.content + payload.content };
                }
                return next;
              });
            } else if (payload.conversation_id) {
              finalConversationId = payload.conversation_id;
              setActiveConversationId(finalConversationId);
              setMessages((prev) => {
                const next = [...prev];
                const last = next[next.length - 1];
                if (last.role === "assistant") {
                  next[next.length - 1] = { ...last, conversationId: finalConversationId };
                }
                return next;
              });
            }
          } catch {
            // malformed JSON line — skip
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== "AbortError") {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last.role === "assistant" && last.content === "") {
            next[next.length - 1] = { ...last, content: "Something went wrong. Please try again." };
          }
          return next;
        });
      }
    } finally {
      setStage("done");
      setStageMessage(null);
      setStreaming(false);
    }
  }, [activeConversationId]);

  const loadConversation = useCallback((conversationId: string, history: Message[]) => {
    abortRef.current?.abort();
    setActiveConversationId(conversationId);
    setMessages(history);
    setStage(null);
    setStageMessage(null);
    setStreaming(false);
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    setStage(null);
    setStageMessage(null);
    setStreaming(false);
    setActiveConversationId(undefined);
  }, []);

  return {
    messages,
    stage,
    stageMessage,
    streaming,
    send,
    reset,
    loadConversation,
    activeConversationId,
  };
}
