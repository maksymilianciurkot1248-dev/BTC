#!/usr/bin/env python3
"""Analiza cudzych transakcji (Hyperliquid): czy kopiowanie portfela miałoby przewagę po kosztach.

Dane są publiczne (api.hyperliquid.xyz/info, bez klucza). Program niczego nie kopiuje i nie składa zleceń;
liczy tylko, jak wyszłoby kopiowanie, i podaje wynik w panelu (copy.json, /api/copy).

Ochrona przed błędem przeżywalności: ranking pokazuje tych, którym się udało, więc historia sprzed dodania
portfela NIE wystarcza do awansu. Status „kandydat” wymaga wyniku z transakcji zamkniętych PO dodaniu portfela
do obserwacji (wyniki poza próbą), testu na sumach tygodniowych i poprawki Benjaminiego-Hochberga wspólnej dla wszystkich portfeli.

Użycie:
  python btc_copy.py add 0xADRES [etykieta]    dodaje portfel do obserwacji
  python btc_copy.py remove 0xADRES
  python btc_copy.py leaderboard [--top 20]    dodaje najlepszych z rankingu miesiąca (do obserwacji, nie do kopiowania)
  python btc_copy.py run                       pobiera nowe transakcje i liczy statystyki (robi to też btc_learn co dobę)
"""
import json
import math
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_lab as lab  # noqa: E402  (t_test, week_sums)

API = "https://api.hyperliquid.xyz/info"
LEADERBOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
WALLETS = "copy_wallets.json"
DATA = "copy_data.json"
OUT = "copy.json"
VERSION = 1
ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
MAX_WALLETS = 60
MAX_FILLS = 6000                    # tyle ostatnich transakcji na portfel
PAGE_PAUSE_S = 0.35                 # limit zapytań giełdy
COPY_COST = 0.002                   # koszt kopiowania w ułamku nominału: 2×prowizja taker + opóźnienie i poślizg (0,2%)
MIN_FWD_TRADES = 30
MIN_FWD_WEEKS = 8
ALPHA = 0.05
MIN_MEAN = 0.001                    # średni wynik po kosztach na transakcję (0,1% nominału)
MAX_TOP_SHARE = 0.5                 # jedna transakcja nie może dawać więcej niż połowy zysku (inaczej to łut szczęścia)
DAY_MS = 86400000


