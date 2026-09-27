"""Юнит-тесты фильтров и дедупликации поиска."""

from datetime import date

from npa_pipeline.models import DocItem, MatchType, Query, Status
from npa_pipeline.search import apply_filters, decide, validate_query


def _doc(eo: str, number: str, d: str, *sids: str) -> DocItem:
    return DocItem(
        eo_number=eo,
        number=number,
        document_date=date.fromisoformat(d),
        signatory_ids=list(sids),
    )


def test_validate_query_invalid():
    assert validate_query(Query()) is not None
    assert validate_query(Query(number="1")) is not None
    assert validate_query(Query(number="1", date=date(2020, 1, 1))) is not None
    assert (
        validate_query(
            Query(number="1", date=date(2020, 1, 1), authority_name="X")
        )
        is None
    )
    assert validate_query(Query(eo_number="0001")) is None


def test_dedupe_joint_document():
    """Один eoNumber через двух подписантов не даёт ambiguous."""
    a = "guid-a"
    b = "guid-b"
    items = [
        _doc("0001202306010022", "247/04", "2023-04-25", a),
        _doc("0001202306010022", "247/04", "2023-04-25", b),
    ]
    matched = apply_filters(
        items,
        target_date=date(2023, 4, 25),
        candidates=[a, b],
        filter_by_authority=True,
        number_check="247/04",
    )
    assert len(matched) == 1
    assert matched[0].eo_number == "0001202306010022"


def test_collision_402_by_date():
    items = [
        _doc("0001201609130025", "402", "2016-07-19", "mp"),
        _doc("0001202609240005", "402", "2026-07-20", "mp"),
    ]
    m2016 = apply_filters(
        items,
        target_date=date(2016, 7, 19),
        candidates=["mp"],
        filter_by_authority=True,
        number_check="402",
    )
    assert [x.eo_number for x in m2016] == ["0001201609130025"]


def test_annual_310_fz():
    items = [
        _doc(f"eo{y}", "310-ФЗ", f"{y}-07-31", "president")
        for y in range(2017, 2027)
    ]
    # даты у законов разные; фильтр по одной дате оставляет максимум один год
    matched = apply_filters(
        items,
        target_date=date(2025, 7, 31),
        candidates=["president"],
        filter_by_authority=True,
        number_check="310-фз",
    )
    assert len(matched) == 1
    assert matched[0].eo_number == "eo2025"


def test_digit_boundary_excludes_3100():
    items = [
        _doc("a", "310-ФЗ", "2025-07-31", "p"),
        _doc("b", "3100-П", "2025-07-31", "p"),
    ]
    matched = apply_filters(
        items,
        target_date=date(2025, 7, 31),
        candidates=["p"],
        filter_by_authority=True,
        digit_boundary_prefix="310",
    )
    assert [x.eo_number for x in matched] == ["a"]


def test_decide_out_of_range():
    r = decide([], match_type=MatchType.EXACT, query_date=date(2010, 1, 1))
    assert r.status == Status.OUT_OF_RANGE


def test_decide_not_found():
    r = decide([], match_type=MatchType.EXACT, query_date=date(2020, 1, 1))
    assert r.status == Status.NOT_FOUND


def test_decide_ambiguous():
    items = [
        _doc("a", "1", "2020-01-01", "x"),
        _doc("b", "1", "2020-01-01", "y"),
    ]
    r = decide(items, match_type=MatchType.EXACT, query_date=date(2020, 1, 1))
    assert r.status == Status.AMBIGUOUS
    assert len(r.candidates) == 2
