"""Синтетический набор адресов для замера скорости и проверки инвариантов.

    python tools/bench.py --n 1000 --out bench_1000.json        # только сгенерировать
    python tools/bench.py --n 1000 --run                          # в процессе
    python tools/bench.py --n 1000 --url http://localhost:8000    # через API

Это НЕ эталон точности: адреса собраны из шаблонов, ожидаемые значения
известны только для дома/квартиры/улицы, и метрика по ним — санити-чек.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CITIES = [
    ("г. Алматы", "город Алматы/Алматы қаласы", "Алматы"),
    ("г. Астана", "город Астана/Астана қаласы", "Астана"),
    ("г. Шымкент", "Шымкент қаласы", "Шымкент"),
    ("город Актобе", "город Актобе/Ақтөбе қаласы", "Актобе"),
    ("г.Караганда", "Қарағанды қаласы", "Караганда"),
    ("Павлодар", "г Павлодар", "Павлодар"),
    ("Костанай", "г. Костанай", "Костанай"),
    ("г. Тараз", "Тараз", "Тараз"),
]
STREETS = [
    "Абая",
    "Толе би",
    "Казыбек би",
    "казибек би",
    "Назарбаева",
    "Сатпаева",
    "Жибек Жолы",
    "Кабанбай батыра",
    "Байтурсынова",
    "Розыбакиева",
    "Momyshuly",
    "Гагарина",
    "8 Марта",
]
MKRS = ["Аксай 3а", "Самал-2", "Орбита-1", "Жетысу-2", "Нурсат", "Таугуль 1", "Мамыр-4", "12", "5"]
LANDMARKS = ["магазин береке", "Qazpost Солнечный", "напротив ТЦ Мега", "возле школы 45"]
INVIS = ["", "", "", "​", " "]


def gen(n: int, seed: int = 42) -> list[dict]:
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        city = rnd.choice(CITIES)
        city_s = rnd.choice(city[:2])
        house = rnd.choice(
            [
                str(rnd.randint(1, 250)),
                f"{rnd.randint(1, 99)}а",
                f"{rnd.randint(1, 99)}/{rnd.randint(1, 9)}",
            ]
        )
        apt = str(rnd.randint(1, 300))
        style = rnd.randint(0, 7)
        street = rnd.choice(STREETS)
        mkr = rnd.choice(MKRS)
        if style == 0:
            raw = f"{city_s}, ул. {street} {house}, кв {apt}"
        elif style == 1:
            raw = f"{city_s}, мкр {mkr}, {house}, кв {apt}"
        elif style == 2:
            raw = f"{city_s}, {street}{house}, flat {apt}"
        elif style == 3:
            raw = f"{mkr} микрорайон {house}дом {rnd.randint(1, 9)} подьезд {apt} кв"
        elif style == 4:
            entrance, floor = rnd.randint(1, 9), rnd.randint(1, 16)
            raw = (
                f"{city_s}, мкр {mkr}, ул. {street} {house}, {entrance} подъезд, "
                f"{floor} этаж, {rnd.choice(LANDMARKS)}"
            )
        elif style == 5:
            raw = f"{city_s} {street} {house} kv {apt}"
        elif style == 6:
            raw = f"{rnd.choice(INVIS)}{city_s}, пр. {street}, д.{house}, кв.{apt}"
        else:
            raw = f"050000, Республика Казахстан, {city_s}, {street} көшесі {house}, {apt} пәтер"
        out.append({"id": f"syn_{i:05d}", "raw_address": raw, "_house": house, "_apt": apt})
    return out


def run_local(records: list[dict]) -> tuple[list[dict], float]:
    from jana_extract.service import extract_batch

    extract_batch([{"id": "warm", "raw_address": "г. Алматы"}])
    t0 = time.perf_counter()
    out = extract_batch([{"id": r["id"], "raw_address": r["raw_address"]} for r in records])
    return out, time.perf_counter() - t0


def run_api(url: str, records: list[dict]) -> tuple[list[dict], float]:
    body = json.dumps([{"id": r["id"], "raw_address": r["raw_address"]} for r in records]).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/parse/batch", body, {"Content-Type": "application/json"}
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as resp:
        out = json.load(resp)
    return out, time.perf_counter() - t0


def check(records: list[dict], out: list[dict]) -> dict:
    assert len(out) == len(records), "пропущены записи"
    text_fields = [
        "city",
        "living_area",
        "street_name",
        "house",
        "apartment",
        "entrance",
        "floor",
        "landmark",
    ]
    bad_sub = sum(
        1
        for r, o in zip(records, out, strict=True)
        for f in text_fields
        if o[f] is not None and o[f] not in r["raw_address"]
    )
    house_ok = sum(o["house"] == r["_house"] for r, o in zip(records, out, strict=True))
    apt_ok = sum(o["apartment"] == r["_apt"] for r, o in zip(records, out, strict=True))
    return {
        "records": len(out),
        "substring_violations": bad_sub,
        "house_exact": round(house_ok / len(out), 3),
        "apartment_exact": round(apt_ok / len(out), 3),
        "mean_confidence": round(sum(o["confidence"] for o in out) / len(out), 3),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--out")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--url")
    args = ap.parse_args(argv)
    records = gen(args.n)
    if args.out:
        Path(args.out).write_text(
            json.dumps(
                [{"id": r["id"], "raw_address": r["raw_address"]} for r in records],
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    if args.run or args.url:
        out, took = run_api(args.url, records) if args.url else run_local(records)
        stats = check(records, out)
        stats["seconds"] = round(took, 3)
        print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
