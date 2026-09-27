"""Скачивание PDF, проверка целостности, учёт файлов в БД."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import httpx
from pypdf import PdfReader

from npa_pipeline import api as pravo_api
from npa_pipeline.db import DocumentStore
from npa_pipeline.models import DocItem, Query
from npa_pipeline.naming import human_filename_stem


class IntegrityError(Exception):
    """Файл не прошёл проверку целостности."""


def verify_pdf(data: bytes, *, content_length: int | None) -> int:
    """Проверяет PDF; возвращает число страниц. Бросает IntegrityError при сбое."""
    if content_length is not None and len(data) != content_length:
        raise IntegrityError(
            f"Размер {len(data)} не совпадает с Content-Length {content_length}"
        )
    if not data.startswith(b"%PDF"):
        raise IntegrityError("Нет сигнатуры %PDF в начале файла")
    tail = data[-1024:] if len(data) >= 1024 else data
    if b"%%EOF" not in tail:
        raise IntegrityError("Нет маркера %%EOF в конце файла")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    try:
        reader = PdfReader(str(tmp_path))
        pages = len(reader.pages)
        if pages < 1:
            raise IntegrityError("PDF не содержит страниц")
        return pages
    except IntegrityError:
        raise
    except Exception as exc:
        raise IntegrityError(f"pypdf не смог открыть файл: {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)


def unique_path(out_dir: Path, stem: str, suffix: str) -> Path:
    """Если файл с таким именем есть — добавляет _2, _3, …"""
    candidate = out_dir / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    n = 2
    while True:
        candidate = out_dir / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def find_pdf_by_eo(store: DocumentStore, eo_number: str) -> Path | None:
    """Путь к PDF по записи в БД, если файл ещё на диске."""
    rec = store.get_file(eo_number)
    if rec is None:
        return None
    path = Path(rec.file_path)
    return path if path.is_file() else None


def already_downloaded(store: DocumentStore, eo_number: str) -> Path | None:
    """Если PDF на месте и sha256 совпадает с БД — возвращает путь."""
    rec = store.get_file(eo_number)
    if rec is None:
        return None
    path = Path(rec.file_path)
    if not path.is_file():
        return None
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest == rec.sha256:
            return path
    except OSError:
        return None
    return None


def save_download(
    out_dir: Path,
    *,
    data: bytes,
    eo_number: str,
    document: DocItem,
    pages: int,
    store: DocumentStore,
) -> Path:
    """Пишет PDF и регистрирует файл в БД (без JSON-sidecar)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir = out_dir.resolve()

    existing = store.get_file(eo_number)
    if existing:
        prev = Path(existing.file_path)
        if prev.parent.resolve() == out_dir and prev.suffix.lower() == ".pdf":
            pdf = prev
        else:
            pdf = unique_path(out_dir, human_filename_stem(document), ".pdf")
    else:
        pdf = unique_path(out_dir, human_filename_stem(document), ".pdf")

    sha256 = hashlib.sha256(data).hexdigest()
    with tempfile.NamedTemporaryFile(dir=out_dir, suffix=".part", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    tmp_path.replace(pdf)

    store.record_file(
        eo_number=eo_number,
        file_path=pdf.resolve(),
        sha256=sha256,
        size_bytes=len(data),
        pages=pages,
    )
    return pdf


def download_document(
    client: httpx.Client,
    *,
    eo_number: str,
    query: Query,
    document: DocItem,
    out_dir: Path,
    store: DocumentStore,
) -> Path:
    """Скачивает и проверяет PDF. Бросает IntegrityError или NetworkError."""
    del query  # реквизиты уже в document / БД
    existing = already_downloaded(store, eo_number)
    if existing is not None:
        return existing

    try:
        card = pravo_api.get_document_card(client, eo_number)
        store.upsert_from_card(card)
        document = pravo_api.doc_item_from_card(card)
    except Exception:
        store.upsert_from_doc_item(document)

    data, content_length = pravo_api.download_pdf(client, eo_number)
    pages = verify_pdf(data, content_length=content_length)
    return save_download(
        out_dir,
        data=data,
        eo_number=eo_number,
        document=document,
        pages=pages,
        store=store,
    )
