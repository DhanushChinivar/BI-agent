"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

interface PlanInfo {
  plan: string;
  queries_today: number;
  stripe_customer_id: string | null;
}

const FREE_LIMIT = 3;

function SettingsPageInner() {
  const [planInfo, setPlanInfo] = useState<PlanInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const searchParams = useSearchParams();
  const upgraded = searchParams.get("upgraded");

  useEffect(() => {
    fetch(`/api/agent/v1/plan/status`)
      .then((r) => r.json())
      .then(setPlanInfo)
      .catch(() => null);
  }, []);

  const startCheckout = async () => {
    setLoading(true);
    const res = await fetch("/api/billing/checkout", { method: "POST" });
    const { url } = await res.json();
    window.location.href = url;
  };

  const openPortal = async () => {
    if (!planInfo?.stripe_customer_id) return;
    setLoading(true);
    const res = await fetch("/api/billing/portal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ customerId: planInfo.stripe_customer_id }),
    });
    const { url } = await res.json();
    window.location.href = url;
  };

  const isPro = planInfo?.plan === "pro";

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="max-w-xl mx-auto px-4 py-12">
        <a href="/" className="text-sm text-zinc-500 hover:text-zinc-300 mb-8 inline-block">
          ← Back to chat
        </a>
        <h1 className="text-2xl font-semibold mb-8">Settings</h1>

        {upgraded && (
          <div className="mb-6 px-4 py-3 rounded-xl bg-green-900/30 border border-green-700 text-green-300 text-sm">
            You're now on Pro. Enjoy unlimited queries.
          </div>
        )}

        <section className="p-5 rounded-2xl bg-zinc-900 border border-zinc-800">
          <h2 className="text-sm font-medium text-zinc-400 mb-4">Plan</h2>
          <div className="flex items-center justify-between mb-4">
            <div>
              <p className="text-sm font-semibold">
                {isPro ? (
                  <span className="text-indigo-400">Pro</span>
                ) : (
                  <span className="text-zinc-300">Free</span>
                )}
              </p>
              {!isPro && planInfo && (
                <p className="text-xs text-zinc-500 mt-0.5">
                  {planInfo.queries_today} / {FREE_LIMIT} queries used today
                </p>
              )}
              {isPro && <p className="text-xs text-zinc-500 mt-0.5">Unlimited queries</p>}
            </div>
            <div>
              {isPro ? (
                <button
                  onClick={openPortal}
                  disabled={loading}
                  className="text-xs border border-zinc-700 hover:border-zinc-500 px-3 py-1.5 rounded-lg text-zinc-300 transition-colors disabled:opacity-40"
                >
                  Manage subscription
                </button>
              ) : (
                <button
                  onClick={startCheckout}
                  disabled={loading}
                  className="text-xs bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded-lg transition-colors disabled:opacity-40"
                >
                  Upgrade to Pro
                </button>
              )}
            </div>
          </div>

          {!isPro && (
            <div className="mt-4 pt-4 border-t border-zinc-800">
              <p className="text-xs text-zinc-500 mb-2">Pro includes:</p>
              <ul className="text-xs text-zinc-400 space-y-1">
                <li>✓ Unlimited queries per day</li>
                <li>✓ Google Sheets, Gmail, and Notion connectors</li>
                <li>✓ Priority support</li>
              </ul>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-zinc-950" />}>
      <SettingsPageInner />
    </Suspense>
  );
}