# ----------------------------------------------------------------- sieć
def post(body, timeout=20):
    req = urllib.request.Request(API, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "User-Agent": "btc-copy"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "btc-copy"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_fills(addr, since_ms, post_fn=post, sleep=time.sleep):
    """Nowe transakcje portfela od since_ms, stronicowane po 2000 (API zwraca najstarsze pierwsze)."""
    out, start = [], int(since_ms)
    for _ in range(12):
        rows = post_fn({"type": "userFillsByTime", "user": addr, "startTime": start})
        if not isinstance(rows, list) or not rows:
            break
        out += rows
        last = max(int(r["time"]) for r in rows)
        if len(rows) < 2000 or last <= start:
            break
        start = last + 1
        sleep(PAGE_PAUSE_S)
    return out


def compact(f):
    """[czas_ms, moneta, kierunek, cena, ilość, strona(dir), closedPnl, opłata, id] — zapis oszczędny."""
    return [int(f["time"]), str(f["coin"]), str(f.get("dir", "")), float(f["px"]), float(f["sz"]), float(f.get("closedPnl") or 0), float(f.get("fee") or 0), str(f.get("tid", f.get("oid", "")))]


# ----------------------------------------------------------------- transakcje i statystyki
def round_trips(fills):
    """Zamknięcia pozycji → [(czas_zamknięcia_ms, moneta, strona, wynik_netto_w_ułamku_nominału, nominał_usd)].
    Cena wejścia wynika z closedPnl (Close Long: pnl = ilość×(wyjście−wejście); Close Short: odwrotnie), więc nie trzeba odtwarzać pozycji.
    Odwrócenia pozycji (Long > Short) pomijamy: closedPnl dotyczy tam tylko części ilości."""
    agg = {}
    for t, coin, dr, px, sz, pnl, fee, _id in fills:
        if dr not in ("Close Long", "Close Short") or sz <= 0:
            continue
        k = (coin, dr, t // 60000)                                  # częściowe wypełnienia w tej samej minucie = jedna transakcja
        a = agg.setdefault(k, [t, 0.0, 0.0, 0.0, 0.0])             # czas, ilość, ilość×cena, pnl, opłata
        a[0], a[1], a[2], a[3], a[4] = max(a[0], t), a[1] + sz, a[2] + sz * px, a[3] + pnl, a[4] + fee
    out = []
    for (coin, dr, _m), (t, sz, szpx, pnl, fee) in agg.items():
        exit_px = szpx / sz
        entry = exit_px - pnl / sz if dr == "Close Long" else exit_px + pnl / sz
        if entry <= 0:
            continue
        notional = sz * entry
        net = pnl - fee - fee * (entry / exit_px)                   # opłata wejścia szacowana jak wyjścia
        out.append((t, coin, "long" if dr == "Close Long" else "short", net / notional, notional))
    out.sort()
    return out


def bh(ps, alpha=ALPHA):
    """Poprawka Benjaminiego-Hochberga: p skorygowane w tej samej kolejności."""
    n = len(ps)
    order = sorted(range(n), key=lambda i: ps[i])
    adj, prev = [1.0] * n, 1.0
    for rank in range(n, 0, -1):
        i = order[rank - 1]
        prev = min(prev, ps[i] * n / rank)
        adj[i] = prev
    return adj


def _stats(trips, cost):
    rets = [r - cost for (_t, _c, _s, r, _n) in trips]
    if not rets:
        return {"n": 0, "mean": 0.0, "win": 0.0, "weeks": 0, "t": 0.0, "p": 1.0, "total": 0.0, "top_share": 0.0, "maxdd": 0.0}
    wk = lab.week_sums([(t[0], r) for t, r in zip(trips, rets)])
    m, t, p, nw = lab.t_test(list(wk.values()))
    gains = sum(r for r in rets if r > 0)
    cum = peak = dd = 0.0
    for r in rets:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return {"n": len(rets), "mean": round(sum(rets) / len(rets), 5), "win": round(sum(1 for r in rets if r > 0) / len(rets), 3), "weeks": nw,
            "t": round(t, 2), "p": round(p, 4), "total": round(sum(rets), 4), "top_share": round(max(rets) / gains, 3) if gains > 0 else 1.0, "maxdd": round(dd, 4)}


def evaluate(wallets, data, now_ms, cost=COPY_COST):
    """Statystyki każdego portfela: cała historia (informacyjnie) i część poza próbą (po dodaniu do obserwacji)."""
    rows = []
    for w in wallets:
        trips = round_trips(data.get(w["addr"], {}).get("fills", []))
        added = int(w.get("added_ms", now_ms))
        hist = _stats([t for t in trips if t[0] < added], cost)
        fwd = _stats([t for t in trips if t[0] >= added], cost)
        coins = {}
        for t in trips:
            coins[t[1]] = coins.get(t[1], 0) + 1
        rows.append({"addr": w["addr"], "label": w.get("label", ""), "source": w.get("source", "ręcznie"), "added_ms": added, "hist": hist, "fwd": fwd,
                     "coins": sorted(coins.items(), key=lambda x: -x[1])[:5], "last_ms": trips[-1][0] if trips else None})
    adj = bh([r["fwd"]["p"] for r in rows]) if rows else []
    for r, a in zip(rows, adj):
        f, h = r["fwd"], r["hist"]
        r["fwd"]["p_adj"] = round(a, 4)
        if h["n"] >= 50 and h["mean"] <= 0 and f["n"] < MIN_FWD_TRADES:
            r["status"], r["why"] = "odrzucony", f"historia ujemna po kosztach ({h['mean'] * 100:+.2f}% na transakcję)"
        elif f["n"] < MIN_FWD_TRADES or f["weeks"] < MIN_FWD_WEEKS:
            r["status"], r["why"] = "obserwacja", f"za mało transakcji poza próbą ({f['n']}/{MIN_FWD_TRADES}, tygodni {f['weeks']}/{MIN_FWD_WEEKS})"
        elif f["top_share"] > MAX_TOP_SHARE and f["total"] > 0:
            r["status"], r["why"] = "obserwacja", f"jedna transakcja daje {f['top_share'] * 100:.0f}% zysku (łut szczęścia?)"
        elif a <= ALPHA and f["mean"] >= MIN_MEAN:
            r["status"], r["why"] = "kandydat", f"poza próbą {f['mean'] * 100:+.2f}% na transakcję po kosztach, p_skoryg {a:.3f}"
        else:
            r["status"], r["why"] = "obserwacja", f"brak istotnej przewagi poza próbą (p_skoryg {a:.2f}, średnia {f['mean'] * 100:+.2f}%)"
    rows.sort(key=lambda r: ({"kandydat": 0, "obserwacja": 1, "odrzucony": 2}[r["status"]], r["fwd"]["p_adj"]))
    return rows


# ----------------------------------------------------------------- pliki
def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def _write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def read_wallets(d):
    return _read(os.path.join(d, WALLETS), {}).get("wallets") or []


def add_wallet(d, addr, label="", source="ręcznie", now_ms=None):
    addr = addr.strip().lower()
    if not ADDR_RE.match(addr):
        raise ValueError("adres musi mieć postać 0x + 40 znaków szesnastkowych")
    ws = read_wallets(d)
    if any(w["addr"] == addr for w in ws):
        return False
    if len(ws) >= MAX_WALLETS:
        raise ValueError(f"limit {MAX_WALLETS} portfeli")
    ws.append({"addr": addr, "label": label[:40], "source": source, "added_ms": int(now_ms if now_ms is not None else time.time() * 1000)})
    _write(os.path.join(d, WALLETS), {"wallets": ws})
    return True


def remove_wallet(d, addr):
    addr = addr.strip().lower()
    ws = read_wallets(d)
    new = [w for w in ws if w["addr"] != addr]
    _write(os.path.join(d, WALLETS), {"wallets": new})
    return len(new) != len(ws)


def top_from_leaderboard(rows, n=20, min_value=20000.0, min_month_vlm=200000.0):
    """Najlepsi z miesiąca według ROI, z odsiewem małych kont i bardzo małych obrotów (łatwo o wynik z jednej szczęśliwej transakcji)."""
    out = []
    for r in rows:
        try:
            perf = {k: v for k, v in r.get("windowPerformances", [])}
            m = perf.get("month") or {}
            if float(r.get("accountValue", 0)) >= min_value and float(m.get("vlm", 0)) >= min_month_vlm and float(m.get("pnl", 0)) > 0:
                out.append((float(m.get("roi", 0)), r["ethAddress"], r.get("displayName") or ""))
        except Exception:  # noqa: BLE001
            continue
    out.sort(reverse=True)
    return out[:n]


def read(d):
    return _read(os.path.join(d, OUT), {"ts": 0, "wallets": [], "version": VERSION})


def run(d, now_ms=None, post_fn=post, sleep=time.sleep):
    """Pobiera nowe transakcje, liczy statystyki, zapisuje copy.json. Zwraca (wynik, [(rodzaj, komunikat)])."""
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    ws = read_wallets(d)
    msgs = []
    if not ws:
        return None, msgs
    path = os.path.join(d, DATA)
    data = _read(path, {})
    errs = 0
    for w in ws:
        cur = data.setdefault(w["addr"], {"fills": []})
        last = max((f[0] for f in cur["fills"]), default=int(w.get("added_ms", now_ms)) - 90 * DAY_MS)   # przy pierwszym pobraniu bierzemy 90 dni wstecz (historia, tylko informacyjnie)
        try:
            new = [compact(f) for f in fetch_fills(w["addr"], last + 1, post_fn, sleep)]
            known = {f[7] for f in cur["fills"]}
            cur["fills"] = (cur["fills"] + [f for f in new if f[7] not in known])[-MAX_FILLS:]
            sleep(PAGE_PAUSE_S)
        except Exception as e:  # noqa: BLE001
            errs += 1
            msgs.append(("alert", f"copy {w['addr'][:8]}…: {type(e).__name__} {str(e)[:60]}"))
    _write(path, data)
    rows = evaluate(ws, data, now_ms)
    res = {"ts": now_ms / 1000, "version": VERSION, "cost": COPY_COST, "wallets": rows,
           "criteria": {"min_fwd_trades": MIN_FWD_TRADES, "min_fwd_weeks": MIN_FWD_WEEKS, "alpha": ALPHA, "min_mean": MIN_MEAN, "max_top_share": MAX_TOP_SHARE}}
    _write(os.path.join(d, OUT), res)
    cand = [r for r in rows if r["status"] == "kandydat"]
    msgs.append(("info", f"Kopiowanie: {len(rows)} portfeli, kandydaci poza próbą: {len(cand)}" + (f" ({', '.join(r['addr'][:8] + '…' for r in cand[:4])})" if cand else "") + (f", błędy pobierania: {errs}" if errs else "")))
    return res, msgs


def main(argv):
    d = os.environ.get("BTC_BOT_DIR", HERE)
    cmd = argv[1] if len(argv) > 1 else "run"
    if cmd == "add" and len(argv) > 2:
        print("dodano" if add_wallet(d, argv[2], " ".join(argv[3:])) else "już jest")
    elif cmd == "remove" and len(argv) > 2:
        print("usunięto" if remove_wallet(d, argv[2]) else "nie ma takiego")
    elif cmd == "leaderboard":
        n = int(argv[argv.index("--top") + 1]) if "--top" in argv else 20
        added = 0
        for roi, addr, name in top_from_leaderboard(get_json(LEADERBOARD).get("leaderboardRows", []), n):
            added += bool(add_wallet(d, addr, name or "ranking", "ranking miesiąca"))
        print(f"dodano {added} portfeli do obserwacji (nie do kopiowania: potrzebują wyniku poza próbą)")
    elif cmd == "run":
        res, msgs = run(d)
        for k, m in msgs:
            print(k, m)
        for r in (res or {}).get("wallets", []):
            print(f"{r['addr'][:10]}… {r['status']:10} hist n={r['hist']['n']} poza próbą n={r['fwd']['n']} — {r['why']}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv)
