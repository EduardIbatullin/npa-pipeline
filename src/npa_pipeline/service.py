"""Публичная точка входа библиотеки."""

from __future__ import annotations

from pathlib import Path

import httpx

from npa_pipeline import api as pravo_api
from npa_pipeline.authorities import AuthorityCache
from npa_pipeline.db import DEFAULT_DB_PATH, DocumentStore
from npa_pipeline.download import IntegrityError, download_document
from npa_pipeline.http_client import NetworkError, create_client
from npa_pipeline.models import DocItem, Query, Result, Status
from npa_pipeline.search import search_document


def _persist_search_hits(
    client: httpx.Client,
    store: DocumentStore,
    result: Result,
) -> Result:
    """Пишет found / ambiguous в БД (полная карточка). Обновляет DocItem из карточки."""
    docs: list[DocItem] = []
    if result.status == Status.FOUND and result.document is not None:
        docs = [result.document]
    elif result.status == Status.AMBIGUOUS and result.candidates:
        docs = list(result.candidates)
    else:
        return result

    enriched: list[DocItem] = []
    for doc in docs:
        try:
            card = pravo_api.get_document_card(client, doc.eo_number)
            store.upsert_from_card(card)
            enriched.append(pravo_api.doc_item_from_card(card))
        except Exception:
            store.upsert_from_doc_item(doc)
            enriched.append(doc)

    if result.status == Status.FOUND:
        result.document = enriched[0]
        result.eo_number = enriched[0].eo_number
    else:
        result.candidates = enriched
    return result


def fetch_document(
    query: Query,
    *,
    client: httpx.Client | None = None,
    cache: AuthorityCache | None = None,
    store: DocumentStore | None = None,
    cache_path: str | Path = DEFAULT_DB_PATH,
    db_path: str | Path | None = None,
    out_dir: str | Path = "downloads",
    download: bool = True,
) -> Result:
    """Ищет документ, сохраняет в БД и при успехе скачивает PDF.

    Все сетевые/целостностные исключения превращаются в статусы контракта.
    """
    owns_client = client is None
    path = Path(db_path) if db_path is not None else Path(cache_path)
    if client is None:
        client = create_client()
    if cache is None:
        cache = AuthorityCache(path)
    if store is None:
        store = DocumentStore(path)

    try:
        result = search_document(client, query, cache)
        if result.status in (Status.FOUND, Status.AMBIGUOUS):
            result = _persist_search_hits(client, store, result)

        if result.status != Status.FOUND or not download:
            return result

        assert result.document is not None and result.eo_number is not None
        try:
            file_path = download_document(
                client,
                eo_number=result.eo_number,
                query=query,
                document=result.document,
                out_dir=Path(out_dir),
                store=store,
            )
            result.pdf_path = str(file_path)
            return result
        except IntegrityError as exc:
            return Result(
                status=Status.INTEGRITY_ERROR,
                eo_number=result.eo_number,
                document=result.document,
                match_type=result.match_type,
                message=str(exc),
            )
        except NetworkError as exc:
            return Result(
                status=Status.NETWORK_ERROR,
                eo_number=result.eo_number,
                document=result.document,
                match_type=result.match_type,
                message=str(exc),
            )
    except NetworkError as exc:
        return Result(status=Status.NETWORK_ERROR, message=str(exc))
    except Exception as exc:
        return Result(status=Status.NETWORK_ERROR, message=f"Неожиданная ошибка: {exc}")
    finally:
        if owns_client:
            client.close()
