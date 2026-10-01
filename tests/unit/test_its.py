"""Тесты ItsCache: upsert, резолвинг, инкрементальный/полный обход."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from npa_pipeline import burondt
from npa_pipeline.http_client import create_client
from npa_pipeline.its import ItsCache

FIXTURES = Path(__file__).parent / "fixtures" / "burondt"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_upsert_card_is_pure_upsert(tmp_path: Path):
    cache = ItsCache(tmp_path / "its.db")
    card = burondt.parse_card(_read("its_card.html"), url_id=1640)
    assert card is not None

    cache.upsert_card(card)
    assert cache.count_its_documents() == 1
    first = cache.get_card(1640)
    assert first["title"] == "ИТС НДТ 28-2021 Добыча нефти"

    card.title = "Изменённое название"
    cache.upsert_card(card)
    assert cache.count_its_documents() == 1  # не дублирует
    second = cache.get_card(1640)
    assert second["title"] == "Изменённое название"
    assert second["first_seen_at"] == first["first_seen_at"]  # сохраняется


def test_upsert_card_records_files_with_order_meta_caption(tmp_path: Path):
    cache = ItsCache(tmp_path / "its.db")
    card = burondt.parse_card(_read("its_card.html"), url_id=1640)
    cache.upsert_card(card)

    files = cache.list_files(1640)
    assert {f["file_id"] for f in files} == {2190, 2504}
    order = next(f for f in files if f["role"] == "order")
    assert order["order_number_caption"] == "835"


def test_resolve_designation_exact_and_none(tmp_path: Path):
    cache = ItsCache(tmp_path / "its.db")
    card = burondt.parse_card(_read("its_card.html"), url_id=1640)
    cache.upsert_card(card)

    assert cache.resolve("ИТС 28-2021") == [1640]
    assert cache.resolve("итс 28-2021") == [1640]  # регистронезависимо
    assert cache.resolve("ИТС 999-2099") == []
    assert cache.resolve("") == []


def test_resolve_strips_thematic_title_after_designation(tmp_path: Path):
    """Полная строка с темой: «ИТС 51-2025 Литейное…» → как «ИТС 51-2025»."""
    cache = ItsCache(tmp_path / "its.db")
    cache.upsert_card(
        burondt.ItsCard(
            url_id=2440,
            designation="ИТС 51-2025",
            title="Литейное производство изделий из черных металлов",
            files=[
                burondt.ItsCardFile(file_id=1, caption="ИТС 51-2025", role="document"),
            ],
        )
    )
    assert cache.resolve(
        "ИТС 51-2025 Литейное производство изделий из черных металлов"
    ) == [2440]
    assert cache.resolve("ИТС 51-2025") == [2440]

def test_resolve_underscore_and_skips_word_variant(tmp_path: Path):
    """«22_1-2021_» ≈ «22.1-2021»; карточка Word не должна перебивать PDF."""
    cache = ItsCache(tmp_path / "its.db")
    cache.upsert_card(
        burondt.ItsCard(
            url_id=1647,
            designation="ИТС 22.1-2021",
            title="ИТС НДТ 22.1-2021",
            files=[
                burondt.ItsCardFile(file_id=2196, caption="ИТС НДТ 22.1-2021", role="document"),
                burondt.ItsCardFile(
                    file_id=2500,
                    caption="Приказ 2 декабря 2021 г. № 2690 об утверждении ИТС 22.1-2021",
                    role="order",
                ),
            ],
        )
    )
    cache.upsert_card(
        burondt.ItsCard(
            url_id=2552,
            designation="ИТС 22.1-2021 в формате Word",
            title="ИТС 22.1-2021 в формате Word",
            files=[
                burondt.ItsCardFile(file_id=3687, caption="ИТС 22.1-2021", role="document"),
            ],
        )
    )

    assert cache.resolve("ИТС 22_1-2021_") == [1647]
    assert cache.resolve("ИТС 22.1-2021") == [1647]
    assert cache.find_related_url_ids(1647) == [2552]
    assert cache.find_related_url_ids(2552) == [1647]


def test_resolve_finds_via_order_caption_when_designation_lacks_year(tmp_path: Path):
    # Реальный случай (UrlId=1150, burondt.ru): «Обозначение» карточки — «ИТС НДТ 47»,
    # без года; год есть только в подписи файла-приказа.
    cache = ItsCache(tmp_path / "its.db")
    card = burondt.ItsCard(
        url_id=1150,
        designation="ИТС НДТ 47",
        title="ИТС НДТ 47",
        files=[
            burondt.ItsCardFile(file_id=1420, caption="ИТС 47", role="document"),
            burondt.ItsCardFile(
                file_id=2476,
                caption="Приказ №2846 от 15.12.2017 об утверждении ИТС 47-2017",
                role="order",
            ),
        ],
    )
    cache.upsert_card(card)

    assert cache.resolve("ИТС 47-2017") == [1150]
    assert cache.resolve("итс 47-2017") == [1150]  # регистронезависимо
    assert cache.resolve("ИТС 999-2099") == []  # не совпадает ни с чем — не ложное срабатывание


def test_resolve_by_bare_year_finds_all_matching_versions(tmp_path: Path):
    cache = ItsCache(tmp_path / "its.db")
    # Год есть прямо в «Обозначении».
    card_2021 = burondt.parse_card(_read("its_card.html"), url_id=1640)
    cache.upsert_card(card_2021)
    # Год есть только в дате приказа — «Обозначение» без года (реальный случай, UrlId=1150).
    card_2017 = burondt.ItsCard(
        url_id=1150,
        designation="ИТС НДТ 47",
        title="ИТС НДТ 47",
        files=[
            burondt.ItsCardFile(
                file_id=2476,
                caption="Приказ №2846 от 15.12.2017 об утверждении ИТС 47-2017",
                role="order",
            ),
        ],
    )
    cache.upsert_card(card_2017)
    # Другая версия того же номера, но другого года — не должна попасть в выдачу по 2017/2021.
    card_2023 = burondt.ItsCard(
        url_id=2100,
        designation="ИТС 47-2023",
        title="ИТС 47-2023",
        files=[
            burondt.ItsCardFile(
                file_id=2982,
                caption="Приказ 21 декабря 2023 г. № 2759 об утверждении ИТС 47-2023",
                role="order",
            ),
        ],
    )
    cache.upsert_card(card_2023)

    assert cache.resolve("2021") == [1640]
    assert cache.resolve("2017") == [1150]
    assert cache.resolve("2023") == [2100]
    assert cache.resolve("1999") == []

    # Одновременно проверяем resolve_by_number: тот же кэш, номер «47» встречается
    # у двух версий (2017 без года в «Обозначении» и 2023 с годом) — новая должна
    # идти первой, «ИТС 28-2021» (другой номер) в выдачу не попадает.
    assert cache.resolve("ИТС 47") == [2100, 1150]
    assert cache.resolve("итс 47") == [2100, 1150]  # регистронезависимо


def test_resolve_by_number_does_not_match_similar_longer_number(tmp_path: Path):
    # «53» как подстрока входит и в «530» — резолвинг по номеру должен сравнивать
    # точно, а не искать подстроку, иначе «ИТС 53» находил бы и «ИТС 530».
    cache = ItsCache(tmp_path / "its.db")
    card_53 = burondt.ItsCard(
        url_id=10,
        designation="ИТС 53-2020",
        title="ИТС 53-2020",
        files=[],
    )
    card_530 = burondt.ItsCard(
        url_id=11,
        designation="ИТС 530-2019",
        title="ИТС 530-2019",
        files=[],
    )
    cache.upsert_card(card_53)
    cache.upsert_card(card_530)

    assert cache.resolve("ИТС 53") == [10]
    assert cache.resolve("ИТС 530") == [11]


def test_record_its_file_requires_existing_document(tmp_path: Path):
    cache = ItsCache(tmp_path / "its.db")
    with pytest.raises(ValueError):
        cache.record_file(file_id=9999, file_path="x.pdf", sha256="a", size_bytes=1, pages=1)


def test_refresh_incremental_vs_full_rescan(tmp_path: Path):
    pages = {
        10: _read("its_card.html"),
        11: _read("non_its_card.html"),
        12: _read("empty_card.html"),
        13: _read("empty_card.html"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url_id = int(request.url.params["UrlId"])
        return httpx.Response(200, text=pages.get(url_id, _read("empty_card.html")))

    cache = ItsCache(tmp_path / "its.db")
    with create_client(base_url=burondt.BURONDT_BASE_URL, transport=httpx.MockTransport(handler)) as client:
        stats = cache.refresh(client, full_rescan=True, confirm_empty_run=2, pause_seconds=0)

    assert stats["lo"] == 10
    assert stats["its_found"] == 1
    assert stats["non_its_skipped"] == 1
    assert stats["truncated"] is False
    assert cache.resolve("ИТС 28-2021") == [10]
    # Полный прогон дошёл до конца (truncated=False) — граница подтверждена.
    assert cache.range_upper_bound_confirmed() == stats["hi"]

    # инкрементальный обход стартует near сохранённой границы, не с 10
    with create_client(base_url=burondt.BURONDT_BASE_URL, transport=httpx.MockTransport(handler)) as client:
        stats2 = cache.refresh(client, full_rescan=False, confirm_empty_run=2, pause_seconds=0)
    assert stats2["lo"] != 10 or stats2["lo"] == max(10, stats["hi"] - 20)


def test_refresh_truncated_by_max_requests(tmp_path: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_read("its_card.html"))

    cache = ItsCache(tmp_path / "its.db")
    with create_client(base_url=burondt.BURONDT_BASE_URL, transport=httpx.MockTransport(handler)) as client:
        stats = cache.refresh(
            client, full_rescan=True, max_requests=3, confirm_empty_run=40, pause_seconds=0
        )

    assert stats["truncated"] is True
    assert stats["scanned"] == 3
    # Усечённый лимитом запросов прогон границу НЕ подтверждает — иначе прогресс-бар
    # в UI принял бы «докуда успели» за «где реально кончается диапазон».
    assert cache.range_upper_bound_confirmed() is None
    assert cache.range_lower_bound() == 10
