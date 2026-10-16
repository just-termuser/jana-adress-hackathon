"""CLI: python -m jana_extract input.json [-o output.json]"""

from __future__ import annotations

import argparse
import json
import sys
import time

from .service import extract_batch


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jana_extract", description=__doc__)
    ap.add_argument("input", help="JSON-массив записей {id, raw_address}")
    ap.add_argument("-o", "--output", help="куда записать результат (по умолчанию stdout)")
    ap.add_argument("--debug", action="store_true", help="добавить _meta в каждую запись")
    args = ap.parse_args(argv)

    with open(args.input, encoding="utf-8-sig") as fh:
        records = json.load(fh)
    t0 = time.perf_counter()
    out = extract_batch(records, debug=args.debug)
    took = time.perf_counter() - t0
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        sys.stdout.write(text + "\n")
    print(f"{len(out)} записей за {took:.2f} с", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
