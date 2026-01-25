from fastapi import APIRouter, HTTPException, Request

from core.config import get_report_api_key
from core.esb_service import EsbService
from core.security import get_current_user

router = APIRouter(prefix="/api/esb", tags=["esb"])


def _authorize(request: Request) -> None:
    api_key = (request.headers.get("X-API-KEY") or "").strip()
    if api_key:
        expected = get_report_api_key()
        if not expected or api_key != expected:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/token/status")
def esb_token_status(request: Request):
    _authorize(request)
    service = EsbService()
    return service.get_token_status()


@router.post("/token/sync")
def esb_token_sync(request: Request):
    _authorize(request)
    service = EsbService()
    return service.get_token_status(auto_refresh=True)
