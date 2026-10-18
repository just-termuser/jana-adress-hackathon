"""Обёртка над парсером: запись на входе → запись на выходе.

Главная гарантия: на каждую входную запись — ровно одна выходная, в том же
порядке. Что бы ни пришло (нет raw_address, число вместо строки, исключение
в парсере), запись возвращается с null-полями и confidence 0.
"""

from __future__ import annotations

import logging
from typing import Any

from .parser import FIELDS, parse_address
from .reference import Reference, get_reference

log = logging.getLogger("jana_extract")

OUTPUT_KEYS = ("id", "raw_address", *FIELDS, "confidence")


def _empty(rec_id: Any, raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"id": rec_id, "raw_address": raw}
    out.update(dict.fromkeys(FIELDS))
    out["confidence"] = 0.0
    return out


def extract_record(
    item: Any,
    debug: bool = False,
    ref: Reference | None = None,
    infer_street_type: bool | None = None,
) -> dict[str, Any]:
    if isinstance(item, dict):
        rec_id = item.get("id")
        raw = item.get("raw_address")
    elif isinstance(item, str):
        rec_id, raw = None, item
    else:
        rec_id, raw = None, None
    out = _empty(rec_id, raw)
    if not isinstance(raw, str) or not raw.strip():
        return out
    try:
        res = parse_address(raw, ref or get_reference(), infer_street_type=infer_street_type)
    except Exception:  # запись не теряем ни при каких условиях
        log.exception("parse failed for id=%r", rec_id)
        return out
    out.update(res.values)
    out["confidence"] = res.confidence
    if debug:
        out["_meta"] = {
            "field_confidence": {k: round(v, 2) for k, v in res.conf.items()},
            "spans": res.spans,
            "leftovers": res.leftovers,
            "ambiguity": res.ambiguity,
            "official": res.official,
            "inferred": res.inferred,
        }
    return out


def extract_batch(
    items: list[Any], debug: bool = False, infer_street_type: bool | None = None
) -> list[dict[str, Any]]:
    ref = get_reference()
    return [
        extract_record(it, debug=debug, ref=ref, infer_street_type=infer_street_type)
        for it in items
    ]
