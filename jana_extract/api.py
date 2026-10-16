"""REST API: /parse, /parse/batch, /parse/file и веб-интерфейс на /."""

from __future__ import annotations

import csv
import io
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .parser import FIELDS
from .reference import get_reference
from .service import OUTPUT_KEYS, extract_batch, extract_record

STATIC = Path(__file__).resolve().parents[1] / "static"
MAX_BATCH = 10_000


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_reference()  # справочник грузится один раз при старте
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Jana Extract",
    version="0.1.0",
    description=(
        "Извлечение полей адреса без нормализации. Текстовые поля — подстроки "
        "raw_address; province, district и street_type — в справочном формате."
    ),
)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/reference/info")
def reference_info() -> dict[str, Any]:
    return get_reference().meta


@app.post("/parse")
def parse_one(
    item: dict[str, Any] = Body(
        ..., examples=[{"id": "rec_1", "raw_address": "г. Алматы, ул. Абая 5"}]
    ),
    debug: bool = Query(False, description="Добавить _meta: уверенность по полям и позиции"),
) -> dict[str, Any]:
    return extract_record(item, debug=debug)


def _records_from_payload(payload: Any) -> list[Any]:
    if isinstance(payload, dict):
        payload = payload.get("records", payload.get("items"))
    if not isinstance(payload, list):
        raise HTTPException(422, 'Ожидается JSON-массив записей или {"records": [...]}')
    if len(payload) > MAX_BATCH:
        raise HTTPException(413, f"Не больше {MAX_BATCH} записей за запрос")
    return payload


@app.post("/parse/batch")
def parse_batch(
    payload: Any = Body(
        ...,
        examples=[
            [
                {
                    "id": "rec_00417",
                    "raw_address": "город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70",
                }
            ]
        ],
    ),
    debug: bool = Query(False),
) -> JSONResponse:
    records = _records_from_payload(payload)
    t0 = time.perf_counter()
    out = extract_batch(records, debug=debug)
    took = time.perf_counter() - t0
    return JSONResponse(
        out, headers={"X-Records": str(len(out)), "X-Elapsed-Ms": f"{took * 1000:.0f}"}
    )


def _read_upload(name: str, data: bytes) -> list[Any]:
    text = data.decode("utf-8-sig", errors="replace")
    lower = (name or "").lower()
    if lower.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(text))
        return [dict(row) for row in reader]
    if lower.endswith(".jsonl"):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if lower.endswith(".txt"):
        return [
            {"id": f"line_{i + 1}", "raw_address": line}
            for i, line in enumerate(text.splitlines())
            if line.strip()
        ]
    try:
        return _records_from_payload(json.loads(text))
    except json.JSONDecodeError as exc:
        raise HTTPException(422, f"Не удалось прочитать файл: {exc}") from exc


@app.post("/parse/file")
async def parse_file(
    file: UploadFile = File(...),
    format: str = Query("json", pattern="^(json|csv)$"),
    debug: bool = Query(False),
) -> Response:
    records = _records_from_payload(_read_upload(file.filename or "", await file.read()))
    out = extract_batch(records, debug=debug)
    if format == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(OUTPUT_KEYS), extrasaction="ignore")
        w.writeheader()
        w.writerows(out)
        return Response(
            "﻿" + buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="parsed.csv"'},
        )
    return JSONResponse(out)


@app.get("/fields")
def fields() -> list[str]:
    return list(FIELDS)


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")
