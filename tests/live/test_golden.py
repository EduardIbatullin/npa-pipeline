"""Live-прогон эталонного набора против publication.pravo.gov.ru."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from npa_pipeline.authorities import AuthorityCache
from npa_pipeline.db import DocumentStore
from npa_pipeline.http_client import create_client
from npa_pipeline.models import Query, parse_user_date
from npa_pipeline.service import fetch_document

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "golden_set.json"


@pytest.mark.live
def test_golden_set(tmp_path: Path):
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    db_path = Path("data/npa.db")
    out_dir = tmp_path / "downloads"

    with create_client() as client:
        cache = AuthorityCache(db_path)
        store = DocumentStore(db_path)
        if cache.count() < 1000:
            cache.build(client)

        ok = 0
        false_found_exact = 0
        false_found_digits = 0
        failures: list[str] = []

        for i, case in enumerate(cases):
            if i:
                time.sleep(1.0)
            qdata = case["query"]
            query = Query(
                eo_number=qdata.get("eo_number"),
                authority_guid=qdata.get("authority_guid"),
                authority_name=qdata.get("authority_name"),
                number=qdata.get("number"),
                date=parse_user_date(qdata["date"]) if qdata.get("date") else None,
            )
            expect = case["expect"]
            result = fetch_document(
                query,
                client=client,
                cache=cache,
                store=store,
                cache_path=db_path,
                out_dir=out_dir,
                download=False,
            )

            status_ok = result.status.value == expect["status"]
            eo_ok = True
            if "eoNumber" in expect:
                eo_ok = result.eo_number == expect["eoNumber"]
            mt_ok = True
            if "match_type" in expect:
                mt_ok = (result.match_type.value if result.match_type else None) == expect[
                    "match_type"
                ]

            passed = status_ok and eo_ok and mt_ok
            if passed:
                ok += 1
            else:
                failures.append(
                    f"{case['id']}: expected {expect}, got "
                    f"status={result.status.value} eo={result.eo_number} "
                    f"match={result.match_type} msg={result.message}"
                )
                if expect["status"] != "found" and result.status.value == "found":
                    if result.match_type and result.match_type.value == "digits_only":
                        false_found_digits += 1
                    else:
                        false_found_exact += 1
                if expect.get("status") == "found" and result.status.value == "found":
                    if expect.get("eoNumber") and result.eo_number != expect["eoNumber"]:
                        if result.match_type and result.match_type.value == "digits_only":
                            false_found_digits += 1
                        else:
                            false_found_exact += 1

            print(
                f"{case['id']}: {'OK' if passed else 'FAIL'} "
                f"got={result.status.value}/{result.eo_number}/{result.match_type}"
            )

    accuracy = ok / len(cases) if cases else 0.0
    print(f"accuracy={accuracy:.3f} ({ok}/{len(cases)})")
    print(f"false_found_exact={false_found_exact}")
    print(f"false_found_digits_only={false_found_digits}")
    for line in failures:
        print(line)

    assert false_found_exact == 0
    assert false_found_digits == 0
    assert ok == len(cases), failures
