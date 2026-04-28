"use client";

import type { Stage } from "@/lib/useAgentStream";

const STAGES: { key: Stage; label: string }[] = [
  { key: "planning", label: "Planning" },
  { key: "retrieving", label: "Retrieving" },
  { key: "analyzing", label: "Analyzing" },
  { key: "summarizing", label: "Summarizing" },
];

const ORDER = STAGES.map((s) => s.key);

export function StageIndicator({ stage }: { stage: Stage }) {
  if (!stage || stage === "done") return null;
  const current = ORDER.indexOf(stage);

  return (
    <div className="flex items-center gap-2 px-4 py-2 text-sm text-zinc-400">
      {STAGES.map(({ key, label }, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <div key={key} className="flex items-center gap-2">
            <span
              className={
                active
                  ? "text-indigo-400 font-medium animate-pulse"
                  : done
                  ? "text-zinc-500 line-through"
                  : "text-zinc-600"
              }
            >
              {label}
            </span>
            {i < STAGES.length - 1 && <span className="text-zinc-700">→</span>}
          </div>
        );
      })}
    </div>
  );
}
