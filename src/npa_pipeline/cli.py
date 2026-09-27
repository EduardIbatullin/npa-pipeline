"""Тонкий CLI поверх библиотеки."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from npa_pipeline.authorities import AuthorityCache
from npa_pipeline.db import DEFAULT_DB_PATH, DocumentStore
from npa_pipeline.http_client import create_client
from npa_pipeline.import_json import import_json_sidecars
from npa_pipeline.models import Query, parse_user_date
from npa_pipeline.service import fetch_document


def _query_from_args(args: argparse.Namespace) -> Query:
    if args.json:
        data = json.loads(Path(args.json).read_text(encoding="utf-8"))
        date_val = data.get("date")
        return Query(
            eo_number=data.get("eo_number") or data.get("eoNumber"),
            authority_guid=data.get("authority_guid") or data.get("authorityGuid"),
            authority_name=data.get("authority_name") or data.get("authorityName"),
            number=data.get("number"),
            date=parse_user_date(date_val) if date_val else None,
            doc_type_id=data.get("doc_type_id") or data.get("docTypeId"),
        )
    return Query(
        eo_number=args.eo,
        authority_guid=args.authority_guid,
        authority_name=args.authority,
        number=args.number,
        date=parse_user_date(args.date) if args.date else None,
        doc_type_id=args.doc_type_id,
    )


def cmd_fetch(args: argparse.Namespace) -> int:
    query = _query_from_args(args)
    result = fetch_document(
        query,
        cache_path=args.db,
        out_dir=args.out_dir,
        download=not args.no_download,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.status.value == "found" else 1


def cmd_refresh_authorities(args: argparse.Namespace) -> int:
    cache = AuthorityCache(args.db)
    with create_client() as client:
        count = cache.build(client)
    print(json.dumps({"authorities": count, "db": str(Path(args.db))}, ensure_ascii=False))
    return 0


def cmd_import_json(args: argparse.Namespace) -> int:
    store = DocumentStore(args.db)
    stats = import_json_sidecars(
        args.out_dir,
        store,
        delete_json=args.delete_json,
    )
    print(json.dumps({"db": str(Path(args.db)), **stats}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="npa", description="Поиск и скачивание НПА")
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help="Путь к единой SQLite-базе (органы + документы)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="Найти и скачать документ")
    fetch.add_argument("--eo", help="Известный eoNumber")
    fetch.add_argument("--authority", help="Название органа")
    fetch.add_argument("--authority-guid", help="GUID органа")
    fetch.add_argument("--number", help="Номер документа")
    fetch.add_argument("--date", help="Дата подписания YYYY-MM-DD или DD.MM.YYYY")
    fetch.add_argument("--doc-type-id", help="GUID типа документа (опционально)")
    fetch.add_argument("--json", help="Путь к JSON с полями запроса")
    fetch.add_argument("--out-dir", default="downloads", help="Каталог для PDF")
    fetch.add_argument(
        "--no-download",
        action="store_true",
        help="Только поиск, без скачивания PDF",
    )
    fetch.set_defaults(func=cmd_fetch)

    refresh = sub.add_parser("refresh-authorities", help="Обновить справочник органов")
    refresh.set_defaults(func=cmd_refresh_authorities)

    imp = sub.add_parser(
        "import-json",
        help="Импортировать старые JSON-sidecar из downloads/ в БД",
    )
    imp.add_argument("--out-dir", default="downloads", help="Каталог с PDF и JSON")
    imp.add_argument(
        "--delete-json",
        action="store_true",
        help="Удалить JSON после успешного импорта",
    )
    imp.set_defaults(func=cmd_import_json)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    code = args.func(args)
    sys.exit(code)


if __name__ == "__main__":
    main()
