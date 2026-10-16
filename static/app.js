"use strict";

const FIELDS = ["province", "city", "district", "living_area", "street_type", "street_name",
  "house", "apartment", "entrance", "floor", "landmark"];
// Поля в справочном формате: могут не совпадать с подстрокой адреса.
const REFERENCE_FIELDS = new Set(["province", "district", "street_type"]);
const HIGHLIGHT = ["city", "living_area", "street_name", "house", "apartment", "entrance", "floor", "landmark"];
const LABELS = {
  province: "Область", city: "Город", district: "Район", living_area: "Мкр/ЖК",
  street_type: "Тип улицы", street_name: "Улица", house: "Дом", apartment: "Кв.",
  entrance: "Подъезд", floor: "Этаж", landmark: "Ориентир",
};
const DEMO = [
  "город Алматы/Алматы қаласы, Мкр Аксай 3а, 59, кв 70",
  "Нурсити 2 микрорайон 11дом 5 подьезд 239 кв",
  "город Актобе/Ақтөбе қаласы, проспект Абилкайыр хана 55/6, кв 12",
  "Алматы, Назарбаева8, flat 191",
  "г. Астана, ул. Казибек би 12/1, кв 5",
  "Алматы, мкр Самал-2, ул. Жолдасбекова 9, 4подьезд, 3 этаж, магазин береке",
  "Караганда, Qazpost Солнечный",
  "Алматинская обл, Карасайский р-н, с. Каскелен, ул Абая 1/2",
];

let rows = [];  // [{rec, meta, edited:Set}]
const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

