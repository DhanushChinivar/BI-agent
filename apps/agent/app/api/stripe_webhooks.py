"""POST /v1/stripe/webhook — handles Stripe subscription lifecycle events."""
import logging

import stripe
from fastapi import APIRouter, Header, HTTPException, Request

from app.config.settings import get_settings
from app.db.engine import get_session_factory
from app.db.plan_crud import set_plan

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["billing"])


@router.post("/stripe/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(alias="stripe-signature"),
) -> dict:
    settings = get_settings()
    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, settings.stripe_webhook_secret
        )
    except stripe.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")

    factory = get_session_factory()

    match event["type"]:
        case "customer.subscription.created" | "customer.subscription.updated":
            sub = event["data"]["object"]
            user_id = sub["metadata"].get("user_id")
            if not user_id:
                logger.warning("Stripe subscription missing user_id metadata: %s", sub["id"])
                return {"received": True}
            plan = "pro" if sub["status"] == "active" else "free"
            async with factory() as session:
                await set_plan(
                    session,
                    user_id=user_id,
                    plan=plan,
                    stripe_customer_id=sub["customer"],
                    stripe_subscription_id=sub["id"],
                )
            logger.info("Set plan=%s for user=%s", plan, user_id)

        case "customer.subscription.deleted":
            sub = event["data"]["object"]
            user_id = sub["metadata"].get("user_id")
            if user_id:
                async with factory() as session:
                    await set_plan(session, user_id=user_id, plan="free")
                logger.info("Downgraded user=%s to free (subscription cancelled)", user_id)

    return {"received": True}
