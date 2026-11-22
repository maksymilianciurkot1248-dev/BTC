"""Spójność plików strony i instalatorów oraz logika synchronizacji danych w przeglądarce (ui.js).

1. Instalatory: każdy plik z listy istnieje, a każdy moduł btc_*.py importowany przez zainstalowane moduły jest instalowany (brak = serwer nie wstaje po aktualizacji).
2. Nawigacja i przekierowania ze starych adresów wskazują istniejące strony i zakładki.
3. Skrypty wbudowane w strony i ui.js mają poprawną składnię (node --check, gdy node jest dostępny).
4. UD.merge i UD.sync (scalanie zmian z dwóch urządzeń, konflikty, tryb offline) na atrapie serwera (node)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = 0


def check(c, m):
    global ok
    assert c, m
    ok += 1


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return f.read()


# ----------------------------------------------------------------- 1. instalatory
inst, setup = read("install_bot.sh"), read("vps/setup_vps.sh")
PY = set(re.search(r'^PY="([^"]+)"', inst, re.M).group(1).split())
WEBF = set(re.search(r'^WEBF="([^"]+)"', inst, re.M).group(1).split())
setup_py = set(re.findall(r'\$SRC/(btc_[a-z_]+\.py)', setup))
setup_web = set(re.findall(r'\$SRC/([a-z_\.]+\.(?:html|css|js|svg))', setup))
for f in sorted(PY | WEBF):
    check(os.path.isfile(os.path.join(ROOT, f)), f"instalator wymienia nieistniejący plik {f}")
imports = {}
for f in PY:
    for m in re.findall(r'^\s*(?:import|from)\s+(btc_[a-z_]+)\b', read(f), re.M):
        imports.setdefault(m + ".py", set()).add(f)
missing = {m: sorted(u) for m, u in imports.items() if m not in PY and m not in setup_py}
check(not missing, f"moduły importowane, ale instalowane przez żaden skrypt: {missing}")
check("btc_deals.py" in PY and "btc_deals.py" in setup_py, "btc_deals.py jest w obu instalatorach (/api/deal i /api/today go importują)")
html = {f for f in os.listdir(ROOT) if f.endswith(".html")}
check(html <= WEBF | setup_web, f"strony, których żaden instalator nie wdraża: {sorted(html - WEBF - setup_web)}")
for f in ("zarabiaj.html", "alfred.html", "ui.js", "ui.css"):
    check(f in WEBF, f"{f} jest wdrażany przez install_bot.sh")

# ----------------------------------------------------------------- 2. nawigacja i przekierowania
ui = read("ui.js")
nav = ui[ui.index("const NAV"):ui.index("const THEMES")]
hrefs = re.findall(r"href: '([a-z_]+\.html)'", nav)
check(len(hrefs) >= 8 and hrefs[:3] == ["alfred.html", "zarabiaj.html", "bot.html"], f"nawigacja: Dziś, Zarabiaj, Bot na początku: {hrefs[:4]}")
for h in hrefs:
    check(os.path.isfile(os.path.join(ROOT, h)) and h in WEBF | setup_web, f"pozycja nawigacji {h} istnieje i jest wdrażana")
zar = read("zarabiaj.html")
for stub, tab in (("produkty.html", "znajdz"), ("mentor.html", "pomysly"), ("zarobki.html", "transakcje")):
    s = read(stub)
    check(f"zarabiaj.html#{tab}" in s and f'id="tab-{tab}"' in zar, f"{stub} przekierowuje na istniejącą zakładkę {tab}")
    check(len(s) < 1200 and "<script src" not in s, f"{stub} to tylko przekierowanie, bez własnej logiki")
for page in sorted(html):
    s = read(page)
    for ref in re.findall(r'(?:src|href)="([a-z_\.]+\.(?:js|css|svg))"', s):
        check(os.path.isfile(os.path.join(ROOT, ref)), f"{page}: brak pliku {ref}")
    for ref in re.findall(r'href="([a-z_]+\.html)(?:#[a-z]+)?"', s):
        check(os.path.isfile(os.path.join(ROOT, ref)), f"{page}: odnośnik do nieistniejącej strony {ref}")
check("zarabiaj.html#transakcje" in read("alfred.html"), "Dziś linkuje do potoku ofert")

# ----------------------------------------------------------------- 3. składnia skryptów
node = shutil.which("node")
if node:
    tmp = tempfile.mkdtemp()
    for page in sorted(html):
        for i, m in enumerate(re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", read(page), re.S)):
            p = os.path.join(tmp, f"{page}.{i}.js")
            with open(p, "w", encoding="utf-8") as f:
                f.write(m.group(1))
            r = subprocess.run([node, "--check", p], capture_output=True, text=True)
            check(r.returncode == 0, f"{page}: skrypt {i} ma błąd składni: {r.stderr[:300]}")
    r = subprocess.run([node, "--check", os.path.join(ROOT, "ui.js")], capture_output=True, text=True)
    check(r.returncode == 0, f"ui.js: błąd składni: {r.stderr[:300]}")

    # ----------------------------------------------------------------- 4. UD.merge i UD.sync na atrapie serwera
    a = ui.index("const UD = {")
    b = ui.index("\n};\n", a) + 4
    c = ui.index("UD.merge = ")
    d = ui.index("\n};\n", c) + 4
    code = ui[a:b] + ui[c:d]
    js = r"""
