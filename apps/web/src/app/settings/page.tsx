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
  const [error, setError] = useState<string | null>(null);
  const searchParams = useSearchParams();
  const upgraded = searchParams.get("upgraded");

  useEffect(() => {
    // `.catch(() => null)` left planInfo null, and null renders identically to a
    // real Free plan — so a Pro subscriber whose plan lookup failed was shown
    // "Free" and invited to pay again.
    fetch(`/api/agent/v1/plan/status`)
      .then((r) => {
        if (!r.ok) throw new Error(`Could not load your plan (${r.status}).`);
        return r.json();
      })
      .then((data) => {
        setPlanInfo(data);
        setError(null);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Could not load your plan.");
      });
  }, []);

  /** Send the browser to a Stripe-hosted page, or explain why we cannot. */
  const goToStripe = async (label: string, request: () => Promise<Response>) => {
    setLoading(true);
    setError(null);
    try {
      const res = await request();
      const body = await res.json().catch(() => ({}));
      // Neither call checked `res.ok`, so a 500 — which is exactly what a
      // missing STRIPE_SECRET_KEY now produces by design — yielded an
      // undefined `url` and navigated the browser to "/undefined".
      if (!res.ok || !body?.url) {
        throw new Error(body?.error ?? `${label} is unavailable right now.`);
      }
      window.location.href = body.url;
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : `${label} is unavailable right now.`);
      // Only on failure: on success the browser is navigating away, and
      // re-enabling the button invites a second checkout session.
      setLoading(false);
    }
  };

  const startCheckout = () =>
    goToStripe("Checkout", () => fetch("/api/billing/checkout", { method: "POST" }));

  const openPortal = () => {
    if (!planInfo?.stripe_customer_id) return;
    return goToStripe("The billing portal", () =>
      fetch("/api/billing/portal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ customerId: planInfo.stripe_customer_id }),
      }),
    );
  };

  const isPro = planInfo?.plan === "pro";

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="max-w-xl mx-auto px-4 py-12">
        <a href="/chat" className="text-sm text-zinc-500 hover:text-zinc-300 mb-8 inline-block">
          ← Back to chat
        </a>
        <h1 className="text-2xl font-semibold mb-8">Settings</h1>

        {error && (
          <div className="mb-6 px-4 py-3 rounded-xl bg-red-950/40 border border-red-800 text-red-300 text-sm">
            {error}
          </div>
        )}

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
                {planInfo === null ? (
                  <span className="text-zinc-500">…</span>
                ) : isPro ? (
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
              {planInfo === null ? null : isPro ? (
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

          {planInfo !== null && !isPro && (
            <div className="mt-4 pt-4 border-t border-zinc-800">
              <p className="text-xs text-zinc-500 mb-2">Pro includes:</p>
              <ul className="text-xs text-zinc-400 space-y-1">
                <li>✓ Unlimited queries per day</li>
                <li>✓ Google Sheets and Gmail connectors</li>
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
