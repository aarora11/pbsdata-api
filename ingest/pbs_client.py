"""PBS Government API client."""
import asyncio
import httpx
from typing import Optional
from api.config import get_settings


class PBSClient:
    def __init__(self, session: Optional[httpx.AsyncClient] = None):
        settings = get_settings()
        self.base_url = settings.PBS_API_BASE_URL
        self.subscription_key = settings.PBS_API_SUBSCRIPTION_KEY
        self.embargo_key = settings.PBS_API_EMBARGO_KEY
        self.delay = settings.PBS_REQUEST_DELAY_SECONDS
        self._session = session

    async def _get_session(self) -> httpx.AsyncClient:
        if self._session is None:
            headers = {"Ocp-Apim-Subscription-Key": self.subscription_key}
            if self.embargo_key:
                headers["embargo-key"] = self.embargo_key
            self._session = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=30.0,
            )
        return self._session

    async def get_all_pages(self, endpoint: str, params: dict) -> list[dict]:
        """Fetch all pages from a paginated endpoint."""
        session = await self._get_session()
        results = []
        page = 1
        while True:
            page_params = {**params, "page": page, "per-page": 200}
            response = await session.get(endpoint, params=page_params)
            response.raise_for_status()
            data = response.json()
            items = data.get("data", data) if isinstance(data, dict) else data
            if not items:
                break
            results.extend(items)
            total = data.get("total") if isinstance(data, dict) else None
            if total is not None and len(results) >= total:
                break
            page += 1
            await asyncio.sleep(self.delay)
        return results

    async def get_all_items(self, schedule_date: str) -> list[dict]:
        """Fetch all items for a given schedule date (YYYY-MM-DD)."""
        return await self.get_all_pages(
            "/drugs",
            {"schedule-date": schedule_date},
        )

    async def get_all_restrictions(self, schedule_date: str) -> list[dict]:
        """Fetch all restrictions for a given schedule date."""
        return await self.get_all_pages(
            "/restrictions",
            {"schedule-date": schedule_date},
        )

    async def close(self):
        if self._session:
            await self._session.aclose()
