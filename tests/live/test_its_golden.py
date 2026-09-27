"""Live точечная проверка ИТС/НДТ против burondt.ru — не весь диапазон (это ~2550
последовательных запросов, не для CI), только уже известные реальные карточки, чтобы
ловить регрессии в парсинге настоящего HTML сайта, а не только фикстур."""

from __future__ import annotations

import time

import pytest

from npa_pipeline import burondt
from npa_pipeline.http_client import BURONDT_PAGE_PAUSE_SECONDS

KNOWN_ITS = [
    (1640, "ИТС 28-2021"),
    (1150, "ИТС НДТ 47"),  # реальное поле «Обозначение» этой карточки — без года (не "ИТС 47-2017")
    (1850, "ИТС 8-2022"),
    (2100, "ИТС 47-2023"),
]
KNOWN_EMPTY = [5, 2650]


@pytest.mark.live
def test_known_its_cards_parse_correctly():
    with burondt.create_burondt_client() as client:
        for i, (url_id, designation) in enumerate(KNOWN_ITS):
            if i:
                time.sleep(BURONDT_PAGE_PAUSE_SECONDS)
            html = burondt.fetch_card_html(client, url_id)
            card = burondt.parse_card(html, url_id)
            assert card is not None, f"UrlId={url_id} должен парситься"
            assert card.designation == designation
            assert burondt.is_its(card)
            assert len(card.files) >= 2, f"UrlId={url_id}: ожидалось минимум 2 файла"


@pytest.mark.live
def test_known_empty_ids_are_zero_false_positives():
    with burondt.create_burondt_client() as client:
        for i, url_id in enumerate(KNOWN_EMPTY):
            if i:
                time.sleep(BURONDT_PAGE_PAUSE_SECONDS)
            html = burondt.fetch_card_html(client, url_id)
            card = burondt.parse_card(html, url_id)
            assert card is None, f"UrlId={url_id} должен быть пустым слотом (ложное 'найдено' опаснее)"
