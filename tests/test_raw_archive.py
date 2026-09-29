"""Tests for archiving older raw months out of Postgres.

The archive deletes rows from raw_feed_rows, so the properties that matter are that it
round-trips exactly, and that it never deletes a month it could not verify.
"""
import gzip
import json

import pytest

from ingest import raw_archive
from ingest.raw_archive import (
    archive_path, load_endpoint, load_raw_endpoint, prune_raw_layer, restore_month,
)

MONTHS = ("1999-01", "1999-02", "1999-03")

# Numeric scale, nulls, nesting and non-ASCII all have to survive: jsonb keeps 25.00
# distinct from 25.0, and a naive parse-and-redump round trip loses it.
PAYLOADS = [
    '{"pbs_code": "1234A", "price": 25.00, "nested": {"name": "Acmé", "abn": null}}',
    '{"pbs_code": "5678B", "price": 7.7, "brands": ["X", "Y"]}',
]


async def _seed(db):
    await db.execute("DELETE FROM raw_feed_rows")
    for month in MONTHS:
        for i, payload in enumerate(PAYLOADS):
            await db.execute(
                "INSERT INTO raw_feed_rows (month, schedule_code, endpoint, row_index, payload) "
                "VALUES ($1, '9999', '/items', $2, $3::jsonb)",
                month, i, payload,
            )


async def _rows(db, month):
    return [tuple(r) for r in await db.fetch(
        "SELECT schedule_code, endpoint, row_index, payload::text, fetched_at "
        "FROM raw_feed_rows WHERE month = $1 ORDER BY endpoint, row_index", month,
    )]


@pytest.mark.asyncio
async def test_prune_keeps_newest_months_and_archives_the_rest(db, tmp_path):
    await _seed(db)

    moved = await prune_raw_layer(db, keep=2, archive_dir=tmp_path)

    assert moved == ["1999-01"]
    assert archive_path("1999-01", tmp_path).exists()
    held = [r["month"] for r in await db.fetch(
        "SELECT DISTINCT month FROM raw_feed_rows ORDER BY month")]
    assert held == ["1999-02", "1999-03"]


@pytest.mark.asyncio
async def test_archive_restores_byte_for_byte(db, tmp_path):
    await _seed(db)
    before = await _rows(db, "1999-01")

    await prune_raw_layer(db, keep=2, archive_dir=tmp_path)
    assert await _rows(db, "1999-01") == []

    restored = await restore_month(db, "1999-01", archive_dir=tmp_path)

    assert restored == len(PAYLOADS)
    assert await _rows(db, "1999-01") == before
    assert '"price": 25.00' in before[0][3], "fixture lost its numeric scale"


@pytest.mark.asyncio
async def test_readers_fall_back_to_the_archive(db, tmp_path, monkeypatch):
    await _seed(db)
    await prune_raw_layer(db, keep=2, archive_dir=tmp_path)
    monkeypatch.setattr(raw_archive, "ARCHIVE_DIR", tmp_path)

    from_archive = await load_raw_endpoint(db, "1999-01", "/items")
    from_table = await load_raw_endpoint(db, "1999-02", "/items")

    assert from_archive == from_table == [json.loads(p) for p in PAYLOADS]
    assert load_endpoint("1999-01", "/other", tmp_path) == []
    assert load_endpoint("1998-12", "/items", tmp_path) is None


@pytest.mark.asyncio
async def test_a_bad_archive_write_deletes_nothing(db, tmp_path, monkeypatch):
    """Mutation test: drop one row on the way to disk; verification must refuse."""
    await _seed(db)
    before = await _rows(db, "1999-01")
    real_open = gzip.open

    class DropsARow:
        def __init__(self, f): self._f = f
        def write(self, line):
            if "5678B" not in line:
                self._f.write(line)
        def __enter__(self): return self
        def __exit__(self, *a): self._f.close()

    def lossy_open(path, mode="rb", **kw):
        f = real_open(path, mode, **kw)
        return DropsARow(f) if mode.startswith("w") else f

    monkeypatch.setattr(raw_archive.gzip, "open", lossy_open)

    with pytest.raises(ValueError, match="did not read back identical"):
        await prune_raw_layer(db, keep=2, archive_dir=tmp_path)

    assert await _rows(db, "1999-01") == before
    assert not archive_path("1999-01", tmp_path).exists()
    assert not list(tmp_path.glob("*.tmp"))
