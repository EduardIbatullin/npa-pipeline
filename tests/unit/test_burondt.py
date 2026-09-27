"""Тесты парсинга карточек burondt.ru и обхода диапазона UrlId."""

from __future__ import annotations

from pathlib import Path

import httpx

from npa_pipeline import burondt
from npa_pipeline.http_client import create_client

FIXTURES = Path(__file__).parent / "fixtures" / "burondt"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_card_its_ok():
    card = burondt.parse_card(_read("its_card.html"), url_id=1640)
    assert card is not None
    assert card.designation == "ИТС 28-2021"
    assert card.title == "ИТС НДТ 28-2021 Добыча нефти"
    assert burondt.is_its(card)
    assert len(card.files) == 2

    roles = {f.role for f in card.files}
    assert roles == {"document", "order"}

    order = next(f for f in card.files if f.role == "order")
    assert order.file_id == 2504
    number, order_date = burondt.extract_order_meta(order.caption)
    assert number == "835"  # подпись карточки — известно, что может расходиться с реальным сканом
    assert order_date is not None

    document = next(f for f in card.files if f.role == "document")
    assert document.file_id == 2190


def test_parse_card_non_its_is_filtered():
    card = burondt.parse_card(_read("non_its_card.html"), url_id=50)
    assert card is not None
    assert card.designation.startswith("ГОСТ")
    assert not burondt.is_its(card)


def test_parse_card_empty_returns_none():
    card = burondt.parse_card(_read("empty_card.html"), url_id=2650)
    assert card is None


def test_fetch_card_html_hits_burondt_base_url():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        seen["path"] = request.url.path
        seen["url_id"] = request.url.params.get("UrlId")
        return httpx.Response(200, text=_read("its_card.html"))

    with burondt.create_burondt_client(transport=httpx.MockTransport(handler)) as client:
        html = burondt.fetch_card_html(client, 1640)

    assert seen["host"] == "burondt.ru"
    assert seen["path"] == burondt.CARD_PATH
    assert seen["url_id"] == "1640"
    assert "ИТС 28-2021" in html


def test_download_file_extracts_filename_and_headers():
    # httpx.Response конструктор требует ASCII в headers (Cyrillic реально приходит по
    # сети как сырые байты — сам сервер это отдаёт, проверено вживую curl'ом; здесь
    # достаточно ASCII-имени, чтобы проверить именно логику извлечения из заголовка).
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("UrlId") == "2190"
        return httpx.Response(
            200,
            content=b"%PDF-fake-bytes",
            headers={"Content-Disposition": 'attachment; filename="prikaz-ot-21.10.pdf"'},
        )

    with burondt.create_burondt_client(transport=httpx.MockTransport(handler)) as client:
        data, content_length, filename = burondt.download_file(client, 2190)

    assert data == b"%PDF-fake-bytes"
    assert filename == "prikaz-ot-21.10.pdf"


def test_iter_cards_skips_gaps_and_non_its():
    pages = {
        10: _read("non_its_card.html"),
        11: _read("empty_card.html"),
        12: _read("its_card.html"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url_id = int(request.url.params["UrlId"])
        return httpx.Response(200, text=pages[url_id])

    with create_client(base_url=burondt.BURONDT_BASE_URL, transport=httpx.MockTransport(handler)) as client:
        results = list(burondt.iter_cards(client, lo=10, hi=12, pause_seconds=0))

    assert [r[0] for r in results] == [10, 11, 12]
    assert results[0][1] is not None and not burondt.is_its(results[0][1])
    assert results[1][1] is None
    assert results[2][1] is not None and burondt.is_its(results[2][1])


def test_find_upper_bound_stops_after_confirm_run():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        url_id = int(request.url.params["UrlId"])
        text = _read("its_card.html") if url_id <= 15 else _read("empty_card.html")
        return httpx.Response(200, text=text)

    with create_client(base_url=burondt.BURONDT_BASE_URL, transport=httpx.MockTransport(handler)) as client:
        boundary = burondt.find_upper_bound(
            client, from_url_id=1, confirm_empty_run=3, pause_seconds=0
        )

    assert boundary == 15
    assert call_count["n"] == 15 + 3
