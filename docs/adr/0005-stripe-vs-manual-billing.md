# ADR 0005 — Billing: Stripe vs Manual

**Date:** 2026-04-29  
**Status:** Accepted

## Context

The application gates certain features behind a "Pro" plan. A billing system is needed to handle subscriptions and enforce limits.

## Options

| Option | Pros | Cons |
|---|---|---|
| **Stripe** | Managed subscriptions, webhooks, hosted checkout/portal, PCI compliant | SaaS dependency |
| **Manual (invite codes, honour system)** | Zero cost, zero complexity | Not scalable, no revenue |
| **Paddle / Lemon Squeezy** | Similar to Stripe | Less ecosystem support |

## Decision

**Stripe** — chosen because it provides a complete billing stack (checkout, customer portal, webhook lifecycle) with minimal code. The `GatingMiddleware` enforces limits locally (3 queries/day free, unlimited pro), and Stripe webhooks update the `user_plan` table on subscription changes.

## Consequences

- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRO_PRICE_ID` required in env
- Free users are limited to 3 queries/day enforced by `GatingMiddleware` (returns 402)
- Pro users get unlimited queries after successful Stripe checkout
- `POST /api/billing/checkout` creates a Stripe Checkout session
- `POST /api/billing/portal` creates a Stripe Customer Portal session for subscription management
