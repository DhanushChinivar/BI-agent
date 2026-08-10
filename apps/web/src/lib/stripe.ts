import Stripe from "stripe";

/**
 * Build a Stripe client per request, never at module scope.
 *
 * `next build` imports every route module to collect page data, but
 * STRIPE_SECRET_KEY only exists at runtime — docker-compose passes
 * `apps/web/.env.local` as an `env_file`, not a build arg. A module-scope
 * `new Stripe(process.env.STRIPE_SECRET_KEY!)` therefore throws
 * "Neither apiKey nor config.authenticator provided" during the build and
 * fails the image, with a stack trace that points at the route rather than
 * at the missing variable.
 *
 * Deferring construction to the handler also means a missing key surfaces as
 * a 500 on the one endpoint that needs it, rather than taking down the build.
 */
export function stripeClient(): Stripe {
  const apiKey = process.env.STRIPE_SECRET_KEY;
  if (!apiKey) {
    throw new Error("STRIPE_SECRET_KEY is not set");
  }
  return new Stripe(apiKey);
}
