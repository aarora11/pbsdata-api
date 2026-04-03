from fastapi import APIRouter, Depends, Response
from api.middleware.rate_limit import check_rate_limit

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks")
async def create_webhook(response: Response, api_key_data: dict = Depends(check_rate_limit)):
    return {"status": "not_implemented"}


@router.get("/webhooks")
async def list_webhooks(response: Response, api_key_data: dict = Depends(check_rate_limit)):
    return {"data": []}


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: str, response: Response, api_key_data: dict = Depends(check_rate_limit)):
    return {"status": "not_implemented"}
