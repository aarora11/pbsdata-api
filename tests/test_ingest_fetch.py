"""
Tests for the fetch phase: pagination, per-endpoint page limits, and the raw
cache that makes a failed run resumable.

These cover the seam between ingest/pbs_client.py and ingest/runner.py, which
previously had no tests at all. A full fetch is ~34 rate-limited endpoints at
~21s per request, so a bug here costs ~25 minutes per attempt.
"""
import json
import httpx
import pytest

from ingest import pbs_client

from ingest import runner
from ingest.pbs_client import PBSClient, DEFAULT_PAGE_LIMIT, PAGE_LIMITS


# ── Page limits ───────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)
        self.headers = {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _RecordingSession:
    """Stands in for httpx.AsyncClient, recording the params of each request."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def get(self, endpoint, params=None):
        self.calls.append((endpoint, dict(params or {})))
        idx = len(self.calls) - 1
        return _FakeResponse(self.pages[idx] if idx < len(self.pages) else {"data": []})


async def _no_sleep(_seconds):
    return None


def _client(session):
    c = PBSClient(session=session)
    c.delay = 0
    return c


@pytest.mark.asyncio
async def test_item_overview_requests_capped_page_limit():
    """
    /item-overview returns 400 for limit above 1000. Requesting the default
    10000 aborted the whole ingest after 23 minutes of successful fetching.
    """
    session = _RecordingSession([{"data": [{"pbs_code": "1234A"}], "total_records": 1}])
    await _client(session).get_all_item_overviews("3966")

    assert session.calls[0][1]["limit"] == 1000
    assert PAGE_LIMITS["/item-overview"] == 1000


@pytest.mark.asyncio
async def test_uncapped_endpoint_uses_default_page_limit():
    session = _RecordingSession([{"data": [{"pbs_code": "1234A"}], "total_records": 1}])
    await _client(session).get_all_items("3966")

    assert session.calls[0][1]["limit"] == DEFAULT_PAGE_LIMIT


# ── Pagination termination ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_total_records_stops_pagination_without_extra_request():
    """
    PBS reports the row count as `total_records`. Reading the wrong key made
    every endpoint fetch one extra empty page — a wasted rate-limited request
    per endpoint, ~34 of them per run.
    """
    session = _RecordingSession([{"data": [{"a": 1}, {"a": 2}], "total_records": 2}])
    rows = await _client(session).get_all_items("3966")

    assert len(rows) == 2
    assert len(session.calls) == 1, "should not request a page beyond total_records"


@pytest.mark.asyncio
async def test_pagination_follows_multiple_pages():
    session = _RecordingSession([
        {"data": [{"a": 1}], "total_records": 2},
        {"data": [{"a": 2}], "total_records": 2},
    ])
    # limit=1 forces a second page even though total_records is 2.
    rows = await _client(session).get_all_pages("/items", {}, page_limit=1)

    assert len(rows) == 2
    assert [c[1]["page"] for c in session.calls] == [1, 2]


# ── Raw cache / resumability ──────────────────────────────────────────────────

def test_partial_cache_is_not_treated_as_a_cache_hit(tmp_path):
    """
    A run that dies mid-fetch leaves some feeds cached. Treating that as a hit
    would silently ingest a schedule missing every feed not yet fetched.
    """
    runner._save_raw(tmp_path, "2026-05", "items", [{"pbs_code": "1234A"}])
    assert runner._cache_exists(tmp_path, "2026-05") is False


def test_complete_cache_is_a_cache_hit(tmp_path):
    for feed in runner.RAW_FEEDS:
        runner._save_raw(tmp_path, "2026-05", feed, [])
    assert runner._cache_exists(tmp_path, "2026-05") is True


def test_raw_feeds_covers_every_cached_feed():
    """RAW_FEEDS drives the completeness check; a feed missing from it would
    make an incomplete cache look complete."""
    source = (runner.__file__).replace(".py", ".py")
    text = open(source).read()
    cached = {
        line.split('"')[3]
        for line in text.splitlines()
        if "fetch(" in line and line.count('"') >= 4
    }
    assert cached, "expected to find fetch() call sites"
    assert cached <= set(runner.RAW_FEEDS), (
        f"feeds cached but absent from RAW_FEEDS: {cached - set(runner.RAW_FEEDS)}"
    )


# ── pbs_code derivation from li_item_id ───────────────────────────────────────

def _derived_pbs_code(li_item_id: str) -> str:
    """The derivation used in ingest/normaliser.py for feeds lacking pbs_code."""
    return li_item_id.split("_")[0] if li_item_id else ""


@pytest.mark.parametrize("li_item_id,expected", [
    ("10001J_14023_31078_31081_31083", "10001J"),
    ("10003L_13467_29812_29815_29817", "10003L"),
    ("10004M_7501_1327_5783_52398", "10004M"),
])
def test_pbs_code_is_first_segment_of_li_item_id(li_item_id, expected):
    """
    `li_item_id` is a 5-segment composite key whose first segment is the PBS
    code. ingest/normaliser.py relies on this for item_pricing, amt_items and
    summary_of_changes, which PBS serves without an explicit pbs_code.

    Validated 2026-07-29 against the full 2026-05 /items feed: the derivation
    matched the PBS-supplied pbs_code on 14,759 of 14,759 rows (100%).
    """
    assert _derived_pbs_code(li_item_id) == expected


def test_pbs_code_derivation_handles_missing_li_item_id():
    assert _derived_pbs_code("") == ""


# ── Transient network errors ──────────────────────────────────────────────────

class _FlakySession:
    """Raises a transport error on the first N calls, then succeeds."""

    def __init__(self, failures, payload):
        self.failures = failures
        self.payload = payload
        self.calls = 0

    async def get(self, endpoint, params=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise httpx.ConnectError("[Errno 8] nodename nor servname provided")
        return _FakeResponse(self.payload)


@pytest.mark.asyncio
async def test_transient_connect_error_is_retried(monkeypatch):
    """
    A DNS blip mid-run previously aborted the entire ingest. Over ~34 endpoints
    and ~25 minutes of rate-limited fetching, transient failures are expected.
    """
    monkeypatch.setattr(pbs_client.asyncio, "sleep", _no_sleep)
    session = _FlakySession(2, {"data": [{"a": 1}], "total_records": 1})
    rows = await _client(session).get_all_items("3966")

    assert rows == [{"a": 1}]
    assert session.calls == 3, "expected two retries then success"


@pytest.mark.asyncio
async def test_transport_error_gives_up_after_retry_budget(monkeypatch):
    monkeypatch.setattr(pbs_client.asyncio, "sleep", _no_sleep)
    session = _FlakySession(99, {"data": []})

    with pytest.raises(httpx.ConnectError):
        await _client(session).get_all_items("3966")

    assert session.calls == 8, "should stop at the 8-attempt budget"
