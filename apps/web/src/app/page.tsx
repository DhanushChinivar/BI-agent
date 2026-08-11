import Link from "next/link";

import { AuthCta } from "@/components/AuthCta";

const FEATURES = [
  {
    icon: "📊",
    title: "Google Sheets Analysis",
    desc: "Connect any spreadsheet and ask questions about your data. Aggregations, trends, comparisons - all in plain English.",
  },
  {
    icon: "📧",
    title: "Gmail Intelligence",
    desc: "Summarise threads, find patterns across emails, and extract key information from your inbox automatically.",
  },
  {
    icon: "📝",
    title: "Notion Workspace",
    desc: "Query your Notion pages, docs, and databases. Great for small teams that run their business in Notion.",
  },
  {
    icon: "⚡",
    title: "Streamed AI Answers",
    desc: "Multi-agent pipeline - planner, retriever, analyst, summarizer - delivers answers in real time as they generate.",
  },
];

const STEPS = [
  {
    number: "01",
    title: "Connect your data",
    desc: "Link Google Sheets, Gmail, or Notion via OAuth to give the agent access to your data.",
  },
  {
    number: "02",
    title: "Ask in plain English",
    desc: "Type any business question - no SQL, no formulas, no code required.",
  },
  {
    number: "03",
    title: "Get instant insights",
    desc: "The AI pipeline fetches, analyses, and streams back a clear, structured answer.",
  },
];

