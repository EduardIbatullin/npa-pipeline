"""Имена файлов для скачанных PDF."""

from __future__ import annotations

import re

from npa_pipeline.models import DocItem

_WIN_FORBIDDEN = '<>:"/\\|?*'
_DATE_DOTS = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_SPACES = re.compile(r"\s+")


def human_filename_stem(document: DocItem) -> str:
    """Строит имя вида «Постановление Правительства РФ от 31_05_2019 N 691».

    Берёт первую строку complexName (официальная формулировка с падежами),
    меняет дату на DD_MM_YYYY и «№» на «N», убирает недопустимые для ФС символы.
    """
    raw = (document.complex_name or "").split("\n", 1)[0].strip()
    if '"' in raw:
        raw = raw.split('"', 1)[0].strip()
    if "<br" in raw.lower():
        raw = re.split(r"<br\s*/?>", raw, flags=re.IGNORECASE)[0].strip()

    if not raw:
        d = document.document_date.strftime("%d_%m_%Y")
        num = document.number or document.eo_number
        raw = f"Документ от {d} N {num}"

    raw = raw.replace("№", "N").replace("Nº", "N")
    raw = _DATE_DOTS.sub(r"\1_\2_\3", raw)
    raw = _SPACES.sub(" ", raw).strip()

    for ch in _WIN_FORBIDDEN:
        raw = raw.replace(ch, "_")
    raw = raw.rstrip(" .")

    # запас на лимит пути Windows
    if len(raw) > 180:
        raw = raw[:180].rstrip(" .")

    return raw or document.eo_number