async function postBatch(records) {
  const t0 = performance.now();
  const r = await fetch("/parse/batch?debug=true", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(records),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  const out = await r.json();
  return { out, ms: performance.now() - t0 };
}

async function postFile(file) {
  const t0 = performance.now();
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch("/parse/file?debug=true", { method: "POST", body: fd });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return { out: await r.json(), ms: performance.now() - t0 };
}

function load({ out, ms }) {
  rows = out.map((rec) => {
    const meta = rec._meta || {};
    delete rec._meta;
    return { rec, meta, edited: new Set() };
  });
  $("toolbar").hidden = false;
  $("legend").hidden = false;
  $("grid").hidden = false;
  renderLegend();
  render();
  const avg = rows.reduce((s, r) => s + (r.rec.confidence || 0), 0) / Math.max(rows.length, 1);
  $("stats").innerHTML = `Записей: <b>${rows.length}</b> · время: <b>${(ms / 1000).toFixed(2)} с</b> · средняя уверенность: <b>${avg.toFixed(2)}</b>`;
}

function renderLegend() {
  $("legend").innerHTML = HIGHLIGHT.map(
    (f) => `<span style="background:var(--c-${f})">${LABELS[f]}</span>`).join("");
}

function highlight(raw, spans) {
  if (!raw) return "";
  const marks = Object.entries(spans || {})
    .filter(([f]) => HIGHLIGHT.includes(f))
    .map(([f, [a, b]]) => ({ f, a, b }))
    .sort((x, y) => x.a - y.a);
  let html = "", pos = 0;
  for (const m of marks) {
    if (m.a < pos) continue;
    html += esc(raw.slice(pos, m.a));
    html += `<mark style="background:var(--c-${m.f})" title="${LABELS[m.f]}">${esc(raw.slice(m.a, m.b))}</mark>`;
    pos = m.b;
  }
  return html + esc(raw.slice(pos));
}

function confClass(c) { return c < 0.6 ? "lo" : c < 0.8 ? "mid" : "hi"; }

function isValid(field, value, raw) {
  if (value === null || value === "" || REFERENCE_FIELDS.has(field)) return true;
  return typeof raw === "string" && raw.includes(value);
}

function render() {
  const thr = parseFloat($("thr").value) || 0;
  const lowOnly = $("lowOnly").checked;
  const q = $("q").value.trim().toLowerCase();
  $("grid").querySelector("thead").innerHTML =
    `<tr><th>#</th><th>id</th><th>raw_address</th>${FIELDS.map((f) => `<th title="${f}">${LABELS[f]}</th>`).join("")}<th>conf</th></tr>`;
  const body = [];
  rows.forEach((row, i) => {
    const r = row.rec;
    if (lowOnly && r.confidence >= thr) return;
    if (q && !JSON.stringify(r).toLowerCase().includes(q)) return;
    const cells = FIELDS.map((f) => {
      const v = r[f];
      const cls = ["f", v == null ? "null" : "", row.edited.has(f) ? "edited" : "",
        isValid(f, v, r.raw_address) ? "" : "invalid"].join(" ");
      return `<td class="${cls}" contenteditable="true" data-i="${i}" data-f="${f}">${v == null ? "" : esc(v)}</td>`;
    }).join("");
    const lo = r.confidence < thr ? "lowrow" : "";
    body.push(`<tr class="${lo}"><td>${i + 1}</td><td>${esc(r.id ?? "")}</td>` +
      `<td class="raw">${highlight(r.raw_address, row.meta.spans)}</td>${cells}` +
      `<td class="conf ${confClass(r.confidence)}">${(r.confidence ?? 0).toFixed(2)}</td></tr>`);
  });
  $("grid").querySelector("tbody").innerHTML = body.join("");
}

function onEdit(e) {
  const td = e.target.closest("td.f");
  if (!td) return;
  const row = rows[+td.dataset.i];
  const f = td.dataset.f;
  const text = td.textContent.trim();
  const v = text === "" ? null : text;
  if (row.rec[f] === v) return;
  row.rec[f] = v;
  row.edited.add(f);
  // Ручная правка: подсветку в адресе для этого поля ищем заново.
  if (row.meta.spans) {
    delete row.meta.spans[f];
    const at = v && row.rec.raw_address ? row.rec.raw_address.indexOf(v) : -1;
    if (at >= 0) row.meta.spans[f] = [at, at + v.length];
  }
  render();
}

function download(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function exportJson() {
  download("parsed.json", JSON.stringify(rows.map((r) => r.rec), null, 2), "application/json");
}

function exportCsv() {
  const keys = ["id", "raw_address", ...FIELDS, "confidence"];
  const q = (v) => v == null ? "" : `"${String(v).replace(/"/g, '""')}"`;
  const lines = [keys.join(","), ...rows.map((r) => keys.map((k) => q(r.rec[k])).join(","))];
  download("parsed.csv", "﻿" + lines.join("\r\n"), "text/csv;charset=utf-8");
}

async function run(fn) {
  try { load(await fn()); } catch (err) { alert("Ошибка: " + err.message); }
}

$("pick").onclick = () => $("file").click();
$("file").onchange = (e) => e.target.files[0] && run(() => postFile(e.target.files[0]));
const drop = $("drop");
drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
drop.addEventListener("dragleave", () => drop.classList.remove("over"));
drop.addEventListener("drop", (e) => {
  e.preventDefault(); drop.classList.remove("over");
  if (e.dataTransfer.files[0]) run(() => postFile(e.dataTransfer.files[0]));
});
$("run").onclick = () => {
  const lines = $("paste").value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  if (!lines.length) return;
  run(() => postBatch(lines.map((raw, i) => ({ id: `row_${i + 1}`, raw_address: raw }))));
};
$("demo").onclick = () => { $("paste").value = DEMO.join("\n"); $("run").click(); };
$("grid").addEventListener("focusout", onEdit);
$("grid").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.matches("td.f")) { e.preventDefault(); e.target.blur(); }
});
["lowOnly", "thr", "q"].forEach((id) => $(id).addEventListener("input", render));
$("expJson").onclick = exportJson;
$("expCsv").onclick = exportCsv;