const assert = require('assert');
const make = new Function('sget', 'sset', 'fetch', %s + '\nreturn UD;');
let server = {ts: 0, value: null, down: false};
const fakeFetch = async (url, opt) => {
  if (server.down) throw new Error('offline');
  if (opt && opt.method === 'POST') {
    const body = JSON.parse(opt.body);
    if ((body.base_ts || 0) !== server.ts) return {status: 409, ok: false, json: async () => ({error: 'konflikt', ts: server.ts, value: server.value})};
    server = {...server, ts: server.ts + 1, value: body.value}; return {status: 200, ok: true, json: async () => ({ts: server.ts})};
  }
  return {status: 200, ok: true, json: async () => ({found: server.value != null, ts: server.ts, value: server.value})};
};
const client = () => { const ud = make(() => null, () => {}, fakeFetch); const h = {ud, state: [], applied: 0};
  h.sync = () => ud.sync('k', {get: () => h.state, merge: (b, l, r) => ud.merge(b, l, r, 't'), apply: v => { h.state = v; h.applied++; }}); return h; };
(async () => {
  const M = make(() => null, () => {}, fakeFetch).merge, a = {t: 1, v: 0}, b = {t: 2, v: 0}, c = {t: 3, v: 0}, ids = l => l.map(x => x.t).sort().join(',');
  assert.strictEqual(ids(M([a, b], [a, b, c], [a, b, {t: 4, v: 0}], 't')), '1,2,3,4', 'dodane po obu stronach');
  assert.strictEqual(ids(M([a, b], [a], [a, b], 't')), '1', 'usunięte u nas');
  assert.strictEqual(ids(M([a, b], [a, b], [a], 't')), '1', 'usunięte na serwerze');
  assert.strictEqual(ids(M([a, b], [a], [a, {...b, v: 9}], 't')), '1', 'usunięcie wygrywa ze zmianą po drugiej stronie');
  assert.strictEqual(JSON.stringify(M([a, b], [{...a, v: 1}, b], [a, {...b, v: 2}], 't').sort((x, y) => x.t - y.t)), '[{"t":1,"v":1},{"t":2,"v":2}]', 'zmiany różnych elementów');
  assert.strictEqual(M([a], [{...a, v: 1}], [{...a, v: 2}], 't')[0].v, 1, 'ten sam element: wygrywa nasza wersja');
  assert.strictEqual(M([a], [{...a, v: 1}], [{...a, v: 2}], 't', (l, r) => ({...l, v: l.v + r.v}))[0].v, 3, 'both łączy zmiany');
  assert.strictEqual(M([a], [a], [{...a, v: 5}], 't')[0].v, 5, 'nietknięty element bierze wersję serwera');
  assert.strictEqual(ids(M(null, [c], [a, b], 't')), '1,2,3', 'brak wersji bazowej: suma');
  const A = client(), B = client(); await A.ud.load('k'); await B.ud.load('k');
  A.state = [a]; let r = await A.sync(); assert.ok(r.ok && !r.merged && server.ts === 1, 'pierwszy zapis');
  B.state = [c]; r = await B.sync();
  assert.ok(r.ok && r.merged && B.applied === 1 && ids(server.value) === '1,3' && ids(B.state) === '1,3', 'nieaktualne urządzenie scala zamiast gubić zmiany: ' + JSON.stringify(r));
  A.state = A.state.map(x => ({...x, v: 7})); r = await A.sync();
  assert.ok(r.ok && r.merged && ids(server.value) === '1,3' && server.value.find(x => x.t === 1).v === 7, 'zmiana + cudze dodanie: ' + JSON.stringify(server.value));
  B.state = B.state.filter(x => x.t !== 3); r = await B.sync();
  assert.ok(r.ok && ids(server.value) === '1' && server.value[0].v === 7, 'usunięcie nie cofa cudzej zmiany: ' + JSON.stringify(server.value));
  const D = client(); D.state = []; const ts1 = server.ts; r = await D.sync();
  assert.ok(r.ok && r.merged && ids(server.value) === '1' && ids(D.state) === '1' && server.ts === ts1, 'urządzenie bez wczytanych danych nie nadpisuje serwera i nie robi zbędnego zapisu: ' + JSON.stringify([r, server]));
  const E = client(); await E.ud.load('k'); E.state = [...server.value]; const p1 = E.sync(); E.state = [...E.state, {t: 9, v: 0}]; const p2 = E.sync(); await Promise.all([p1, p2]);
  assert.ok(server.value.some(x => x.t === 9), 'szybkie kolejne zapisy: drugi zawiera późniejszą zmianę');
  const F = client(); F.ud._ts.k = 0; r = await F.ud.save('k', [{t: 5}]); assert.ok(!r.ok && r.conflict && ids(r.value) === ids(server.value), 'save bez scalania zwraca konflikt z wersją serwera');
  server.down = true; r = await E.sync(); assert.ok(!r.ok && r.offline, 'serwer niedostępny: offline');
  console.log('ok');
})().catch(e => { console.error(e.stack || e); process.exit(1); });
""" % json.dumps(code)
    p = os.path.join(tmp, "sync_test.js")
    with open(p, "w", encoding="utf-8") as f:
        f.write(js)
    r = subprocess.run([node, p], capture_output=True, text=True, timeout=60)
    check(r.returncode == 0 and r.stdout.strip() == "ok", f"UD.merge/UD.sync: {r.stderr[-900:] or r.stdout[-300:]}")
else:
    print("node niedostępny: pominięto kontrolę składni skryptów stron i test UD.sync")
print("pages ok", ok)
