"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

interface ConnectorStatus {
  connector: string;
  connected: boolean;
  last_updated: string | null;
}

const CONNECTOR_META: Record<string, { label: string; description: string; color: string }> = {
  google_sheets: {
    label: "Google Sheets",
    description: "Read spreadsheets and analyse tabular data.",
    color: "text-green-400",
  },
  gmail: {
    label: "Gmail",
    description: "Search and summarise emails. Requires Gmail read access.",
    color: "text-red-400",
  },
  notion: {
    label: "Notion",
    description: "Read pages and databases from your workspace.",
    color: "text-zinc-300",
  },
};

const USER_ID = "dev-user"; // replaced by Clerk userId in Phase 5

export default function ConnectPage() {
  const [statuses, setStatuses] = useState<ConnectorStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const searchParams = useSearchParams();
  const justConnected = searchParams.get("connected");

  useEffect(() => {
    fetch(`/api/agent/v1/connectors/status?user_id=${USER_ID}`)
      .then((r) => r.json())
      .then((data) => setStatuses(data.connectors ?? []))
      .finally(() => setLoading(false));
  }, [justConnected]);

  const disconnect = async (name: string) => {
    await fetch(`/api/agent/v1/connectors/${name}?user_id=${USER_ID}`, { method: "DELETE" });
    setStatuses((prev) =>
      prev.map((s) => (s.connector === name ? { ...s, connected: false, last_updated: null } : s))
    );
  };

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="max-w-2xl mx-auto px-4 py-12">
        <a href="/" className="text-sm text-zinc-500 hover:text-zinc-300 mb-8 inline-block">
          ← Back to chat
        </a>
        <h1 className="text-2xl font-semibold mb-2">Connect data sources</h1>
        <p className="text-sm text-zinc-400 mb-8">
          Link your accounts so the agent can answer questions about your data.
        </p>

        {justConnected && (
          <div className="mb-6 px-4 py-3 rounded-xl bg-green-900/30 border border-green-700 text-green-300 text-sm">
            Successfully connected {CONNECTOR_META[justConnected]?.label ?? justConnected}.
          </div>
        )}

        {loading ? (
          <p className="text-zinc-500 text-sm">Loading…</p>
        ) : (
          <div className="space-y-4">
            {statuses.map((s) => {
              const meta = CONNECTOR_META[s.connector];
              if (!meta) return null;
              return (
                <div
                  key={s.connector}
                  className="flex items-center justify-between p-5 rounded-2xl bg-zinc-900 border border-zinc-800"
                >
                  <div>
                    <p className={`font-medium text-sm ${meta.color}`}>{meta.label}</p>
                    <p className="text-xs text-zinc-500 mt-0.5">{meta.description}</p>
                    {s.connected && s.last_updated && (
                      <p className="text-xs text-zinc-600 mt-1">
                        Last connected {new Date(s.last_updated).toLocaleDateString()}
                      </p>
                    )}
                  </div>
                  <div className="shrink-0 ml-4">
                    {s.connected ? (
                      <button
                        onClick={() => disconnect(s.connector)}
                        className="text-xs text-zinc-400 hover:text-red-400 border border-zinc-700 hover:border-red-700 px-3 py-1.5 rounded-lg transition-colors"
                      >
                        Disconnect
                      </button>
                    ) : (
                      <a
                        href={`/api/agent/v1/oauth/${s.connector.replace("_", "-")}/start?user_id=${USER_ID}`}
                        className="text-xs bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded-lg transition-colors"
                      >
                        Connect
                      </a>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
