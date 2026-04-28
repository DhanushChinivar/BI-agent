"use client";

import { useCallback, useRef, useState } from "react";

export type Stage = "planning" | "retrieving" | "analyzing" | "summarizing" | "done" | null;

export interface Message {
  role: "user" | "assistant";
  content: string;
  conversationId?: string;
}

export function useAgentStream() {
  const [stage, setStage] = useState<Stage>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(async (question: string, conversationId?: string) => {
    // Cancel any in-flight stream
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    // Optimistic user message
    setMessages((prev) => [...prev, { role: "user", content: question }]);
    setStreaming(true);
    setStage("planning");

    // Placeholder assistant message we'll fill in as chunks arrive
    setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

    try {
      const res = await fetch("/api/agent/v1/query/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question, conversation_id: conversationId }),
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

            if (payload.stage) {
              setStage(payload.stage as Stage);
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
      setStreaming(false);
    }
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    setStage(null);
    setStreaming(false);
  }, []);

  return { messages, stage, streaming, send, reset };
}
