# syntax=docker/dockerfile:1
# ── deps: install node_modules ────────────────────────────────────────────────
FROM node:20-alpine AS deps
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN npm ci

# ── builder: next build ───────────────────────────────────────────────────────
FROM node:20-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

# NEXT_PUBLIC_* is inlined into the client bundle by `next build`, so it must be
# present *here* — supplying it at runtime via compose `env_file:` is too late,
# and the browser gets `undefined`. That failure is silent and confusing:
# ClerkProvider never initialises, `useUser().isLoaded` stays false forever, and
# every page that waits on it hangs on its loading state with no error anywhere.
#
# `.dockerignore` excludes `**/.env.*`, so `.env.local` is deliberately not
# copied — secrets must not be baked into a layer. A publishable key is public
# by design, which is why it is safe to pass as a build arg.
ARG NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
ARG NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in
ARG NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up
ARG NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY
ENV NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY \
    NEXT_PUBLIC_CLERK_SIGN_IN_URL=$NEXT_PUBLIC_CLERK_SIGN_IN_URL \
    NEXT_PUBLIC_CLERK_SIGN_UP_URL=$NEXT_PUBLIC_CLERK_SIGN_UP_URL \
    NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=$NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY

COPY --from=deps /app/node_modules ./node_modules
COPY apps/web ./

# Fail the build rather than shipping an image whose UI hangs at runtime.
RUN test -n "$NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY" \
      || (echo "ERROR: NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY build arg is empty." \
               "Run via 'make dev' / 'make build', which reads apps/web/.env.local." >&2; exit 1) \
 && npm run build

# ── runner: standalone output only ────────────────────────────────────────────
FROM node:20-alpine AS runner
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
WORKDIR /app

RUN addgroup --system --gid 1001 nodejs \
 && adduser  --system --uid 1001 nextjs

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static     ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public           ./public

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
