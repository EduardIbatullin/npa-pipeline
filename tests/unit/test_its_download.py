"""Тесты скачивания/проверки файлов ИТС и резолвинга fetch_its."""

from __future__ import annotations

from pathlib import Path

import httpx

from npa_pipeline import burondt
from npa_pipeline.http_client import create_client
from npa_pipeline.its import ItsCache
from npa_pipeline.its_download import already_downloaded_its, fetch_its, fetch_its_by_url_id, save_its_file
from npa_pipeline.models import Status

FIXTURES = Path(__file__).parent / "fixtures" / "burondt"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _minimal_pdf() -> bytes:
    return b"""%PDF-1.1
1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj
2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj
3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>endobj
xref
0 4
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
trailer<< /Size 4 /Root 1 0 R >>
startxref
190
%%EOF
"""


def _seeded_cache(tmp_path: Path) -> ItsCache:
    cache = ItsCache(tmp_path / "its.db")
    card = burondt.parse_card(_read("its_card.html"), url_id=1640)
    cache.upsert_card(card)
    return cache


def test_save_its_file_atomic_and_dedup(tmp_path: Path):
    cache = _seeded_cache(tmp_path)
    data = _minimal_pdf()
    path = save_its_file(
        tmp_path / "out",
        data=data,
        designation="ИТС 28-2021",
        file_id=2190,
        role="document",
        pages=1,
        cache=cache,
    )
    assert path.is_file()
    assert not path.name.endswith(".part")
    assert already_downloaded_its(cache, 2190) == path


def test_fetch_its_not_found(tmp_path: Path):
    cache = ItsCache(tmp_path / "its.db")

    def handler(request: httpx.Request) -> httpx.Response:  # не должен вызываться
        raise AssertionError("сеть не должна вызываться для пустого кэша")

    with create_client(base_url=burondt.BURONDT_BASE_URL, transport=httpx.MockTransport(handler)) as client:
        result = fetch_its(client, designation="ИТС 999-2099", out_dir=tmp_path / "out", cache=cache)

    assert result.status == Status.NOT_FOUND


def test_fetch_its_downloads_and_verifies(tmp_path: Path):
    cache = _seeded_cache(tmp_path)
    data = _minimal_pdf()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=data,
            headers={"Content-Disposition": 'attachment; filename="file.pdf"'},
        )

    with create_client(base_url=burondt.BURONDT_BASE_URL, transport=httpx.MockTransport(handler)) as client:
        result = fetch_its(
            client, designation="ИТС 28-2021", out_dir=tmp_path / "out", cache=cache
        )

    assert result.status == Status.FOUND
    assert len(result.files) == 2
    order = next(f for f in result.files if f.role == "order")
    assert order.order_number_caption == "835"
    assert order.pdf_path is not None
    assert Path(order.pdf_path).is_file()


def test_fetch_its_skips_already_downloaded(tmp_path: Path):
    cache = _seeded_cache(tmp_path)
    data = _minimal_pdf()
    save_its_file(
        tmp_path / "out",
        data=data,
        designation="ИТС 28-2021",
        file_id=2190,
        role="document",
        pages=1,
        cache=cache,
    )

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=data)

    with create_client(base_url=burondt.BURONDT_BASE_URL, transport=httpx.MockTransport(handler)) as client:
        result = fetch_its(
            client, designation="ИТС 28-2021", out_dir=tmp_path / "out", cache=cache
        )

    assert result.status == Status.FOUND
    # только file_id 2504 (приказ) должен реально скачиваться — 2190 уже на диске
    assert calls["n"] == 1


def test_fetch_its_ambiguous_includes_files_per_candidate(tmp_path: Path):
    # Две версии номера «47» — запрос по номеру без года должен вернуть AMBIGUOUS
    # с файлами (роль + подпись) у каждого кандидата, чтобы UI мог показать
    # «справочник + приказ» и дать скачать конкретную версию без нового резолвинга.
    cache = ItsCache(tmp_path / "its.db")
    cache.upsert_card(
        burondt.ItsCard(
            url_id=1150,
            designation="ИТС НДТ 47",
            title="ИТС НДТ 47",
            files=[
                burondt.ItsCardFile(file_id=1420, caption="ИТС 47", role="document"),
                burondt.ItsCardFile(
                    file_id=2476,
                    caption="Приказ №2846 от 15.12.2017 об утверждении ИТС 47-2017",
                    role="order",
                ),
            ],
        )
    )
    cache.upsert_card(
        burondt.ItsCard(
            url_id=2100,
            designation="ИТС 47-2023",
            title="ИТС 47-2023",
            files=[
                burondt.ItsCardFile(file_id=2983, caption="ИТС 47-2023", role="document"),
                burondt.ItsCardFile(
                    file_id=2982,
                    caption="Приказ 21 декабря 2023 г. № 2759 об утверждении ИТС 47-2023",
                    role="order",
                ),
            ],
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:  # не должен вызываться
        raise AssertionError("AMBIGUOUS не должен скачивать файлы")

    with create_client(base_url=burondt.BURONDT_BASE_URL, transport=httpx.MockTransport(handler)) as client:
        result = fetch_its(client, designation="ИТС 47", out_dir=tmp_path / "out", cache=cache)

    assert result.status == Status.AMBIGUOUS
    assert [c["url_id"] for c in result.candidates] == [2100, 1150]  # новые сначала
    for c in result.candidates:
        roles = {f["role"] for f in c["files"]}
        assert roles == {"document", "order"}


def test_fetch_its_by_url_id_bypasses_resolve(tmp_path: Path):
    cache = _seeded_cache(tmp_path)
    data = _minimal_pdf()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=data, headers={"Content-Disposition": 'attachment; filename="file.pdf"'})

    with create_client(base_url=burondt.BURONDT_BASE_URL, transport=httpx.MockTransport(handler)) as client:
        result = fetch_its_by_url_id(client, url_id=1640, out_dir=tmp_path / "out", cache=cache)

    assert result.status == Status.FOUND
    assert result.url_id == 1640
    assert len(result.files) == 2
    assert all(Path(f.pdf_path).is_file() for f in result.files)
