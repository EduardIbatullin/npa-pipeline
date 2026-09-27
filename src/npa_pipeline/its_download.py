"""Скачивание и проверка файлов ИТС/НДТ. Переиспользует download.verify_pdf и
атомарную запись (.part → replace), но со своим ключом (file_id, не eo_number) и
своими таблицами (its_files/its_document_files) — карточка ИТС структурно
несёт 1:N файлов, а не 1:1, как documents/document_files."""

from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path

import httpx

from npa_pipeline import burondt
from npa_pipeline.download import IntegrityError, unique_path, verify_pdf
from npa_pipeline.http_client import NetworkError
from npa_pipeline.its import ItsCache
from npa_pipeline.models import ItsFileResult, ItsResult, Status

_WIN_FORBIDDEN = '<>:"/\\|?*'
_SPACES = re.compile(r"\s+")

_ROLE_LABEL = {"document": "справочник", "order": "приказ", "unknown": "файл"}


def _sanitize_filename(text: str) -> str:
    text = _SPACES.sub(" ", text).strip()
    for ch in _WIN_FORBIDDEN:
        text = text.replace(ch, "_")
    return text.rstrip(" .")


def already_downloaded_its(cache: ItsCache, file_id: int) -> Path | None:
    """Если файл на месте и sha256 совпадает с БД — возвращает путь (аналог already_downloaded)."""
    rec = cache.get_file_record(file_id)
    if rec is None:
        return None
    path = Path(rec["file_path"])
    if not path.is_file():
        return None
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest == rec["sha256"]:
            return path
    except OSError:
        return None
    return None


def save_its_file(
    out_dir: Path,
    *,
    data: bytes,
    designation: str,
    file_id: int,
    role: str,
    pages: int,
    cache: ItsCache,
) -> Path:
    """Атомарная запись (.part → replace), sha256, регистрация в its_document_files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir = out_dir.resolve()

    stem = _sanitize_filename(f"{designation} — {_ROLE_LABEL.get(role, role)}")
    path = unique_path(out_dir, stem, ".pdf")

    sha256 = hashlib.sha256(data).hexdigest()
    with tempfile.NamedTemporaryFile(dir=out_dir, suffix=".part", delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)

    cache.record_file(
        file_id=file_id,
        file_path=str(path.resolve()),
        sha256=sha256,
        size_bytes=len(data),
        pages=pages,
    )
    return path


def fetch_its(
    client: httpx.Client,
    *,
    designation: str,
    out_dir: Path,
    cache: ItsCache,
    download: bool = True,
) -> ItsResult:
    """Резолвит designation → url_id, скачивает и проверяет файлы карточки.

    Никогда не бросает — сбои превращаются в ItsResult.status (переиспользует
    общий Status: FOUND/NOT_FOUND/AMBIGUOUS/NETWORK_ERROR/INTEGRITY_ERROR).

    При AMBIGUOUS в каждый кандидат добавляется его список файлов (files) — чтобы
    в UI можно было показать «справочник + приказ» и ссылку на скачивание конкретной
    версии без ещё одного резолвинга по обозначению (см. fetch_its_by_url_id) — важно,
    если у двух версий совпадает выводимое обозначение (например, обеим разрешат
    показаться по общему номеру или году).
    """
    if not designation or not designation.strip():
        return ItsResult(status=Status.INVALID_INPUT, designation=designation, message="Пустое обозначение")

    candidates = cache.resolve(designation)
    if not candidates:
        return ItsResult(status=Status.NOT_FOUND, designation=designation)
    if len(candidates) > 1:
        cand_cards = []
        for u in candidates:
            card = cache.get_card(u)
            if card is None:
                continue
            card = dict(card)
            card["files"] = cache.list_files(u)
            cand_cards.append(card)
        return ItsResult(status=Status.AMBIGUOUS, designation=designation, candidates=cand_cards)

    return fetch_its_by_url_id(
        client,
        url_id=candidates[0],
        out_dir=out_dir,
        cache=cache,
        download=download,
        designation=designation,
    )


def fetch_its_by_url_id(
    client: httpx.Client,
    *,
    url_id: int,
    out_dir: Path,
    cache: ItsCache,
    download: bool = True,
    designation: str | None = None,
) -> ItsResult:
    """Как fetch_its, но по уже известному url_id — без повторного резолвинга.

    Нужен для UI: когда resolve() вернул несколько версий (AMBIGUOUS), пользователь
    выбирает одну по её url_id, а не вводит обозначение заново (обозначение у версий
    может совпадать или быть неполным — см. resolve).
    """
    card_row = cache.get_card(url_id)
    if card_row is None:
        return ItsResult(status=Status.NOT_FOUND, designation=designation or str(url_id))
    designation = designation or card_row["designation"]

    if not download:
        return ItsResult(status=Status.FOUND, designation=designation, url_id=url_id)

    files_meta = cache.list_files(url_id)
    results: list[ItsFileResult] = []
    to_download: list[dict] = []

    for f in files_meta:
        existing = already_downloaded_its(cache, f["file_id"])
        if existing is not None:
            results.append(
                ItsFileResult(
                    role=f["role"],
                    file_id=f["file_id"],
                    pdf_path=str(existing),
                    order_number_caption=f.get("order_number_caption"),
                    order_date_caption=f.get("order_date_caption"),
                )
            )
        else:
            to_download.append(f)

    downloaded: list[tuple[dict, bytes]] = []
    for f in to_download:
        try:
            data, _content_length, _filename = burondt.download_file(client, f["file_id"])
        except NetworkError as exc:
            return ItsResult(
                status=Status.NETWORK_ERROR,
                designation=designation,
                url_id=url_id,
                message=str(exc),
            )
        downloaded.append((f, data))

    for f, data in downloaded:
        try:
            pages = verify_pdf(data, content_length=None)
        except IntegrityError as exc:
            return ItsResult(
                status=Status.INTEGRITY_ERROR,
                designation=designation,
                url_id=url_id,
                message=str(exc),
            )
        path = save_its_file(
            out_dir,
            data=data,
            designation=card_row["designation"],
            file_id=f["file_id"],
            role=f["role"],
            pages=pages,
            cache=cache,
        )
        results.append(
            ItsFileResult(
                role=f["role"],
                file_id=f["file_id"],
                pdf_path=str(path),
                pages=pages,
                order_number_caption=f.get("order_number_caption"),
                order_date_caption=f.get("order_date_caption"),
            )
        )

    return ItsResult(status=Status.FOUND, designation=designation, url_id=url_id, files=results)
