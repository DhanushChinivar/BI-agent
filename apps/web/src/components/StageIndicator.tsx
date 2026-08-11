"use client";

import type { Stage } from "@/lib/useAgentStream";

const STAGES: { key: Stage; label: string }[] = [
  { key: "planning", label: "Planning" },
  { key: "retrieving", label: "Retrieving" },
  { key: "analyzing", label: "Analyzing" },
  { key: "summarizing", label: "Summarizing" },
];

const ORDER = STAGES.map((s) => s.key);

/**
 * Progress through the agent pipeline.
 *
 * The four steps are fixed, but the caption below them comes from the agent.
 * That split is deliberate: a stable step count keeps the indicator from
 * changing shape between questions, while the caption can say what is actually
 * happening — "Analyzing" covers `compute_node` writing and running SQL on
 * aggregation questions, which is a different and much slower thing than the
 * same stage on a lookup.
 */
export function StageIndicator({
  stage,
  message,
}: {
  stage: Stage;
  message?: string | null;
}) {
  if (!stage || stage === "done") return null;
  const current = ORDER.indexOf(stage);
  const label = STAGES[current]?.label ?? "";

  return (
    // Progress is otherwise invisible to a screen reader: the caption is the
    // only part worth announcing, and `polite` keeps it from interrupting.
    <div role="status" aria-live="polite" className="px-4 py-2">
      <span className="sr-only">
        {label}
        {message ? `: ${message}` : ""}
      </span>

      <div aria-hidden className="flex items-center gap-2 text-sm">
        {STAGES.map(({ key, label: stepLabel }, i) => {
          const done = i < current;
          const active = i === current;
          return (
            <div key={key} className="flex items-center gap-2">
              <span
                className={`flex items-center gap-1.5 ${
                  active
                    ? "text-indigo-400 font-medium"
                    : done
                      ? "text-zinc-500"
                      : "text-zinc-600"
                }`}
              >
                {/* A check, not `line-through`. Strikethrough reads as
                    "cancelled" — it is what a todo list does to a deleted
                    item — which made finished steps look like failed ones. */}
                {done && <span className="text-green-600/80 text-xs">✓</span>}
                {active && (
                  <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse" />
                )}
                {stepLabel}
              </span>
              {i < STAGES.length - 1 && <span className="text-zinc-700">→</span>}
            </div>
          );
        })}
      </div>

      {message && (
        <p className="mt-1.5 text-xs text-zinc-500 min-h-[1rem]">{message}</p>
      )}
    </div>
  );
}
