# Architecture Diagram

```mermaid
graph TD
    User["👤 User (Browser)"]

    subgraph Web ["apps/web — Next.js 16"]
        UI["Chat UI\n/"]
        Connect["Connector Onboarding\n/connect"]
        Settings["Settings + Billing\n/settings"]
        BFF["BFF Proxy\n/api/agent/[...path]"]
        Clerk["Clerk Auth\nmiddleware.ts"]
    end

    subgraph Agent ["apps/agent — FastAPI + LangGraph"]
        API["REST + SSE API\n/v1/query  /v1/query/stream"]
        Webhook["Inbound Webhook\n/v1/webhooks/n8n"]
        Workflows["Workflow Trigger\n/v1/workflows/trigger"]
        Metrics["/metrics (Prometheus)"]

        subgraph Graph ["LangGraph Pipeline"]
            Planner["planner_node\nDecomposes question"]
            Retriever["retriever_node\nFetches data"]
            Analyst["analyst_node\nLLM analysis"]
            Summarizer["summarizer_node\nStreams answer"]
            Action["action_node\nTriggers n8n"]
        end

        subgraph Connectors ["Data Connectors"]
            Sheets["Google Sheets"]
            Gmail["Gmail"]
            Notion["Notion"]
            CSV["CSV / PDF Upload"]
            Mock["Mock (dev)"]
        end

        Auth["AuthMiddleware\nClerk JWT verify"]
        Gating["GatingMiddleware\nPlan quota check"]
        RateLimit["SlowAPI\n60 req/min per IP"]
    end

    subgraph Data ["Data Layer"]
        PG[("PostgreSQL\nCredentials + Plans")]
        Redis[("Redis\nConnector cache")]
    end

    subgraph Automation ["apps/n8n — n8n"]
        ScheduledReport["scheduled_report\nCron → query → email"]
        DataAlert["data_change_alert\nWebhook → query → email"]
    end

    Claude["☁️ Anthropic Claude API"]
    Stripe["☁️ Stripe"]
    ClerkSvc["☁️ Clerk"]

    User -->|HTTPS| UI
    UI --> BFF
    Connect --> BFF
    Settings --> BFF
    Clerk --> BFF
    BFF -->|Bearer JWT| API

    API --> Auth --> Gating --> RateLimit --> Graph
    Webhook --> Graph
    Workflows -->|REST| Automation

    Planner -->|LLM call| Claude
    Analyst -->|LLM call| Claude
    Summarizer -->|LLM stream| Claude
    Planner --> Retriever --> Analyst --> Summarizer
    Summarizer -->|action_required| Action
    Action -->|n8n REST API| Automation

    Retriever --> Connectors
    Connectors --> Redis
    Connectors --> PG

    Gating --> PG
    Auth --> ClerkSvc

    Settings -->|Checkout/Portal| Stripe
    Stripe -->|Webhook| Agent

    ScheduledReport -->|POST /v1/query| API
    DataAlert -->|POST /v1/query| API
```

## Component Summary

| Component | Tech | Purpose |
|---|---|---|
| **web** | Next.js 16, Clerk, Tailwind | Chat UI, connector onboarding, billing settings |
| **agent** | FastAPI, LangGraph, Anthropic SDK | Multi-agent BI pipeline, REST + SSE API |
| **postgres** | PostgreSQL 16 | Encrypted connector credentials, user plans |
| **redis** | Redis 7 | Connector data cache (5-min TTL) |
| **n8n** | n8n (Docker) | Scheduled reports and data-change alerts |
| **Claude** | Anthropic API | LLM for planning, analysis, summarization |
| **Clerk** | Clerk SaaS | JWT-based user auth |
| **Stripe** | Stripe SaaS | Subscription billing, free/pro gating |
