from functools import lru_cache

from fastapi import Depends
from supabase import Client, create_client

from .config import get_setting


@lru_cache()
def get_supabase_client() -> Client | None:
    url = get_setting("SUPABASE_URL")
    key = get_setting("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)


@lru_cache()
def get_supabase_admin_client() -> Client | None:
    url = get_setting("SUPABASE_URL")
    service_key = get_setting("SUPABASE_SERVICE_KEY") or get_setting(
        "SUPABASE_SERVICE_ROLE_KEY"
    )
    if not url or not service_key:
        return None
    return create_client(url, service_key)


async def get_db(client: Client | None = Depends(get_supabase_client)):
    yield client
