"""Разведка справочников органов и дозапись фикстур."""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://publication.pravo.gov.ru"
OUT = Path("tests/unit/fixtures")


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> None:
    blocks = json.loads((OUT / "public_blocks.json").read_text(encoding="utf-8"))
    b0 = blocks[0]
    print("block name:", b0.get("name"))
    cats = b0.get("categories") or []
    print("nested categories:", len(cats))
    if cats:
        c0 = cats[0]
        print("cat0 keys:", list(c0.keys()))
        print("cat0 name:", c0.get("name"), "id:", c0.get("id"))

    # Пробуем разные варианты запроса органов
    paths = ["/api/SignatoryAuthorities"]
    if cats:
        cid = cats[0]["id"]
        paths = [
            f"/api/SignatoryAuthorities?CategoryId={cid}",
            f"/api/SignatoryAuthorities?categoryId={cid}",
            f"/api/SignatoryAuthorities?PublicBlockId={b0['id']}",
            "/api/SignatoryAuthorities",
        ]

    for path in paths:
        try:
            data = get(path)
            print("OK", path, "type", type(data).__name__, end="")
            if isinstance(data, list):
                print(" len", len(data))
                if data:
                    print("  keys", list(data[0].keys()))
                    sample = {
                        k: data[0].get(k)
                        for k in (
                            "id",
                            "name",
                            "categoryId",
                            "publicBlockId",
                            "weight",
                        )
                    }
                    print("  sample", sample)
                    (OUT / "signatory_authorities_sample.json").write_text(
                        json.dumps(data[:5], ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
            elif isinstance(data, dict):
                print(" keys", list(data.keys()))
            break
        except Exception as e:
            print("FAIL", path, e)

    # Categories отдельно?
    for path in [
        f"/api/Categories?PublicBlockId={b0['id']}",
        f"/api/Categories?publicBlockId={b0['id']}",
        "/api/Categories",
    ]:
        try:
            data = get(path)
            print("OK", path, type(data).__name__, len(data) if hasattr(data, "__len__") else "")
            (OUT / "categories_sample.json").write_text(
                json.dumps(data if not isinstance(data, list) else data[:10], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            break
        except Exception as e:
            print("FAIL", path, e)


if __name__ == "__main__":
    main()
