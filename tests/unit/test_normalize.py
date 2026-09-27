"""Юнит-тесты нормализации номера и названия."""

from npa_pipeline.normalize import (
    digit_prefix,
    matches_digit_boundary,
    normalize_name,
    normalize_number,
    numbers_equal,
)


def test_normalize_number_dashes_and_no():
    assert normalize_number("№ 310–ФЗ") == "310-ФЗ"
    assert normalize_number("310—ФЗ") == "310-ФЗ"
    assert normalize_number(" 402 ") == "402"


def test_numbers_equal_casefold():
    assert numbers_equal("310-ФЗ", "310-фз")
    assert numbers_equal("310–ФЗ", "310-ФЗ")
    assert not numbers_equal("310-ФЗ", "3100-ФЗ")


def test_digit_prefix():
    assert digit_prefix("310-ФЗ") == "310"
    assert digit_prefix("402") == "402"
    assert digit_prefix("П-123") == ""
    assert digit_prefix("ММВ-7-3/123@") == ""


def test_matches_digit_boundary():
    assert matches_digit_boundary("310-ФЗ", "310")
    assert matches_digit_boundary("310", "310")
    assert matches_digit_boundary("310/04", "310")
    assert not matches_digit_boundary("3100-ФЗ", "310")
    assert not matches_digit_boundary("3101", "310")


def test_normalize_name():
    assert normalize_name('Министерство "Финансов"') == "министерство финансов"
    assert "е" in normalize_name("Ёжик")
    assert normalize_name("Минфин") == "министерство финансов"
