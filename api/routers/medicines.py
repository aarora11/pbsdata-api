from fastapi import APIRouter, Depends, Response
from api.middleware.rate_limit import check_rate_limit

router = APIRouter(tags=["medicines"])


def _rl(response: Response, d: dict):
    response.headers["X-RateLimit-Limit"] = str(d.get("_rl_limit", 0))
    response.headers["X-RateLimit-Remaining"] = str(d.get("_rl_remaining", 0))
    response.headers["X-RateLimit-Reset"] = str(d.get("_rl_reset", 0))


@router.get("/medicines")
async def list_medicines(response: Response, api_key_data: dict = Depends(check_rate_limit)):
    _rl(response, api_key_data)
    return {"data": [], "meta": {"total": 0, "page": 1, "limit": 50}}


@router.get("/medicines/{medicine_id}")
async def get_medicine(medicine_id: str, response: Response, api_key_data: dict = Depends(check_rate_limit)):
    _rl(response, api_key_data)
    return {"status": "not_implemented"}
