"""Небольшой веб-интерфейс для проверки поиска и скачивания НПА."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query as FastQuery
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from npa_pipeline import burondt
from npa_pipeline.authorities import AuthorityCache
from npa_pipeline.db import DEFAULT_DB_PATH, DocumentStore
from npa_pipeline.download import find_pdf_by_eo
from npa_pipeline.http_client import create_client
from npa_pipeline.its import ItsCache
from npa_pipeline.its_download import fetch_its, fetch_its_by_url_id
from npa_pipeline.models import Query, parse_user_date
from npa_pipeline.service import fetch_document

ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_DB = ROOT / DEFAULT_DB_PATH
DEFAULT_OUT = ROOT / "downloads"

app = FastAPI(title="NPA Pipeline", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class FetchRequest(BaseModel):
    eo_number: str | None = None
    authority_guid: str | None = None
    authority_name: str | None = None
    number: str | None = None
    date: str | None = None
    download: bool = True


def _cache() -> AuthorityCache:
    return AuthorityCache(DEFAULT_DB)


def _store() -> DocumentStore:
    return DocumentStore(DEFAULT_DB)


def _its_cache() -> ItsCache:
    return ItsCache(DEFAULT_DB)


class FetchItsRequest(BaseModel):
    # designation — обычный поиск по обозначению/году/номеру (см. ItsCache.resolve).
    # url_id — точный выбор конкретной версии из уже показанного списка кандидатов
    # (AMBIGUOUS), без повторного резолвинга по тексту.
    designation: str | None = None
    url_id: int | None = None
    download: bool = True


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    cache = _cache()
    store = _store()
    its_cache = _its_cache()
    return {
        "ok": True,
        "authorities_cached": cache.count(),
        "documents": store.count_documents(),
        "files": store.count_files(),
        "its_cached": its_cache.count_its_documents(),
        "its_files": its_cache.count_its_files(),
        "its_range_lo": its_cache.range_lower_bound(),
        "its_range_hi_confirmed": its_cache.range_upper_bound_confirmed(),
        "db_path": str(DEFAULT_DB),
        "downloads_dir": str(DEFAULT_OUT),
    }


@app.post("/api/refresh-authorities")
def refresh_authorities() -> dict:
    cache = _cache()
    with create_client() as client:
        count = cache.build(client)
    return {"authorities": count, "db_path": str(DEFAULT_DB)}


@app.get("/api/resolve")
def resolve_authority(name: str = FastQuery(min_length=1)) -> dict:
    cache = _cache()
    guids = cache.resolve(authority_name=name)
    import sqlite3

    items: list[dict] = []
    if guids:
        with sqlite3.connect(cache.db_path) as conn:
            conn.row_factory = sqlite3.Row
            placeholders = ",".join("?" * len(guids))
            rows = conn.execute(
                f"SELECT guid, name FROM authorities WHERE guid IN ({placeholders})",
                guids,
            ).fetchall()
            by_id = {r["guid"]: r["name"] for r in rows}
        items = [{"guid": g, "name": by_id.get(g, g)} for g in guids]
    return {"count": len(items), "candidates": items}


@app.get("/api/authorities/suggest")
def suggest_authorities(
    q: str = FastQuery(min_length=1),
    limit: int = FastQuery(default=15, ge=1, le=50),
) -> dict:
    """Автодополнение названия органа из локального кэша."""
    cache = _cache()
    items = cache.suggest(q, limit=limit)
    return {"count": len(items), "items": items}


@app.post("/api/fetch")
def api_fetch(body: FetchRequest) -> dict:
    date_val = None
    if body.date and body.date.strip():
        try:
            date_val = parse_user_date(body.date.strip())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Некорректная дата: {exc}") from exc

    query = Query(
        eo_number=(body.eo_number or "").strip() or None,
        authority_guid=(body.authority_guid or "").strip() or None,
        authority_name=(body.authority_name or "").strip() or None,
        number=(body.number or "").strip() or None,
        date=date_val,
    )
    result = fetch_document(
        query,
        cache_path=DEFAULT_DB,
        out_dir=DEFAULT_OUT,
        download=body.download,
    )
    payload = result.to_dict()
    if result.pdf_path:
        eo = result.eo_number or ""
        payload["pdf_url"] = f"/api/pdf/{eo}" if eo else None
    return payload


@app.get("/api/pdf/{eo_number}")
def get_pdf(eo_number: str) -> FileResponse:
    path = find_pdf_by_eo(_store(), eo_number)
    if path is None:
        raise HTTPException(status_code=404, detail="PDF не найден на диске")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        content_disposition_type="inline",
    )


@app.post("/api/refresh-its")
def refresh_its(max_requests: int = FastQuery(default=300, ge=1, le=3000)) -> dict:
    """Ограниченное (по умолчанию 300 запросов) обновление кэша ИТС — кнопка в UI не
    должна блокировать HTTP-запрос на ~15+ минут, которые займёт полный обход.
    Полный/неограниченный прогон — только через CLI (npa refresh-its --full-rescan)."""
    cache = _its_cache()
    with burondt.create_burondt_client() as client:
        stats = cache.refresh(client, full_rescan=False, max_requests=max_requests)
    return {**stats, "db_path": str(DEFAULT_DB)}


@app.get("/api/its/search")
def search_its(designation: str = FastQuery(min_length=1)) -> dict:
    cache = _its_cache()
    url_ids = cache.resolve(designation)
    items = [cache.get_card(u) for u in url_ids]
    return {"count": len(items), "candidates": [i for i in items if i is not None]}


@app.post("/api/fetch-its")
def api_fetch_its(body: FetchItsRequest) -> dict:
    if body.url_id is None and not (body.designation and body.designation.strip()):
        raise HTTPException(status_code=400, detail="Укажите designation или url_id")

    cache = _its_cache()
    with burondt.create_burondt_client() as client:
        if body.url_id is not None:
            result = fetch_its_by_url_id(
                client,
                url_id=body.url_id,
                out_dir=DEFAULT_OUT / "its",
                cache=cache,
                download=body.download,
                designation=body.designation,
            )
        else:
            result = fetch_its(
                client,
                designation=body.designation.strip(),
                out_dir=DEFAULT_OUT / "its",
                cache=cache,
                download=body.download,
            )
    payload = result.to_dict()
    for f in payload.get("files", []):
        if f.get("pdf_path"):
            f["pdf_url"] = f"/api/its/pdf/{f['file_id']}"
    return payload


@app.get("/api/its/pdf/{file_id}")
def get_its_pdf(file_id: int) -> FileResponse:
    cache = _its_cache()
    rec = cache.get_file_record(file_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="PDF не найден на диске")
    path = Path(rec["file_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="PDF не найден на диске")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        content_disposition_type="inline",
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        "npa_pipeline.webapp:app",
        host="127.0.0.1",
        port=8765,
        reload=False,
    )


if __name__ == "__main__":
    main()
