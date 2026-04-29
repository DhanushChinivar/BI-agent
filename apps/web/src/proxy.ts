/**
 * Proxy (was: middleware) — Clerk is bypassed when NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is not set.
 * Restore Clerk by replacing this file with the clerkMiddleware version once keys are added.
 */
import { type NextRequest, NextResponse } from "next/server";

export default function proxy(_req: NextRequest) {
  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
