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
            headers = {"subscription-key": self.subscription_key}
            if self.embargo_key:
                headers["embargo-key"] = self.embargo_key
            self._session = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=60.0,
            )
        return self._session

    async def get_all_pages(self, endpoint: str, params: dict) -> list[dict]:
        """Fetch all pages from a paginated endpoint, with retry on 429."""
        session = await self._get_session()
        results = []
        page = 1
        while True:
            page_params = {**params, "page": page, "limit": 10000}
            for attempt in range(8):
                response = await session.get(endpoint, params=page_params)
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    wait = int(retry_after) if retry_after and retry_after.isdigit() else 30 * (attempt + 1)
                    await asyncio.sleep(wait)
                    continue
                response.raise_for_status()
                break
            else:
                raise RuntimeError(f"Rate-limited on {endpoint} after 8 retries")
            content = response.text.strip()
            if not content:
                break
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

    # ── All PBS endpoints ──────────────────────────────────────────────────────

    async def get_available_schedules(self) -> list[dict]:
        """Fetch list of available schedule codes from PBS API."""
        return await self.get_all_pages("/schedules", {})

    async def get_all_items(self, schedule_code: str) -> list[dict]:
        """Fetch all items for a given schedule code."""
        return await self.get_all_pages("/items", {"schedule_code": schedule_code})

    async def get_all_restrictions(self, schedule_code: str) -> list[dict]:
        """Fetch all restrictions for a given schedule code."""
        return await self.get_all_pages("/restrictions", {"schedule_code": schedule_code})

    async def get_all_fees(self, schedule_code: str) -> list[dict]:
        """Fetch all fee records for a given schedule code."""
        return await self.get_all_pages("/fees", {"schedule_code": schedule_code})

    async def get_all_prescribing_texts(self, schedule_code: str) -> list[dict]:
        """Fetch all prescribing text records for a given schedule code."""
        return await self.get_all_pages("/prescribing-texts", {"schedule_code": schedule_code})

    async def get_all_indications(self, schedule_code: str) -> list[dict]:
        """Fetch all medical indications for a given schedule code."""
        return await self.get_all_pages("/indications", {"schedule_code": schedule_code})

    async def get_all_amt_items(self, schedule_code: str) -> list[dict]:
        """Fetch all AMT concepts for a given schedule code."""
        return await self.get_all_pages("/amt-items", {"schedule_code": schedule_code})

    async def get_all_atc_codes(self, schedule_code: str) -> list[dict]:
        """Fetch all ATC codes for a given schedule code."""
        return await self.get_all_pages("/atc-codes", {"schedule_code": schedule_code})

    async def get_all_copayments(self, schedule_code: str) -> list[dict]:
        """Fetch all copayment records for a given schedule code."""
        return await self.get_all_pages("/copayments", {"schedule_code": schedule_code})

    async def get_all_criteria(self, schedule_code: str) -> list[dict]:
        """Fetch all clinical criteria for a given schedule code."""
        return await self.get_all_pages("/criteria", {"schedule_code": schedule_code})

    async def get_all_criteria_parameter_relationships(self, schedule_code: str) -> list[dict]:
        """Fetch criteria <-> parameter relationships for a given schedule code."""
        return await self.get_all_pages("/criteria-parameter-relationships", {"schedule_code": schedule_code})

    async def get_all_dispensing_rules(self, schedule_code: str) -> list[dict]:
        """Fetch all dispensing rules for a given schedule code."""
        return await self.get_all_pages("/dispensing-rules", {"schedule_code": schedule_code})

    async def get_all_organisations(self, schedule_code: str) -> list[dict]:
        """Fetch all organisations (manufacturers/sponsors) for a given schedule code."""
        return await self.get_all_pages("/organisations", {"schedule_code": schedule_code})

    async def get_all_parameters(self, schedule_code: str) -> list[dict]:
        """Fetch all parameters for a given schedule code."""
        return await self.get_all_pages("/parameters", {"schedule_code": schedule_code})

    async def get_all_prescribers(self, schedule_code: str) -> list[dict]:
        """Fetch all prescriber types for a given schedule code."""
        return await self.get_all_pages("/prescribers", {"schedule_code": schedule_code})

    async def get_all_programs(self, schedule_code: str) -> list[dict]:
        """Fetch all PBS programs for a given schedule code."""
        return await self.get_all_pages("/programs", {"schedule_code": schedule_code})

    async def get_all_markup_bands(self, schedule_code: str) -> list[dict]:
        """Fetch all markup bands for a given schedule code."""
        return await self.get_all_pages("/markup-bands", {"schedule_code": schedule_code})

    async def get_all_containers(self, schedule_code: str) -> list[dict]:
        """Fetch all container records for a given schedule code."""
        return await self.get_all_pages("/containers", {"schedule_code": schedule_code})

    async def get_all_summary_of_changes(self, schedule_code: str) -> list[dict]:
        """Fetch official PBS published summary of changes for a given schedule code."""
        return await self.get_all_pages("/summary-of-changes", {"schedule_code": schedule_code})

    async def get_all_extemporaneous_ingredients(self, schedule_code: str) -> list[dict]:
        """Fetch all extemporaneous ingredients for a given schedule code."""
        return await self.get_all_pages("/extemporaneous-ingredients", {"schedule_code": schedule_code})

    async def get_all_extemporaneous_preparations(self, schedule_code: str) -> list[dict]:
        """Fetch all extemporaneous preparations for a given schedule code."""
        return await self.get_all_pages("/extemporaneous-preparations", {"schedule_code": schedule_code})

    async def get_all_standard_formula_preparations(self, schedule_code: str) -> list[dict]:
        """Fetch all standard formula preparations for a given schedule code."""
        return await self.get_all_pages("/standard-formula-preparations", {"schedule_code": schedule_code})

    async def get_all_item_overviews(self, schedule_code: str) -> list[dict]:
        """Fetch item overview data (artg_id, sponsor, etc.) for a given schedule code."""
        return await self.get_all_pages("/item-overviews", {"schedule_code": schedule_code})

    async def get_all_item_dispensing_rules(self, schedule_code: str) -> list[dict]:
        """Fetch item <-> dispensing rule relationships for a given schedule code."""
        return await self.get_all_item_dispensing_rule_relationships(schedule_code)

    async def get_all_program_dispensing_rules(self, schedule_code: str) -> list[dict]:
        """Fetch program-level dispensing rules for a given schedule code."""
        return await self.get_all_dispensing_rules(schedule_code)

    async def get_all_item_amt(self, schedule_code: str) -> list[dict]:
        """Fetch item <-> AMT concept relationships for a given schedule code."""
        return await self.get_all_pages("/item-amt-relationships", {"schedule_code": schedule_code})

    async def get_all_item_prescribing_texts(self, schedule_code: str) -> list[dict]:
        """Fetch item <-> prescribing text relationships for a given schedule code."""
        return await self.get_all_item_prescribing_text_relationships(schedule_code)

    # ── Relationship endpoints ─────────────────────────────────────────────────

    async def get_all_item_atc_relationships(self, schedule_code: str) -> list[dict]:
        """Fetch item <-> ATC code relationships for a given schedule code."""
        return await self.get_all_pages("/item-atc-relationships", {"schedule_code": schedule_code})

    async def get_all_item_dispensing_rule_relationships(self, schedule_code: str) -> list[dict]:
        """Fetch item <-> dispensing rule relationships for a given schedule code."""
        return await self.get_all_pages("/item-dispensing-rule-relationships", {"schedule_code": schedule_code})

    async def get_all_item_organisation_relationships(self, schedule_code: str) -> list[dict]:
        """Fetch item <-> organisation relationships for a given schedule code."""
        return await self.get_all_pages("/item-organisation-relationships", {"schedule_code": schedule_code})

    async def get_all_item_prescribing_text_relationships(self, schedule_code: str) -> list[dict]:
        """Fetch item <-> prescribing text relationships for a given schedule code."""
        return await self.get_all_pages("/item-prescribing-text-relationships", {"schedule_code": schedule_code})

    async def get_all_item_pricing_events(self, schedule_code: str) -> list[dict]:
        """Fetch item pricing events for a given schedule code."""
        return await self.get_all_pages("/item-pricing-events", {"schedule_code": schedule_code})

    async def get_all_item_restriction_relationships(self, schedule_code: str) -> list[dict]:
        """Fetch item <-> restriction relationships for a given schedule code."""
        return await self.get_all_pages("/item-restriction-relationships", {"schedule_code": schedule_code})

    async def get_all_restriction_prescribing_text_relationships(self, schedule_code: str) -> list[dict]:
        """Fetch restriction <-> prescribing text relationships for a given schedule code."""
        return await self.get_all_pages("/restriction-prescribing-text-relationships", {"schedule_code": schedule_code})

    async def get_all_container_organisation_relationships(self, schedule_code: str) -> list[dict]:
        """Fetch container <-> organisation relationships for a given schedule code."""
        return await self.get_all_pages("/container-organisation-relationships", {"schedule_code": schedule_code})

    async def get_all_extemporaneous_prep_sfp_relationships(self, schedule_code: str) -> list[dict]:
        """Fetch extemporaneous prep <-> standard formula preparation relationships."""
        return await self.get_all_pages("/extemporaneous-prep-sfp-relationships", {"schedule_code": schedule_code})

    async def get_all_extemporaneous_tariffs(self, schedule_code: str) -> list[dict]:
        """Fetch extemporaneous tariffs for a given schedule code."""
        return await self.get_all_pages("/extemporaneous-tariffs", {"schedule_code": schedule_code})

    async def close(self):
        if self._session:
            await self._session.aclose()
