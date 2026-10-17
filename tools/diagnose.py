"""Таксономия ошибок парсера без ручного разбора записей.

    # размеченные данные (holdout, синтетика): с эталоном
    python tools/diagnose.py --cases tests/fixtures/synth_corpus.json --cases tests/fixtures/holdout_cases.json
    # неразмеченные (train.json на хакатоне): только сигналы без эталона
    python tools/diagnose.py --input train.json --out reports/train_diag.md

Сигналы без эталона: пустые ключевые поля, слова-ориентиры в уличной части,
неразобранные «хвосты», признаки конфликта «номер мкр против дома»,
неуверенные записи по городам. С эталоном — ещё точность по полям и тегам,
классы ошибок (пропущено / лишнее / обрезано / захвачено лишнее / другое).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from eval import (  # noqa: E402
    OFFICIAL_GROUPS,
    district_restoration,
    evaluate,
    field_outcome,
    norm,
    scope_gold,
    score_record,
)

from jana_extract.parser import FIELDS  # noqa: E402
from jana_extract.service import extract_batch  # noqa: E402

LANDMARK_WORDS = re.compile(
    r"\b(магазин\w*|маг|склад\w*|участ\w*|рын\w*|базар\w*|кафе|аптек\w*|школ\w*|мектеп\w*|"
    r"тц|трц|бц|тд|супермаркет|остановк\w*|напротив|возле|около|рядом|мечет\w*|больниц\w*|"
    r"поликлиник\w*|азс|банк\w*|qazpost|казпочт\w*|kazpost|ориентир\w*|дүкен\w*|садик|"
    r"детсад\w*|сто|гараж\w*|офис)\b",
    re.I,
)
LOW_CONF = 0.75
FIELD_WEIGHT = {f: w for w, fs in OFFICIAL_GROUPS.values() for f in fs}


def classify(p, g) -> str:
    np_, ng = norm(p), norm(g)
    if ng is not None and np_ is None:
        return "пропущено"
    if ng is None and np_ is not None:
        return "лишнее (в эталоне пусто)"
    if ng in np_:
        return "захвачено лишнее"
    if np_ in ng:
        return "обрезано"
    return "другое значение"


def fmt_parse(o: dict) -> str:
    return ", ".join(f"{k}={o[k]!r}" for k in FIELDS if o.get(k) is not None) or "∅"


def load(args):
    recs, gold, tags = [], [], []
    for path in args.cases or []:
        for i, c in enumerate(json.loads(Path(path).read_text(encoding="utf-8"))):
            recs.append({"id": c.get("id", f"{Path(path).stem}_{i}"), "raw_address": c["raw"]})
            gold.append(c["expect"])
            tags.append(c.get("tags") or [Path(path).stem])
    for path in args.input or []:
        for r in json.loads(Path(path).read_text(encoding="utf-8")):
            recs.append({"id": r.get("id"), "raw_address": r.get("raw_address")})
            gold.append(None)
            tags.append([Path(path).stem])
    return recs, gold, tags


def pct(a, b) -> str:
    return f"{a} ({a / b:.1%})" if b else "0"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", action="append", help="размеченный файл [{raw, expect, tags?}]")
    ap.add_argument("--input", action="append", help="неразмеченный файл [{id, raw_address}]")
    ap.add_argument("--out")
    ap.add_argument("--examples", type=int, default=4)
    args = ap.parse_args(argv)

    recs, gold_full, tags = load(args)
    # Район в метрике — только там, где он есть в строке (правило ТЗ);
    # восстановление района оценивается отдельно (раздел 0).
    gold = [
        None if g is None else scope_gold([g], [r["raw_address"]], "text")[0]
        for g, r in zip(gold_full, recs, strict=True)
    ]
    out = extract_batch(recs, debug=True)
    n = len(out)
    L: list[str] = []
    w = L.append

    labeled = [i for i, g in enumerate(gold) if g is not None]
    w(f"# Диагностика парсера\n\nЗаписей: {n}, из них с эталоном: {len(labeled)}.\n")

    if labeled:
        res = evaluate([out[i] for i in labeled], [gold[i] for i in labeled])
        w(
            f"## Метрика (симулятор жюри)\n\nИтог: **{res['score']:.4f}**, записей без ошибок: "
            f"{res['exact_records']:.1%}.\n"
        )
        w("| Поле | Точность | Засчитано/учтено | Вес группы |\n|---|---|---|---|")
        for f in FIELDS:
            ok, cnt = res["per_field_counts"][f]
            a = res["per_field"][f]
            w(
                f"| {f} | {'—' if a is None else f'{a:.3f}'} | {ok}/{cnt} | {FIELD_WEIGHT.get(f, 'вне групп')} |"
            )
        w("")

    if labeled:
        dr = district_restoration(
            [out[i] for i in labeled],
            [gold_full[i] for i in labeled],
            [recs[i]["raw_address"] for i in labeled],
        )
        if dr["records"]:
            w("## 0. Восстановление района (нет в строке; вне автоматической метрики)\n")
            w(
                f"Записей с районом в эталоне, но не в строке: {dr['records']}; "
                f"покрытие {dr['coverage']:.3f}, точность {dr['precision']:.3f}; "
                f"район выдан при пустом эталоне: {dr['emitted_when_gold_null']}.\n"
            )

    # ------------------------------------------------------------ покрытие
    w("## 1. Пустые ключевые поля\n")
    w(
        "| Поле | Пусто у нас | из них в эталоне есть значение (пропуск) | у нас есть, в эталоне пусто |\n|---|---|---|---|"
    )
    for f in ("province", "city", "street_name", "house", "apartment", "district"):
        empty = sum(1 for o in out if o[f] is None)
        miss = sum(1 for i in labeled if out[i][f] is None and norm(gold[i].get(f)) is not None)
        extra = sum(
            1
            for i in labeled
            if f in gold[i] and out[i][f] is not None and norm(gold[i][f]) is None
        )
        w(f"| {f} | {pct(empty, n)} | {miss} | {extra} |")
    w("")

    # ------------------------------------------------------------ ориентиры в улице
    w("## 2. Слова-ориентиры в уличной части\n")
    lm_hits = []
    for i, o in enumerate(out):
        for f in ("street_name", "living_area"):
            if o[f] and LANDMARK_WORDS.search(o[f]):
                lm_hits.append((i, f))
    w(f"Записей: {len(lm_hits)}.\n")
    for i, f in lm_hits[: args.examples + 2]:
        w(f"- `{recs[i]['raw_address']}` → {f}={out[i][f]!r}")
    w("")

    # ------------------------------------------------------------ хвосты
    w("## 3. Неразобранные «хвосты»\n")
    tails = [(i, out[i]["_meta"]["leftovers"]) for i in range(n) if out[i]["_meta"]["leftovers"]]
    tok = Counter(t.lower() for _i, ls in tails for t in ls)
    w(
        f"Записей с хвостами: {pct(len(tails), n)}. Частые токены: "
        + ", ".join(f"`{t}`×{c}" for t, c in tok.most_common(15))
        + "\n"
    )
    for i, ls in tails[: args.examples + 1]:
        w(f"- `{recs[i]['raw_address']}` → хвост {ls}; разбор: {fmt_parse(out[i])}")
    w("")

    # ------------------------------------------------------------ числа
    w("## 4. Конфликты трактовки чисел (мкр / дом / квартира)\n")
    conflicts = defaultdict(list)
    for i, o in enumerate(out):
        sn, h = o["street_name"] or "", o["house"]
        nums_in_name = re.findall(r"\d+[а-яa-z]?(?:/\d+)?", sn, re.I)
        if o["street_type"] == "микрорайон" and nums_in_name and h is None:
            conflicts["номер в названии мкр, дома нет (возможно, номер дома ушёл в мкр)"].append(i)
        if o["street_type"] == "микрорайон" and sn.isdigit() and h is None:
            conflicts["мкр-число без дома"].append(i)
        g = gold[i]
        if g and g.get("house") and o["house"] != g["house"]:
            if g["house"] in sn:
                conflicts["[эталон] номер дома попал в название улицы/мкр"].append(i)
            elif o["house"] and g.get("street_name") and o["house"] in (g["street_name"] or ""):
                conflicts["[эталон] число из названия мкр стало домом"].append(i)
            elif o["house"] and o["house"] == g.get("apartment"):
                conflicts["[эталон] квартира стала домом"].append(i)
    if not conflicts:
        w("Не найдено.\n")
    for k, ids in sorted(conflicts.items(), key=lambda kv: -len(kv[1])):
        w(f"**{k}** — {len(ids)}")
        for i in ids[: args.examples]:
            w(
                f"- `{recs[i]['raw_address']}` → {fmt_parse(out[i])}"
                + (
                    f"; эталон house={gold[i].get('house')!r}, street={gold[i].get('street_name')!r}"
                    if gold[i]
                    else ""
                )
            )
        w("")

    # ------------------------------------------------------------ города
    w("## 5. Неуверенные записи по городам\n")
    by_city: dict[str, list[int]] = defaultdict(list)
    for i, o in enumerate(out):
        key = o["province"] or "∅ (область не определена)"
        by_city[key].append(i)
    w(f"Порог неуверенности: confidence < {LOW_CONF}.\n")
    w(
        "| Область / город респ. значения | Записей | Неуверенных | Средний confidence | Точность (эталон) |\n|---|---|---|---|---|"
    )
    rows = []
    for k, ids in by_city.items():
        low = sum(1 for i in ids if out[i]["confidence"] < LOW_CONF)
        mean = sum(out[i]["confidence"] for i in ids) / len(ids)
        lab = [i for i in ids if gold[i] is not None]
        sc = [s for i in lab if (s := score_record(out[i], gold[i])) is not None]
        rows.append((low / len(ids), k, len(ids), low, mean, sum(sc) / len(sc) if sc else None))
    for share, k, cnt, low, mean, acc in sorted(rows, reverse=True):
        w(
            f"| {k} | {cnt} | {low} ({share:.0%}) | {mean:.2f} | {'—' if acc is None else f'{acc:.3f}'} |"
        )
    w("")

    if labeled:
        # -------------------------------------------------------- по тегам
        w("## 6. Точность по паттернам (теги синтетики)\n")
        tag_ids: dict[str, list[int]] = defaultdict(list)
        for i in labeled:
            for t in tags[i]:
                tag_ids[t].append(i)
        w("| Паттерн | Записей | Итог | Без ошибок |\n|---|---|---|---|")
        trows = []
        for t, ids in tag_ids.items():
            sc = [score_record(out[i], gold[i]) for i in ids]
            sc = [s for s in sc if s is not None]
            trows.append((sum(sc) / len(sc), t, len(ids), sum(1 for s in sc if s == 1.0) / len(sc)))
        for s, t, cnt, ex in sorted(trows):
            w(f"| {t} | {cnt} | {s:.3f} | {ex:.0%} |")
        w("")

        # -------------------------------------------------------- топ-10
        w("## 7. Топ-10 паттернов проблем\n")
        w(
            "Сигнатура = поле + класс ошибки. «Потеря» — сколько итоговой метрики (в п.п.) "
            "вернётся, если исправить все записи класса (оценка сверху).\n"
        )
        sig: dict[str, list[int]] = defaultdict(list)
        for i in labeled:
            for f in FIELDS:
                if field_outcome(out[i], gold[i], f) is False:
                    sig[f"{f}: {classify(out[i][f], gold[i][f])}"].append(i)
        base = evaluate([out[i] for i in labeled], [gold[i] for i in labeled])["score"]

        def loss(field, ids):
            fixed = {i: dict(out[i], **{field: gold[i][field]}) for i in ids}
            preds = [fixed.get(i, out[i]) for i in labeled]
            return evaluate(preds, [gold[i] for i in labeled])["score"] - base

        ranked = sorted(sig.items(), key=lambda kv: -len(kv[1]))[:10]
        w("| # | Сигнатура | Записей | Потеря, п.п. | Частые теги |\n|---|---|---|---|---|")
        details = []
        for k, (s, ids) in enumerate(ranked, 1):
            field = s.split(":")[0]
            tag_c = Counter(
                t for i in ids for t in tags[i] if not t.startswith("street:")
            ).most_common(3)
            st_c = Counter(t for i in ids for t in tags[i] if t.startswith("street:")).most_common(
                2
            )
            lo = loss(field, ids) * 100
            w(
                f"| {k} | {s} | {len(ids)} | {lo:.2f} | "
                + ", ".join(f"{t}×{c}" for t, c in st_c + tag_c)
                + " |"
            )
            details.append((k, s, ids, field))
        w("")
        for k, s, ids, field in details:
            w(f"### {k}. {s} ({len(ids)})\n")
            for i in ids[: args.examples]:
                w(
                    f"- `{recs[i]['raw_address']}`  \n  у нас {field}={out[i][field]!r}, эталон {gold[i][field]!r}; "
                    f"разбор: {fmt_parse(out[i])}"
                )
            w("")

    text = "\n".join(L)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"→ {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
