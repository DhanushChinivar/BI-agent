import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

// `/api/agent/v1/oauth/(.*)` is deliberately NOT public: the only route under it
// that the browser hits is `/start`, which binds the OAuth flow to an identity.
// Left public, Clerk yields no user, the BFF falls back to `X-User-Id: dev-user`
// and the tokens land on the wrong account. The provider's callback goes straight
// to the agent on :8000 and never passes through Next, so nothing needs exempting.
const isPublicRoute = createRouteMatcher([
  "/",
  "/sign-in(.*)",
  "/sign-up(.*)",
  "/api/billing/checkout(.*)",
]);

export default clerkMiddleware(async (auth, request) => {
  if (!isPublicRoute(request)) {
    await auth.protect();
  }
});

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