const REVIEWS = [
  {
    name: "Sarah M.",
    role: "Head of Sales",
    text: "I used to spend 2 hours every Monday pulling together our weekly sales report. Now I just ask BI Agent and get it in seconds.",
    stars: 5,
  },
  {
    name: "James T.",
    role: "Startup Founder",
    text: "Connected our Google Sheets and started asking revenue questions immediately. No data team needed.",
    stars: 5,
  },
  {
    name: "Priya K.",
    role: "Operations Manager",
    text: "The Gmail summarisation feature alone is worth it. I can finally keep up with customer feedback threads.",
    stars: 5,
  },
];

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      {/* Nav */}
      <nav className="border-b border-zinc-800 px-6 py-4 flex items-center justify-between max-w-6xl mx-auto">
        <span className="font-semibold text-indigo-400 text-lg tracking-wide">BI Agent</span>
        <div className="flex items-center gap-4">
          <AuthCta
            out={
              <>
                <Link href="/sign-in" className="text-sm text-zinc-400 hover:text-zinc-100 transition-colors">
                  Sign in
                </Link>
                <Link
                  href="/sign-up"
                  className="text-sm bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg transition-colors"
                >
                  Get started free
                </Link>
              </>
            }
            in={
              <Link
                href="/chat"
                className="text-sm bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg transition-colors"
              >
                Go to app
              </Link>
            }
          />
        </div>
      </nav>

      {/* Hero */}
      <section className="max-w-4xl mx-auto px-6 pt-24 pb-20 text-center">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-indigo-950 border border-indigo-800 text-indigo-400 text-xs mb-6">
          <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse" />
          Powered by Claude AI
        </div>
        <h1 className="text-5xl font-bold leading-tight text-zinc-50 mb-6">
          Ask anything about<br />
          <span className="text-indigo-400">your business data</span>
        </h1>
        <p className="text-lg text-zinc-400 max-w-xl mx-auto mb-10">
          Connect Google Sheets, Gmail, or Notion and get AI-powered answers in seconds -
          no SQL, no dashboards, no data team required.
        </p>
        <div className="flex items-center justify-center gap-4">
          <AuthCta
            out={
              <>
                <Link
                  href="/sign-up"
                  className="bg-indigo-600 hover:bg-indigo-500 text-white px-6 py-3 rounded-xl font-medium transition-colors"
                >
                  Start for free
                </Link>
                <Link
                  href="/sign-in"
                  className="border border-zinc-700 hover:border-zinc-500 text-zinc-300 px-6 py-3 rounded-xl font-medium transition-colors"
                >
                  Sign in
                </Link>
              </>
            }
            in={
              <>
                <Link
                  href="/chat"
                  className="bg-indigo-600 hover:bg-indigo-500 text-white px-6 py-3 rounded-xl font-medium transition-colors"
                >
                  Open BI Agent
                </Link>
                <Link
                  href="/connect"
                  className="border border-zinc-700 hover:border-zinc-500 text-zinc-300 px-6 py-3 rounded-xl font-medium transition-colors"
                >
                  Manage data sources
                </Link>
              </>
            }
          />
        </div>

        {/* Hero visual */}
        <div className="mt-16 rounded-2xl bg-zinc-900 border border-zinc-800 p-6 text-left max-w-2xl mx-auto shadow-2xl">
          <div className="flex items-center gap-2 mb-4">
            <div className="w-3 h-3 rounded-full bg-red-500/60" />
            <div className="w-3 h-3 rounded-full bg-yellow-500/60" />
            <div className="w-3 h-3 rounded-full bg-green-500/60" />
          </div>
          <div className="space-y-3 text-sm">
            <div className="flex gap-3">
              <span className="text-zinc-500 shrink-0">You</span>
              <span className="text-zinc-300">What were total sales by month in Q4?</span>
            </div>
            <div className="flex gap-3">
              <span className="text-indigo-400 shrink-0">AI</span>
              <span className="text-zinc-300">
                Based on your Q4 Sales 2024 sheet:<br />
                • October - <span className="text-green-400">$120,000</span> (340 units)<br />
                • November - <span className="text-green-400">$145,000</span> (410 units)<br />
                • December - <span className="text-green-400">$198,000</span> (560 units)<br />
                <span className="text-zinc-400">Total: $463,000 - December was your strongest month, up 37% from October.</span>
              </span>
            </div>
          </div>
        </div>
      </section>

      {/* How it works */}
      <section className="max-w-4xl mx-auto px-6 py-20 border-t border-zinc-800">
        <h2 className="text-2xl font-semibold text-center mb-12">How it works</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
          {STEPS.map((step) => (
            <div key={step.number} className="text-center">
              <div className="text-4xl font-bold text-indigo-900 mb-3">{step.number}</div>
              <h3 className="font-semibold text-zinc-100 mb-2">{step.title}</h3>
              <p className="text-sm text-zinc-400">{step.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Features */}
      <section className="max-w-4xl mx-auto px-6 py-20 border-t border-zinc-800">
        <h2 className="text-2xl font-semibold text-center mb-4">Everything you need</h2>
        <p className="text-center text-zinc-400 text-sm mb-12">
          One agent that connects to where your data already lives.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
          {FEATURES.map((f) => (
            <div
              key={f.title}
              className="p-6 rounded-2xl bg-zinc-900 border border-zinc-800 hover:border-indigo-800 transition-colors"
            >
              <div className="text-3xl mb-3">{f.icon}</div>
              <h3 className="font-semibold text-zinc-100 mb-2">{f.title}</h3>
              <p className="text-sm text-zinc-400">{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Pricing */}
      <section className="max-w-4xl mx-auto px-6 py-20 border-t border-zinc-800">
        <h2 className="text-2xl font-semibold text-center mb-4">Simple pricing</h2>
        <p className="text-center text-zinc-400 text-sm mb-12">Start free. Upgrade when you need more.</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 max-w-2xl mx-auto">
          {/* Free */}
          <div className="p-6 rounded-2xl bg-zinc-900 border border-zinc-800">
            <h3 className="font-semibold text-zinc-100 mb-1">Free</h3>
            <div className="text-3xl font-bold text-zinc-100 mb-1">$0</div>
            <p className="text-xs text-zinc-500 mb-6">No credit card required</p>
            <ul className="space-y-2 text-sm text-zinc-400 mb-8">
              <li>✓ 3 queries per day</li>
              <li>✓ Google Sheets connector</li>
              <li>✓ Gmail connector</li>
              <li>✓ Notion connector</li>
            </ul>
            <AuthCta
              out={
                <Link
                  href="/sign-up"
                  className="block text-center border border-zinc-700 hover:border-zinc-500 text-zinc-300 px-4 py-2 rounded-lg text-sm transition-colors"
                >
                  Get started
                </Link>
              }
              in={
                <Link
                  href="/chat"
                  className="block text-center border border-zinc-700 hover:border-zinc-500 text-zinc-300 px-4 py-2 rounded-lg text-sm transition-colors"
                >
                  Go to app
                </Link>
              }
            />
          </div>
          {/* Pro */}
          <div className="p-6 rounded-2xl bg-indigo-950 border border-indigo-700 relative">
            <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-0.5 bg-indigo-600 rounded-full text-xs text-white font-medium">
              Most popular
            </div>
            <h3 className="font-semibold text-zinc-100 mb-1">Pro</h3>
            <div className="text-3xl font-bold text-zinc-100 mb-1">A$29.90<span className="text-base font-normal text-zinc-400">/mo</span></div>
            <p className="text-xs text-zinc-500 mb-6">Cancel anytime</p>
            <ul className="space-y-2 text-sm text-zinc-300 mb-8">
              <li>✓ Unlimited queries</li>
              <li>✓ Google Sheets connector</li>
              <li>✓ Gmail connector</li>
              <li>✓ Notion connector</li>
              <li>✓ Priority support</li>
            </ul>
            <AuthCta
              out={
                <Link
                  href="/sign-up"
                  className="block text-center bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg text-sm transition-colors"
                >
                  Start Pro
                </Link>
              }
              in={
                // Already signed in: checkout lives on /settings, and sending
                // them to /sign-up would bounce them straight back.
                <Link
                  href="/settings"
                  className="block text-center bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg text-sm transition-colors"
                >
                  Upgrade to Pro
                </Link>
              }
            />
          </div>
        </div>
      </section>

      {/* Reviews */}
      <section className="max-w-4xl mx-auto px-6 py-20 border-t border-zinc-800">
        <h2 className="text-2xl font-semibold text-center mb-12">What people are saying</h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
          {REVIEWS.map((r) => (
            <div key={r.name} className="p-5 rounded-2xl bg-zinc-900 border border-zinc-800">
              <div className="flex gap-0.5 mb-3">
                {Array.from({ length: r.stars }).map((_, i) => (
                  <span key={i} className="text-yellow-400 text-sm">★</span>
                ))}
              </div>
              <p className="text-sm text-zinc-300 mb-4">"{r.text}"</p>
              <div>
                <p className="text-sm font-medium text-zinc-100">{r.name}</p>
                <p className="text-xs text-zinc-500">{r.role}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-zinc-800 px-6 py-10 mt-8">
        <div className="max-w-4xl mx-auto flex items-center justify-center">
          <p className="text-xs text-zinc-600">© 2026 BI Agent. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
