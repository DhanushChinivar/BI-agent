/**
 * POST /api/billing/portal
 * Creates a Stripe Customer Portal session for managing subscriptions.
 */
import { auth } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import Stripe from "stripe";

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);

export async function POST(req: Request) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const { customerId } = await req.json();
  if (!customerId) return NextResponse.json({ error: "Missing customerId" }, { status: 400 });

  const session = await stripe.billingPortal.sessions.create({
    customer: customerId,
    return_url: `${process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3000"}/settings`,
  });

  return NextResponse.json({ url: session.url });
}
