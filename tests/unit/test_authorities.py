"""Тесты кэша органов и резолвинга."""

from pathlib import Path

import httpx
import sqlite3

from npa_pipeline.authorities import AuthorityCache
from npa_pipeline.http_client import create_client
from npa_pipeline.normalize import normalize_name


def _insert(cache: AuthorityCache, rows: list[tuple]) -> None:
    """rows: (guid, name, weight|None)."""
    with sqlite3.connect(cache.db_path) as conn:
        for guid, name, weight in rows:
            conn.execute(
                """
                INSERT INTO authorities (guid, name, name_norm, block, category, updated_at, weight)
                VALUES (?, ?, ?, NULL, NULL, 't', ?)
                """,
                (guid, name, normalize_name(name), weight),
            )
        conn.commit()


def test_resolve_exact_and_partial(tmp_path: Path):
    cache = AuthorityCache(tmp_path / "a.db")
    _insert(
        cache,
        [
            ("g1", "Министерство финансов Российской Федерации", 88080),
            ("g2", "Министерство финансов Республики Татарстан", 66978),
            ("g3", "Президент Российской Федерации", 95000),
        ],
    )

    assert cache.resolve(authority_guid="g3") == ["g3"]
    assert set(cache.resolve(authority_name="Министерство финансов")) == {"g1", "g2"}
    assert cache.resolve(authority_name="неизвестный орган xyz") == []


def test_build_cache_from_mock(tmp_path: Path):
    payload = [
        {"id": "a1", "name": "Орган А", "weight": 1},
        {"id": "a2", "name": "Орган Б", "weight": 2},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/api/SignatoryAuthorities" in str(request.url)
        return httpx.Response(200, json=payload)

    cache = AuthorityCache(tmp_path / "b.db")
    with create_client(transport=httpx.MockTransport(handler)) as client:
        n = cache.build(client)
    assert n == 2
    assert cache.resolve(authority_name="Орган А") == ["a1"]


def test_suggest_federal_before_regional(tmp_path: Path):
    cache = AuthorityCache(tmp_path / "s.db")
    _insert(
        cache,
        [
            # короткий региональный без weight — раньше всплывал первым по длине
            ("g2", "Министерство финансов Кузбасса", None),
            ("g3", "Министерство финансов Республики Татарстан", 66978),
            ("g1", "Министерство финансов Российской Федерации", 88080),
            ("g4", "Президент Российской Федерации", 95000),
        ],
    )

    items = cache.suggest("министерство финансов", limit=10)
    names = [i["name"] for i in items]
    assert names[0] == "Министерство финансов Российской Федерации"
    assert "Президент Российской Федерации" not in names
    assert {i["guid"] for i in items} == {"g1", "g2", "g3"}


def test_suggest_heuristic_without_weight(tmp_path: Path):
    """Пока weight не загружен — федеральный по окончанию «...Российской Федерации»."""
    cache = AuthorityCache(tmp_path / "h.db")
    _insert(
        cache,
        [
            ("g2", "Министерство финансов Кузбасса", None),
            ("g1", "Министерство финансов Российской Федерации", None),
        ],
    )
    items = cache.suggest("министерство финансов", limit=5)
    assert items[0]["guid"] == "g1"
