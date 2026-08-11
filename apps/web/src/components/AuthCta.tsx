"use client";

import { ClerkLoading, Show } from "@clerk/nextjs";
import type { ReactNode } from "react";

/**
 * Swap a call to action based on whether the visitor is already signed in.
 *
 * Lives in its own `"use client"` module rather than inline in `page.tsx`:
 * importing Clerk's control components straight into the server component
 * opted the landing page out of static rendering (`○ /` became `ƒ /`), because
 * the import pulls request-scoped code into the server module graph. Behind a
 * client boundary the page prerenders again and this resolves in the browser.
 *
 * `<Show>` renders `null` while Clerk is loading — and `null` forever if Clerk
 * never loads at all, which is exactly what happens when
 * NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is missing from the *build*. A landing page
 * whose buttons silently never appear is the worst outcome here, so the
 * signed-out markup also renders under `<ClerkLoading>`: unauthenticated is the
 * right default for a marketing page, and a visitor always sees a way in.
 */
export function AuthCta({ out, in: whenIn }: { out: ReactNode; in: ReactNode }) {
  return (
    <>
      <ClerkLoading>{out}</ClerkLoading>
      <Show when="signed-in" fallback={out}>
        {whenIn}
      </Show>
    </>
  );
}
