import json, sys, collections
sys.path[:0] = ['.', 'tools']
from eval import district_in_text, norm
from jana_extract.service import extract_batch
from jana_extract.reference import get_reference
from jana_extract.text import core
ref = get_reference()
C = json.load(open('tests/fixtures/synth_corpus.json', encoding='utf-8'))
out = extract_batch([{'id': c['id'], 'raw_address': c['raw']} for c in C], debug=True)
# 1) emitted when gold null
cat = collections.Counter(); ex = collections.defaultdict(list)
for c, o in zip(C, out):
    g = c['expect']
    if district_in_text(c['raw'], g['district']) or g['district'] is not None or o['district'] is None:
        continue
    k = 'rep' if g['province'] in ('Алматы', 'Астана', 'Шымкент') else ('village' if 'village' in c['tags'] else 'town')
    cat[k] += 1; ex[k].append(c['raw'])
print('emitted when gold null:', dict(cat))
for k, v in ex.items(): print(' ', k, v[:3])
# 2) threshold curve for republican cities
rows = []
for c, o in zip(C, out):
    g = c['expect']
    if g['province'] not in ('Алматы', 'Астана', 'Шымкент') or district_in_text(c['raw'], g['district']) or not g['district']:
        continue
    reg = ref.region_by_name[g['province']]
    cands = []
    for src in ('living_area', 'street_name'):
        if o[src]:
            hit = ref.area_or_street_district(reg, core(o[src]))
            if hit: cands.append((src, ref.district_name(hit[0]), hit[1]))
    rows.append((g['district'], cands))
print('rep-city records with restorable district:', len(rows))
for order in (('street_name', 'living_area'), ('living_area', 'street_name')):
    print('order', order)
    for t in (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9):
        em = ok = 0
        for gd, cands in rows:
            pick = None
            for src in order:
                h = next((x for x in cands if x[0] == src), None)
                if h and h[2] >= t:
                    pick = h[1]; break
            if pick: em += 1; ok += pick == gd
        print(f'   t={t:.2f} coverage={em/len(rows):.3f} precision={ok/max(em,1):.3f} (n={em})')
