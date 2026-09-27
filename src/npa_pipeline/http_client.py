"""HTTP-клиент к publication.pravo.gov.ru: retry/backoff для JSON и скачивания PDF."""

from __future__ import annotations

import time
from typing import Any

import httpx

BASE_URL = "http://publication.pravo.gov.ru"

# JSON-запросы короткие; PDF может быть 30+ МБ
JSON_TIMEOUT = httpx.Timeout(30.0, connect=15.0)
PDF_TIMEOUT = httpx.Timeout(180.0, connect=15.0)

RETRY_STATUSES = {500, 502, 503, 504}
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0)
PAGE_PAUSE_SECONDS = 0.35


class NetworkError(Exception):
    """Сбой сети/сервера после исчерпания retry."""

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


def create_client(*, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """Создаёт синхронный httpx.Client (можно подменить transport для тестов)."""
    kwargs: dict[str, Any] = {
        "base_url": BASE_URL,
        "timeout": JSON_TIMEOUT,
        "follow_redirects": True,
        "headers": {"Accept": "text/html, application/json, */*"},
    }
    if transport is not None:
        kwargs["transport"] = transport
    return httpx.Client(**kwargs)


def request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> Any:
    """GET/POST JSON с retry на обрывы и 5xx."""
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.request(method, path, params=params, timeout=JSON_TIMEOUT)
            if response.status_code in RETRY_STATUSES:
                raise httpx.HTTPStatusError(
                    f"HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
            response.raise_for_status()
            return response.json()
        except (httpx.TransportError, httpx.HTTPStatusError, httpx.TimeoutException) as exc:
            last_exc = exc
            if attempt + 1 >= MAX_ATTEMPTS:
                break
            time.sleep(BACKOFF_SECONDS[attempt])
    raise NetworkError(f"Запрос {method} {path} не удался после {MAX_ATTEMPTS} попыток", cause=last_exc)


def download_bytes(
    client: httpx.Client,
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> tuple[bytes, int | None]:
    """Скачивает тело ответа потоком; возвращает (данные, Content-Length или None)."""
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            with client.stream("GET", path, params=params, timeout=PDF_TIMEOUT) as response:
                if response.status_code in RETRY_STATUSES:
                    raise httpx.HTTPStatusError(
                        f"HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                content_length = None
                cl = response.headers.get("Content-Length")
                if cl and cl.isdigit():
                    content_length = int(cl)
                chunks: list[bytes] = []
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                return b"".join(chunks), content_length
        except (httpx.TransportError, httpx.HTTPStatusError, httpx.TimeoutException) as exc:
            last_exc = exc
            if attempt + 1 >= MAX_ATTEMPTS:
                break
            time.sleep(BACKOFF_SECONDS[attempt])
    raise NetworkError(f"Скачивание {path} не удалось после {MAX_ATTEMPTS} попыток", cause=last_exc)
