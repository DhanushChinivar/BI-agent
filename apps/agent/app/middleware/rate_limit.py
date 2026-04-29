"""Shared slowapi limiter instance.

Import `limiter` and use @limiter.limit("N/minute") on route functions.
The key_func returns the client IP for global limits; swap it per-route
for user-scoped limits via get_user_id_for_limit().
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)


def get_user_id_for_limit(request) -> str:  # type: ignore[type-arg]
    """Rate-limit key scoped to authenticated user, falls back to IP."""
    user_id = getattr(request.state, "user_id", None)
    return user_id if user_id and user_id != "anonymous" else get_remote_address(request)
