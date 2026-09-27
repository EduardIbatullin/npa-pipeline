"""Тесты поиска с MockTransport (без сети)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from npa_pipeline.authorities import AuthorityCache
from npa_pipeline.http_client import create_client
from npa_pipeline.models import MatchType, Query, Status
from npa_pipeline.search import search_document

FIXTURES = Path(__file__).parent / "fixtures"

MINPRIRODY = "d67a404e-260a-4a5b-b340-d34558da8bd6"
ROSNEDRA = "fc29509d-d3b7-40e4-9d36-fd0edf84e952"
PRESIDENT = "225698f1-cfbc-4e42-9caa-32f9f7403211"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _empty_page():
    return {
        "items": [],
        "currentPage": 1,
        "pagesTotalCount": 1,
        "itemsTotalCount": 0,
        "itemsPerPage": 200,
    }


def _page(items):
    return {
        "items": items,
        "currentPage": 1,
        "pagesTotalCount": 1,
        "itemsTotalCount": len(items),
        "itemsPerPage": 200,
    }


@pytest.fixture
def cache(tmp_path: Path) -> AuthorityCache:
    c = AuthorityCache(tmp_path / "auth.db")
    import sqlite3
    from npa_pipeline.normalize import normalize_name

    with sqlite3.connect(c.db_path) as conn:
        rows = [
            (MINPRIRODY, "Министерство природных ресурсов и экологии Российской Федерации", 89200),
            (ROSNEDRA, "Федеральное агентство по недропользованию", 89120),
            (PRESIDENT, "Президент Российской Федерации", 95000),
            ("wrong-guid", "Другой орган тестовый", 0),
        ]
        for guid, name, weight in rows:
            conn.execute(
                """
                INSERT INTO authorities (guid, name, name_norm, block, category, updated_at, weight)
                VALUES (?, ?, ?, NULL, NULL, 'now', ?)
                """,
                (guid, name, normalize_name(name), weight),
            )
        conn.commit()
    return c


def test_joint_doc_single_non_main_candidate(cache: AuthorityCache):
    """Один кандидат — Роснедра: сервер фильтрует, клиент не отсекает по основному GUID списка."""
    list_item = _load("documents_list_joint_247_04.json")["items"][0]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert "SignatoryAuthorityId=" + ROSNEDRA in url or "SignatoryAuthorityId" in url
        if "/api/Documents" in url:
            return httpx.Response(200, json=_page([list_item]))
        return httpx.Response(404, json={"error": url})

    transport = httpx.MockTransport(handler)
    with create_client(transport=transport) as client:
        result = search_document(
            client,
            Query(
                authority_guid=ROSNEDRA,
                number="247/04",
                date=date(2023, 4, 25),
            ),
            cache,
        )
    assert result.status == Status.FOUND
    assert result.eo_number == "0001202306010022"


def test_joint_doc_multi_candidate_needs_card(cache: AuthorityCache):
    """Много кандидатов: в списке только основной GUID → догрузка карточки обязательна."""
    list_item = _load("documents_list_joint_247_04.json")["items"][0]
    card = _load("document_joint_247_04.json")
    # Добавим ещё один «мин»-орган, чтобы название дало >1 кандидата
    import sqlite3
    from npa_pipeline.normalize import normalize_name

    with sqlite3.connect(cache.db_path) as conn:
        conn.execute(
            """
            INSERT INTO authorities (guid, name, name_norm, block, category, updated_at, weight)
            VALUES (?, ?, ?, NULL, NULL, 'now', ?)
            """,
            (
                "extra-min",
                "Министерство чего-то ещё",
                normalize_name("Министерство чего-то ещё"),
                1000,
            ),
        )
        conn.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/api/Documents" in url:
            assert "SignatoryAuthorityId" not in url
            return httpx.Response(200, json=_page([list_item]))
        if "/api/Document" in url:
            return httpx.Response(200, json=card)
        return httpx.Response(404, json={"error": url})

    transport = httpx.MockTransport(handler)
    with create_client(transport=transport) as client:
        result = search_document(
            client,
            Query(
                authority_name="министерство",
                number="247/04",
                date=date(2023, 4, 25),
            ),
            cache,
        )
    # «министерство» матчит Минприроды + «Министерство чего-то ещё», не Роснедра.
    # После обогащения карточки Минприроды в signatory_ids → found.
    assert result.status == Status.FOUND
    assert result.eo_number == "0001202306010022"
    assert MINPRIRODY in (result.document.signatory_ids if result.document else [])


def test_collision_exact_date(cache: AuthorityCache):
    sample = _load("documents_search_402_collision_sample.json")
    # если фикстура пустая — соберём вручную из карточек
    items = sample.get("items") or []
    if len(items) < 2:
        c2016 = _load("document_402_2016.json")
        c2026 = _load("document_402_2026.json")
        items = [
            {
                "eoNumber": c2016["eoNumber"],
                "number": c2016["number"],
                "documentDate": c2016["documentDate"],
                "signatoryAuthorityId": c2016["signatoryAuthorityId"],
                "documentTypeId": c2016.get("documentTypeId"),
                "name": c2016.get("name"),
                "complexName": c2016.get("complexName"),
            },
            {
                "eoNumber": c2026["eoNumber"],
                "number": c2026["number"],
                "documentDate": c2026["documentDate"],
                "signatoryAuthorityId": c2026["signatoryAuthorityId"],
                "documentTypeId": c2026.get("documentTypeId"),
                "name": c2026.get("name"),
                "complexName": c2026.get("complexName"),
            },
        ]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/api/Documents" in url:
            return httpx.Response(200, json=_page(items))
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with create_client(transport=transport) as client:
        result = search_document(
            client,
            Query(
                authority_guid=items[0]["signatoryAuthorityId"],
                number="402",
                date=date(2016, 7, 19),
            ),
            cache,
        )
    assert result.status == Status.FOUND
    assert result.eo_number == "0001201609130025"
    assert result.match_type == MatchType.EXACT


def test_fallback_skipped_for_letter_prefix(cache: AuthorityCache):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "/api/Documents" in str(request.url):
            return httpx.Response(200, json=_empty_page())
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with create_client(transport=transport) as client:
        result = search_document(
            client,
            Query(
                authority_guid=PRESIDENT,
                number="П-123",
                date=date(2020, 1, 1),
            ),
            cache,
        )
    assert result.status == Status.NOT_FOUND
    # только точный запрос, без NumberSearchType=1
    assert all("NumberSearchType=1" not in u for u in calls)


def test_fallback_skipped_short_prefix_multi_candidate(cache: AuthorityCache):
    """При многих кандидатах и цифровой части короче 3 — fallback не выполняется."""
    import sqlite3
    from npa_pipeline.normalize import normalize_name

    with sqlite3.connect(cache.db_path) as conn:
        conn.execute(
            """
            INSERT INTO authorities (guid, name, name_norm, block, category, updated_at, weight)
            VALUES (?, ?, ?, NULL, NULL, 'now', ?)
            """,
            ("extra-min", "Министерство чего-то ещё", normalize_name("Министерство чего-то ещё"), 1000),
        )
        conn.commit()

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=_empty_page())

    # «министерство» → Минприроды + «Министерство чего-то ещё»
    transport = httpx.MockTransport(handler)
    with create_client(transport=transport) as client:
        result = search_document(
            client,
            Query(
                authority_name="министерство",
                number="12-п",
                date=date(2020, 1, 1),
            ),
            cache,
        )
    assert result.status == Status.NOT_FOUND
    assert all("NumberSearchType=1" not in u for u in calls)


def test_fallback_digits_only_match(cache: AuthorityCache):
    card = _load("document_402_2016.json")
    list_item = {
        "eoNumber": card["eoNumber"],
        "number": card["number"],
        "documentDate": card["documentDate"],
        "signatoryAuthorityId": card["signatoryAuthorityId"],
        "documentTypeId": card.get("documentTypeId"),
        "name": card.get("name"),
        "complexName": card.get("complexName"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("NumberSearchType") == "0":
            # точный «402-П» ничего не даёт
            return httpx.Response(200, json=_empty_page())
        if params.get("NumberSearchType") == "1" and params.get("Number") == "402":
            return httpx.Response(200, json=_page([list_item]))
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with create_client(transport=transport) as client:
        result = search_document(
            client,
            Query(
                authority_guid=card["signatoryAuthorityId"],
                number="402-П",
                date=date(2016, 7, 19),
            ),
            cache,
        )
    assert result.status == Status.FOUND
    assert result.match_type == MatchType.DIGITS_ONLY
    assert result.eo_number == card["eoNumber"]


def test_diagnostics_single_wrong_candidate(cache: AuthorityCache):
    """Один неверный кандидат → not_found с диагностикой из запроса без органа."""
    list_item = _load("documents_list_joint_247_04.json")["items"][0]

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if "SignatoryAuthorityId" in params:
            return httpx.Response(200, json=_empty_page())
        if "/api/Documents" in str(request.url):
            return httpx.Response(200, json=_page([list_item]))
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with create_client(transport=transport) as client:
        result = search_document(
            client,
            Query(
                authority_guid="wrong-guid",
                number="247/04",
                date=date(2023, 4, 25),
            ),
            cache,
        )
    assert result.status == Status.NOT_FOUND
    assert result.diagnostics
    assert result.diagnostics[0].eo_number == "0001202306010022"


def test_out_of_range(cache: AuthorityCache):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_empty_page())

    transport = httpx.MockTransport(handler)
    with create_client(transport=transport) as client:
        result = search_document(
            client,
            Query(authority_guid=PRESIDENT, number="1", date=date(2010, 5, 1)),
            cache,
        )
    assert result.status == Status.OUT_OF_RANGE
