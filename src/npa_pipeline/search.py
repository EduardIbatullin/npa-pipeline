"""Алгоритм поиска документа (шаги 1–12 плана этапа 1)."""

from __future__ import annotations

from datetime import date

import httpx

from npa_pipeline import api as pravo_api
from npa_pipeline.authorities import AuthorityCache
from npa_pipeline.models import (
    COVERAGE_START,
    DiagnosticHit,
    DocItem,
    MatchType,
    Query,
    Result,
    Status,
)
from npa_pipeline.normalize import (
    digit_prefix,
    matches_digit_boundary,
    normalize_number,
    numbers_equal,
)


def validate_query(query: Query) -> str | None:
    """Возвращает текст ошибки или None, если вход корректен."""
    if query.eo_number:
        return None
    if not query.number or not str(query.number).strip():
        return "Не указан номер документа"
    if query.date is None:
        return "Не указана дата подписания"
    if not query.authority_guid and not query.authority_name:
        return "Не указан орган (GUID или название)"
    return None


def apply_filters(
    items: list[DocItem],
    *,
    target_date: date,
    candidates: list[str],
    filter_by_authority: bool,
    number_check: str | None = None,
    digit_boundary_prefix: str | None = None,
    doc_type_id: str | None = None,
) -> list[DocItem]:
    """Фильтрация по дате, номеру, органу, типу + дедупликация по eoNumber."""
    result: list[DocItem] = []
    seen: set[str] = set()
    candidate_set = set(candidates)

    for item in items:
        if item.document_date != target_date:
            continue
        if number_check is not None and not numbers_equal(item.number, number_check):
            continue
        if digit_boundary_prefix is not None and not matches_digit_boundary(
            item.number, digit_boundary_prefix
        ):
            continue
        if filter_by_authority and candidates:
            if not set(item.signatory_ids) & candidate_set:
                continue
        if doc_type_id and item.document_type_id and item.document_type_id != doc_type_id:
            continue
        if item.eo_number in seen:
            continue
        seen.add(item.eo_number)
        result.append(item)
    return result


def decide(
    matched: list[DocItem],
    *,
    match_type: MatchType,
    query_date: date,
    diagnostics: list[DiagnosticHit] | None = None,
) -> Result:
    """Выбор статуса по числу совпадений (шаги 9–10)."""
    if len(matched) == 1:
        return Result(
            status=Status.FOUND,
            match_type=match_type,
            document=matched[0],
            eo_number=matched[0].eo_number,
        )
    if len(matched) > 1:
        return Result(
            status=Status.AMBIGUOUS,
            match_type=match_type,
            candidates=matched,
            message=f"Найдено {len(matched)} документов",
        )
    if query_date < COVERAGE_START:
        return Result(
            status=Status.OUT_OF_RANGE,
            message=(
                f"Дата подписания {query_date.isoformat()} раньше границы охвата "
                f"{COVERAGE_START.isoformat()}"
            ),
            diagnostics=diagnostics or [],
        )
    return Result(
        status=Status.NOT_FOUND,
        diagnostics=diagnostics or [],
        message="Документ не найден среди кандидатов органа",
    )


def _diag_from_items(items: list[DocItem], candidates: list[str]) -> list[DiagnosticHit]:
    candidate_set = set(candidates)
    hits: list[DiagnosticHit] = []
    seen: set[str] = set()
    for item in items:
        if item.eo_number in seen:
            continue
        if candidate_set and set(item.signatory_ids) & candidate_set:
            continue
        seen.add(item.eo_number)
        hits.append(
            DiagnosticHit(
                eo_number=item.eo_number,
                authority_id=item.signatory_ids[0] if item.signatory_ids else None,
                number=item.number,
                document_date=item.document_date,
            )
        )
    return hits


def _filter_by_date_and_number(
    items: list[DocItem],
    *,
    target_date: date,
    number: str,
    prefix: str,
) -> list[DocItem]:
    """Для диагностики: дата + точный номер или граница цифровой части."""
    out: list[DocItem] = []
    seen: set[str] = set()
    for item in items:
        if item.document_date != target_date:
            continue
        ok = numbers_equal(item.number, number) or (
            bool(prefix) and matches_digit_boundary(item.number, prefix)
        )
        if not ok or item.eo_number in seen:
            continue
        seen.add(item.eo_number)
        out.append(item)
    return out


