"""Классы причин ошибок на синтетике и их цена в метрике.

    python tools/causes.py

Каждый класс — эвристика над (raw, эталон, разбор, теги). «Цена» — на сколько
п.п. вырастет итог, если исправить поля класса во всех его записях (оценка
сверху). CONV — расхождение конвенций (решение за постановкой, не за кодом),
AMBIG — неустранимая без контекста неоднозначность, BUG — дефект парсера.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
from eval import evaluate, field_outcome  # noqa: E402

from jana_extract.parser import FIELDS  # noqa: E402
from jana_extract.service import extract_batch  # noqa: E402

C = json.load(open(ROOT / "tests/fixtures/synth_corpus.json", encoding="utf-8"))
gold = [c["expect"] for c in C]
raw = [c["raw"] for c in C]
tags = [set(c["tags"]) for c in C]
out = extract_batch([{"id": c["id"], "raw_address": c["raw"]} for c in C], debug=True)
base = evaluate(out, gold, raws=raw)["score"]


def bad(i, f):
    return field_outcome(out[i], gold[i], f) is False


def gain(ids, fields=None):
    fx = []
    for i, o in enumerate(out):
        if i in ids:
            o = dict(o)
            for f in fields or FIELDS:
                o[f] = gold[i][f]
        fx.append(o)
    return (evaluate(fx, gold, raws=raw)["score"] - base) * 100


classes = {}


def add(name, pred, fields=None):
    ids = {i for i in range(len(C)) if pred(i)}
    classes[name] = (len(ids), gain(ids, fields), sorted(ids))


def anybad(i, fs):
    return any(bad(i, f) for f in fs)


add(
    "CONV district для райцентра (эталон null, у нас район)",
    lambda i: bad(i, "district") and gold[i]["district"] is None and out[i]["district"] is not None,
    ["district"],
)
add(
    "CONV district: у нас null при неоднозначной улице (порог 60%)",
    lambda i: (
        bad(i, "district")
        and out[i]["district"] is None
        and gold[i]["province"] in ("Алматы", "Астана", "Шымкент")
    ),
    ["district"],
)
add(
    "CONV district: мкр vs улица как источник района",
    lambda i: (
        bad(i, "district")
        and out[i]["district"]
        and gold[i]["district"]
        and "street:mkr_plus_street" in tags[i]
    ),
    ["district"],
)
add(
    "CONV «мкр X N, M»: N часть мкр или дом",
    lambda i: (
        "house_in_street_segment" in tags[i]
        and gold[i]["street_type"] == "микрорайон"
        and anybad(i, ["house", "street_name"])
    ),
)
add(
    "AMBIG одноимённые НП / район без города",
    lambda i: bad(i, "province") and out[i]["province"] is not None,
)
add(
    "BUG казахские многословные области",
    lambda i: (
        re.search(r"Қазақстан облысы", raw[i]) is not None
        and (anybad(i, FIELDS) or bool(out[i]["_meta"]["leftovers"]))
    ),
)
add(
    "BUG ориентир в падеже («за мечетью»)",
    lambda i: (
        bool(gold[i]["landmark"]) and anybad(i, ["landmark", "street_name", "living_area", "house"])
    ),
)
add(
    "BUG опечатка в городе рядом с маркером",
    lambda i: "typo_city" in tags[i] and anybad(i, ["city", "province"]),
)
add(
    "BUG улица без маркера перед помеченным домом",
    lambda i: (
        gold[i]["street_type"] is None
        and out[i]["street_name"] is None
        and bool(gold[i]["street_name"])
    ),
)
add(
    "BUG дубль города (без запятой / принят за улицу)",
    lambda i: "city_duplicated" in tags[i] and anybad(i, ["street_name", "living_area", "city"]),
)
add(
    "BUG без запятых: хвост в названии улицы",
    lambda i: "no_commas" in tags[i] and bad(i, "street_name") and "city_duplicated" not in tags[i],
)
add(
    "BUG «60квартал 202» без запятых",
    lambda i: "glued_kvartal" in tags[i] and anybad(i, ["street_name", "house"]),
)
add("BUG скобки как альтернативное имя", lambda i: "(" in raw[i] and anybad(i, FIELDS))
print("base", round(base, 4))
for k, (n, g, ex) in sorted(classes.items(), key=lambda kv: -kv[1][1]):
    print(f"{g:5.2f} pp | {n:4d} | {k}")
    for i in ex[:3]:
        print("        ", repr(raw[i]))