def search_document(
    client: httpx.Client,
    query: Query,
    cache: AuthorityCache,
) -> Result:
    """Полный алгоритм поиска без скачивания PDF."""
    err = validate_query(query)
    if err:
        return Result(status=Status.INVALID_INPUT, message=err)

    # Шаг 1: известен eoNumber
    if query.eo_number:
        doc = pravo_api.get_document(client, query.eo_number)
        return Result(
            status=Status.FOUND,
            match_type=MatchType.EXACT,
            document=doc,
            eo_number=doc.eo_number,
        )

    assert query.number is not None and query.date is not None

    # Шаг 2: резолвинг
    candidates = cache.resolve(
        authority_guid=query.authority_guid,
        authority_name=query.authority_name,
    )
    number_for_request = normalize_number(query.number)
    prefix = digit_prefix(query.number)
    single = len(candidates) == 1

    # Шаги 4–5: точный запрос
    raw_exact = pravo_api.search_documents(
        client,
        number=number_for_request,
        search_type=0,
        authority_id=candidates[0] if single else None,
    )

    if single:
        # Сервер уже отфильтровал по органу; не сужаем по основному GUID из списка
        matched = apply_filters(
            raw_exact,
            target_date=query.date,
            candidates=candidates,
            filter_by_authority=False,
            number_check=query.number,
            doc_type_id=query.doc_type_id,
        )
    else:
        date_only = apply_filters(
            raw_exact,
            target_date=query.date,
            candidates=candidates,
            filter_by_authority=False,
            number_check=query.number,
        )
        if date_only and candidates:
            date_only = pravo_api.enrich_signatories(client, date_only)
        matched = apply_filters(
            date_only,
            target_date=query.date,
            candidates=candidates,
            filter_by_authority=bool(candidates),
            number_check=query.number,
            doc_type_id=query.doc_type_id,
        )

    if matched:
        return decide(matched, match_type=MatchType.EXACT, query_date=query.date)

    # Шаг 8: запасной поиск по цифровой части
    fallback_raw: list[DocItem] = []
    fallback_ran = False
    if prefix and (single or len(prefix) >= 3):
        fallback_raw = pravo_api.search_documents(
            client,
            number=prefix,
            search_type=1,
            authority_id=candidates[0] if single else None,
        )
        fallback_ran = True

        if single:
            matched = apply_filters(
                fallback_raw,
                target_date=query.date,
                candidates=candidates,
                filter_by_authority=False,
                digit_boundary_prefix=prefix,
                doc_type_id=query.doc_type_id,
            )
        else:
            date_fb = apply_filters(
                fallback_raw,
                target_date=query.date,
                candidates=candidates,
                filter_by_authority=False,
                digit_boundary_prefix=prefix,
            )
            if date_fb and candidates:
                date_fb = pravo_api.enrich_signatories(client, date_fb)
            matched = apply_filters(
                date_fb,
                target_date=query.date,
                candidates=candidates,
                filter_by_authority=bool(candidates),
                digit_boundary_prefix=prefix,
                doc_type_id=query.doc_type_id,
            )

        if matched:
            return decide(matched, match_type=MatchType.DIGITS_ONLY, query_date=query.date)

    # Шаг 11: диагностика (сбой сети здесь не должен подменять not_found/out_of_range)
    diagnostics: list[DiagnosticHit] = []
    if query.authority_name or query.authority_guid:
        try:
            if not single:
                source = fallback_raw if fallback_ran else raw_exact
                by_date = _filter_by_date_and_number(
                    source, target_date=query.date, number=query.number, prefix=prefix
                )
                if by_date:
                    try:
                        by_date = pravo_api.enrich_signatories(client, by_date)
                    except Exception:
                        pass
                diagnostics = _diag_from_items(by_date, candidates)
            else:
                diag_raw = pravo_api.search_documents(
                    client,
                    number=number_for_request,
                    search_type=0,
                    authority_id=None,
                )
                by_date = apply_filters(
                    diag_raw,
                    target_date=query.date,
                    candidates=[],
                    filter_by_authority=False,
                    number_check=query.number,
                )
                diagnostics = _diag_from_items(by_date, candidates)
        except Exception:
            diagnostics = []

    return decide(
        [],
        match_type=MatchType.EXACT,
        query_date=query.date,
        diagnostics=diagnostics,
    )
