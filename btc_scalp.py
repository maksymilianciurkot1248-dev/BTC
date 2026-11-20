#!/usr/bin/env python3
"""Bot do scalpingu (krótkie transakcje na świecach 5 min), osobna usługa obok bota dziennego.

Trzy strategie działają równolegle W CIENIU (symulacja na żywych danych, wynik po prowizjach i poślizgu):
  mr  powrót do średniej   skrajne odchylenie ceny od średniej z 40 świec + RSI; cel: powrót do średniej
  bo  wybicie              wyjście z wąskiego zakresu 36 świec przy obrocie > 1,8 średniej; cel 2R
  ob  nierównowaga zleceń  przewaga jednej strony arkusza zleceń w dwóch kolejnych odczytach, zgodna ze świecą; cel 1,5R
  fl  przepływ (12 h)      wskaźnik łączny z danych o przepływie i pozycjonowaniu (btc_flow.py): wolumen agresywny, konta long/short,
                           premia kontraktu, ruch z 4 h. Wejście przy |wskaźnik| >= 1, stop 3 x ATR godzinowy, cel 2R, wyjście po 12 h.
                           Eksperyment bez potwierdzonej skuteczności (opis badań w btc_flow.py): wynik liczy się tylko od wdrożenia.
Zlecenia składa tylko strategia, która w cieniu wychodzi na plus z zapasem na przypadek. Bramka (ostatnie do 300 transakcji z 30 dni):
>= 30 transakcji, średnia >= +0,05R i t >= 2 (t liczone tak, że transakcje zamknięte w tej samej godzinie są jedną obserwacją, bo
aktywa poruszają się razem). Otwarta bramka zamyka się dopiero, gdy średnia spadnie poniżej zera (histereza, bez migotania).
Sama średnia nie wystarcza: na historii strategia tracąca 0,36R na transakcję miała średnią >= +0,05R w 17% okien po 30 transakcji,
a z warunkiem t >= 2 w 0,7% (docs/badania_bramka).
Hamulec: strategia, której PRAWDZIWE transakcje są istotnie ujemne (>= 10 transakcji, t <= −2), nie składa zleceń mimo dodatniego cienia.
Na demo/testnecie, gdy żadna nie przechodzi bramki, bot handluje najlepszą z połową ryzyka („rozpoznanie”, SCALP_EXPLORE=no wyłącza),
ale tylko taką, która w cieniu nie traci wyraźnie (średnia >= −0,10R): strategiami, o których wiemy, że tracą, bot nie handluje wcale.
Wymuszona aktywność (tylko demo i testnet, SCALP_MIN_TRADES_DAY, domyślnie 2): gdy dziś było mniej transakcji, niż wynosi minimum, scalper bierze świeży sygnał
strategii z najlepszą średnią w cieniu, z połową ryzyka, także gdy bramka, weto pary i hamulec by go zatrzymały. To celowo handel strategiami bez dowodu,
żeby na demo zbierać prawdziwe wykonanie i wyniki; oczekiwana strata to ok. 0,04% kapitału na transakcję. Limity konta, pauza, STOP i dzienny limit w R nadal obowiązują.
Na koncie live bot składa zlecenia wyłącznie przez bramkę i tylko z SCALP_CONFIRM_LIVE=I_ACCEPT_THE_RISK (wymuszona aktywność wyłączona).
Limity konta są wspólne z botem dziennym: dźwignia i kapitał z panelu, pauza, wyłączniki (strata dzienna, tygodniowa, spadek kapitału).

Prowizje są głównym przeciwnikiem: wejście i wyjście rynkowe to ok. 0,13% nominału, dlatego stop ma minimum 0,30% ceny
(koszt <= 0,43R na transakcję). Każda pozycja ma stop-loss i cel ustawione na giełdzie w chwili wejścia oraz limit czasu.

Ustawienia (trader.env): SCALP_SYMBOLS (domyślnie BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT), SCALP_RISK_PCT (0.2),
SCALP_MAX_POSITIONS (3), SCALP_MAX_TRADES_DAY (30), SCALP_MAX_DAILY_LOSS_R (5), SCALP_MAX_NOTIONAL_X (1.0), SCALP_EXPLORE (yes).
Tryb i klucze: te same co bot dzienny (TRADER_MODE). Włącznik: panel, zakładka Bot (plik scalp_settings.json).
Użycie: python btc_scalp.py   (usługa btc-scalp)   |   python btc_scalp.py --once   (jeden obrót, do testów)
"""
import fcntl
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_botcfg as bb  # noqa: E402
import btc_flow as bf  # noqa: E402  (dane o przepływie i pozycjonowaniu, wskaźnik łączny)
import btc_trader as bt  # noqa: E402  (klient z podpisem, konfiguracja trybu i kluczy, powiadomienia)

BAR_S = 300
STRATS = {"mr": "Powrót do średniej", "bo": "Wybicie", "ob": "Nierównowaga zleceń", "fl": "Przepływ i pozycjonowanie (12 h)"}
FL_HOLD, FL_ATR_MULT, FL_TP = 144, 3.0, 2.0      # 12 h w świecach 5 min, stop w ATR godzinowych, cel w R
COST_PCT = 0.0013                 # wejście + wyjście rynkowe (2 × 0,055%) + poślizg 2 × 0,01%
MIN_STOP_PCT = 0.0030
GATE_MIN_N, GATE_MIN_MEAN, GATE_MIN_T, GATE_DAYS, GATE_LAST = 30, 0.05, 2.0, 30, 300
GATE_OFF_MEAN = 0.0               # otwarta bramka zamyka się, gdy średnia z cienia spadnie poniżej tej wartości
T_BLOCK_S = 3600                  # transakcje zamknięte w tej samej godzinie to jedna obserwacja (skupiska)
BRAKE_MIN_N, BRAKE_T, BRAKE_DAYS, BRAKE_LAST = 10, -2.0, 30, 100      # hamulec z prawdziwych transakcji
CLOSE_WAIT_S = 180                # tyle czekamy, aż giełda poda wynik zamkniętej pozycji
GAP_BARS = 3                      # dłuższa przerwa w świecach: otwarte transakcje z cienia zamykamy bez wyniku
VETO_MIN_N, VETO_MEAN = 20, -0.10
EXPLORE_MIN_N = 10
EXPLORE_MIN_MEAN = -0.10          # rozpoznanie tylko strategią, która w cieniu nie traci wyraźnie
SEED_PAGES = 3                    # 3 × 1000 świec 5 min = ok. 10 dni historii na start
MAX_SHADOW = 8000
COOLDOWN_S = 900                  # przerwa na aktywie po stracie
EXPLORE_MAX_TRADES = 6             # w rozpoznaniu (strategia bez dowodu) najwyżej tyle transakcji na dobę
STATE, OUT, SETTINGS, LEDGER = "scalp_state.json", "scalp.json", "scalp_settings.json", "scalp_trades.jsonl"
DEFAULT_SYMBOLS = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT"


# ----------------------------------------------------------------- wskaźniki i sygnały (czysta logika)
def atr(bars, i, n=14):
    if i < n:
        return None
    s = 0.0
    for k in range(i - n + 1, i + 1):
        h, l, pc = bars[k][2], bars[k][3], bars[k - 1][4]
        s += max(h - l, abs(h - pc), abs(l - pc))
    return s / n


def rsi(bars, i, n=6):
    if i < n:
        return None
    up = dn = 0.0
    for k in range(i - n + 1, i + 1):
        d = bars[k][4] - bars[k - 1][4]
        up, dn = up + max(d, 0), dn + max(-d, 0)
    return 100.0 if dn == 0 else 100 - 100 / (1 + up / dn)


def _plan(side, price, stop_dist, tp_dist, hold):
    stop_dist = max(stop_dist, MIN_STOP_PCT * price)
    return {"side": side, "stop_dist": stop_dist, "tp_dist": max(tp_dist, stop_dist), "hold": hold}


def sig_mr(bars, i):
    n = 40
    a = atr(bars, i)
    if i < n or not a:
        return None
    cl = [b[4] for b in bars[i - n + 1:i + 1]]
    m = sum(cl) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in cl) / n)
    c, r = bars[i][4], rsi(bars, i)
    if sd <= 0 or r is None:
        return None
    z = (c - m) / sd
    side = 1 if (z < -2.2 and r < 22) else -1 if (z > 2.2 and r > 78) else 0
    if not side:
        return None
    stop = max(1.5 * a, MIN_STOP_PCT * c)
    if abs(c - m) < stop:                      # średnia za blisko: zysk nie pokryje kosztów
        return None
    return _plan(side, c, stop, min(abs(c - m), 2 * stop), 24)


def sig_bo(bars, i):
    n = 36
    a = atr(bars, i)
    if i < n + 1 or not a:
        return None
    win = bars[i - n:i]
    hh, ll = max(b[2] for b in win), min(b[3] for b in win)
    av = sum(b[5] for b in win) / n
    c, v = bars[i][4], bars[i][5]
    if hh - ll > 6 * a or av <= 0 or v < 1.8 * av:
        return None
    side = 1 if c > hh else -1 if c < ll else 0
    if not side:
        return None
    stop = max(1.2 * a, MIN_STOP_PCT * c)
    return _plan(side, c, stop, 2 * stop, 36)


def sig_ob(bars, i, imb=None):
    """imb: ostatnie odczyty nierównowagi arkusza (−1..1). Tylko na żywo: historii arkusza nie ma."""
    a = atr(bars, i)
    if not imb or len(imb) < 2 or i < 20 or not a:
        return None
    x = (imb[-1] + imb[-2]) / 2
    o, c = bars[i][1], bars[i][4]
    m = sum(b[4] for b in bars[i - 19:i + 1]) / 20
    side = 1 if (x > 0.35 and min(imb[-2:]) > 0.2 and c > o and c > m) else -1 if (x < -0.35 and max(imb[-2:]) < -0.2 and c < o and c < m) else 0
    if not side:
        return None
    stop = max(1.0 * a, MIN_STOP_PCT * c)
    return _plan(side, c, stop, 1.5 * stop, 12)


def atr_hour(bars, i, n=14):
    """ATR ze świec godzinowych złożonych ze świec 5 min (pełne godziny przed świecą i)."""
    hrs = {}
    for b in bars[max(0, i - (n + 2) * 12):i + 1]:
        h = hrs.setdefault(b[0] // 3600, [b[2], b[3], b[4], 0])
        h[0], h[1], h[2], h[3] = max(h[0], b[2]), min(h[1], b[3]), b[4], h[3] + 1
    full = [v for k, v in sorted(hrs.items()) if v[3] == 12]
    if len(full) < n + 1:
        return None
    full = full[-(n + 1):]
    return sum(max(full[k][0] - full[k][1], abs(full[k][0] - full[k - 1][2]), abs(full[k][1] - full[k - 1][2])) for k in range(1, n + 1)) / n


def sig_fl(bars, i, lean=None):
    """lean: wskaźnik łączny przepływu aktywa (btc_flow) znany w chwili zamknięcia świecy. Tylko na żywo."""
    if lean is None or abs(lean) < bf.THETA:
        return None
    a = atr_hour(bars, i)
    if not a:
        return None
    c = bars[i][4]
    stop = max(FL_ATR_MULT * a, 2 * MIN_STOP_PCT * c)
    return _plan(1 if lean > 0 else -1, c, stop, FL_TP * stop, FL_HOLD)


def signals(bars, i, imb=None, lean=None):
    out = {}
    for name, fn in (("mr", sig_mr), ("bo", sig_bo)):
        p = fn(bars, i)
        if p:
            out[name] = p
    p = sig_ob(bars, i, imb)
    if p:
        out["ob"] = p
    p = sig_fl(bars, i, lean)
    if p:
        out["fl"] = p
    return out


def book_imbalance(book, depth_pct=0.001):
    """(wolumen kupna − sprzedaży) / suma, w odległości depth_pct od środka arkusza."""
    try:
        bids, asks = [(float(p), float(q)) for p, q in book["b"]], [(float(p), float(q)) for p, q in book["a"]]
        mid = (bids[0][0] + asks[0][0]) / 2
        bv = sum(q for p, q in bids if p >= mid * (1 - depth_pct))
        av = sum(q for p, q in asks if p <= mid * (1 + depth_pct))
        return (bv - av) / (bv + av) if bv + av > 0 else 0.0
    except (KeyError, IndexError, ValueError, TypeError):
        return None


# ----------------------------------------------------------------- cień: symulacja po kosztach
def open_shadow(strat, sym, bar, plan):
    e, sg = bar[4], plan["side"]
    return {"strat": strat, "sym": sym, "side": sg, "entry": e, "stop": e - sg * plan["stop_dist"], "tp": e + sg * plan["tp_dist"],
            "sd": plan["stop_dist"], "ts": bar[0], "left": plan["hold"]}


def step_shadow(t, bar):
    """Jedna świeca dla otwartej transakcji z cienia. Zwraca wynik w R po kosztach albo None. Gdy świeca obejmuje stop i cel, liczymy stop."""
    sg, cost = t["side"], COST_PCT * t["entry"] / t["sd"]
    hi, lo = bar[2], bar[3]
    if (sg == 1 and lo <= t["stop"]) or (sg == -1 and hi >= t["stop"]):
        return -1.0 - cost
    if (sg == 1 and hi >= t["tp"]) or (sg == -1 and lo <= t["tp"]):
        return abs(t["tp"] - t["entry"]) / t["sd"] - cost
    t["left"] -= 1
    if t["left"] <= 0:
        return (bar[4] - t["entry"]) * sg / t["sd"] - cost
    return None


def feed(sh, sym, bars, imb=None, live_from=None, lean=None):
    """Przetwarza nowe zamknięte świece aktywa: zamyka i otwiera transakcje z cienia. Zwraca sygnały z OSTATNIEJ świecy (do handlu)."""
    last = sh["last"].get(sym, 0)
    fresh = {}
    first = next((b[0] for b in bars if b[0] > last), None)
    if last and first and first - last > GAP_BARS * BAR_S:       # przerwa w danych (postój usługi): tych transakcji nie da się uczciwie rozliczyć
        for strat in STRATS:
            sh["open"].pop(strat + ":" + sym, None)
    for i, bar in enumerate(bars):
        if bar[0] <= last:
            continue
        for strat in STRATS:
            k = strat + ":" + sym
            t = sh["open"].get(k)
            if t:
                r = step_shadow(t, bar)
                if r is not None:
                    sh["trades"].append([bar[0] + BAR_S, strat, sym, t["side"], round(r, 3)])
                    del sh["open"][k]
        is_last = i == len(bars) - 1
        live = is_last and (live_from is None or bar[0] >= live_from)
        sig = signals(bars, i, imb if live else None, lean if live else None)
        for strat, plan in sig.items():
            k = strat + ":" + sym
            if k not in sh["open"]:
                sh["open"][k] = open_shadow(strat, sym, bar, plan)
        if is_last:
            fresh = sig
        sh["last"][sym] = bar[0]
    if len(sh["trades"]) > MAX_SHADOW:
        sh["trades"] = sh["trades"][-MAX_SHADOW:]
    return fresh


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def tstat(rows, block_s=T_BLOCK_S):
    """t średniej wyniku odporne na skupiska: rows = [(czas zamknięcia, R)]; transakcje z tej samej godziny to jedna obserwacja.
    t = średnia × n / sqrt(suma kwadratów sum odchyleń w blokach × G/(G−1)), G = liczba bloków."""
    n = len(rows)
    if n < 2:
        return 0.0
    m = sum(r for _, r in rows) / n
    blocks = {}
    for ts, r in rows:
        blocks[int(ts) // block_s] = blocks.get(int(ts) // block_s, 0.0) + (r - m)
    g = len(blocks)
    s2 = sum(v * v for v in blocks.values()) * (g / (g - 1) if g > 1 else 1.0)
    return m * n / math.sqrt(s2) if g > 1 and s2 > 0 else 0.0


def stats(trades, now_s):
    """Wyniki cienia: na strategię i na parę strategia × aktywo."""
    lo = now_s - GATE_DAYS * 86400
    out = {}
    for strat in STRATS:
        rs_all = [t for t in trades if t[1] == strat]
        win = [t for t in rs_all if t[0] >= lo][-GATE_LAST:]
        rs = [t[4] for t in win]
        d1 = [t[4] for t in rs_all if t[0] >= now_s - 86400]
        pairs = {}
        for t in rs_all:
            if t[0] >= lo:
                pairs.setdefault(t[2], []).append(t[4])
        out[strat] = {"name": STRATS[strat], "n": len(rs), "mean_r": round(_mean(rs), 3), "win": round(sum(1 for r in rs if r > 0) / len(rs), 3) if rs else 0.0,
                      "total_r": round(sum(rs), 2), "t": round(tstat([(t[0], t[4]) for t in win]), 2), "d1_n": len(d1), "d1_r": round(sum(d1), 2),
                      "pairs": {s: {"n": len(v), "mean_r": round(_mean(v), 3)} for s, v in sorted(pairs.items())}}
    return out


def gate(st, explore, mem=None, banned=()):
    """Które strategie mogą składać zlecenia: ({strategia: mnożnik ryzyka}, tryb).
    Bramka otwiera się przy n >= GATE_MIN_N, średniej >= GATE_MIN_MEAN i t >= GATE_MIN_T; zostaje otwarta, dopóki średnia >= GATE_OFF_MEAN
    (mem: pamięć otwartych bramek, zmieniana w miejscu). banned: strategie zatrzymane hamulcem z prawdziwych transakcji.
    Gdy żadna nie przechodzi, a explore (demo/testnet): najlepsza nietracąca wyraźnie strategia z połową ryzyka („rozpoznanie”)."""
    mem = mem if mem is not None else {}
    for s, v in st.items():
        enough = v["n"] >= GATE_MIN_N
        if enough and v["mean_r"] >= GATE_MIN_MEAN and v.get("t", 0.0) >= GATE_MIN_T:
            mem[s] = 1
        elif not (enough and v["mean_r"] >= GATE_OFF_MEAN):
            mem.pop(s, None)
    ok = {s: 1.0 for s in st if mem.get(s) and s not in banned}
    if ok or not explore:
        return ok, "bramka" if ok else "brak"
    cand = [(v["mean_r"], s) for s, v in st.items() if v["n"] >= EXPLORE_MIN_N and v["mean_r"] >= EXPLORE_MIN_MEAN and s not in banned]
    return ({max(cand)[1]: 0.5}, "rozpoznanie") if cand else ({}, "brak")


def real_brake(led, now_s):
    """Hamulec z prawdziwych transakcji: {strategia: {n, mean_r, t}} dla strategii, których realne wyniki są istotnie ujemne.
    Cień nie widzi poślizgu, odrzuconych zleceń ani opóźnień, więc prawdziwy wynik ma pierwszeństwo przed symulacją."""
    out, lo = {}, now_s - BRAKE_DAYS * 86400
    for strat in STRATS:
        rows = [(x.get("ts", 0), x["r"]) for x in led if x.get("strat") == strat and x.get("r") is not None and x.get("ts", 0) >= lo][-BRAKE_LAST:]
        if len(rows) >= BRAKE_MIN_N:
            m, t = _mean([r for _, r in rows]), tstat(rows)
            if m < 0 and t <= BRAKE_T:
                out[strat] = {"n": len(rows), "mean_r": round(m, 3), "t": round(t, 2)}
    return out


def vetoed(st, strat, sym):
    p = st[strat]["pairs"].get(sym)
    return bool(p and p["n"] >= VETO_MIN_N and p["mean_r"] < VETO_MEAN)


# ----------------------------------------------------------------- pliki
def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, obj):
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(path + ".tmp", path)


def load_state(d):
    s = _read(os.path.join(d, STATE), {})
    s.setdefault("shadow", {})
    for k, v in (("open", {}), ("trades", []), ("last", {})):
        s["shadow"].setdefault(k, v)
    s.setdefault("live", {})
    for k, v in (("open", {}), ("cool", {}), ("day", ""), ("day_r", 0.0), ("day_n", 0)):
        s["live"].setdefault(k, v)
    for k in ("ob", "ob_t", "gate_on"):
        s.setdefault(k, {})
    s["shadow"].setdefault("fl_t", {})
    s.setdefault("seeded", [])
    return s


def read_settings(d):
    s = _read(os.path.join(d, SETTINGS), {})
    return {"enabled": bool(s.get("enabled", True))}


def write_settings(d, body):
    if not isinstance(body.get("enabled"), bool):
        raise ValueError("enabled musi być true albo false")
    _write(os.path.join(d, SETTINGS), {"enabled": body["enabled"]})
    return read_settings(d)


def read_out(d):
    return _read(os.path.join(d, OUT), {"ts": 0})


def read_ledger(d, n=600):
    """Ostatnie n prawdziwych transakcji scalpera. Uszkodzona linia jest pomijana (nie gubimy reszty dziennika)."""
    out = []
    try:
        with open(os.path.join(d, LEDGER), encoding="utf-8") as f:
            for x in f.readlines()[-n:]:
                try:
                    row = json.loads(x)
                    if isinstance(row, dict):
                        out.append(row)
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def open_symbols(d):
    """Aktywa z otwartą pozycją scalpera (bot dzienny nie liczy ich do swojego limitu pozycji)."""
    return set((_read(os.path.join(d, STATE), {}).get("live") or {}).get("open") or {})


# ----------------------------------------------------------------- konfiguracja
def scalp_cfg(c, e=os.environ.get):
    syms = [x.strip().upper() for x in (e("SCALP_SYMBOLS") or DEFAULT_SYMBOLS).split(",") if x.strip()]
    if not syms or len(syms) > 8 or any(not bb.SYMBOL_RE.match(x) for x in syms):
        sys.exit("SCALP_SYMBOLS: 1-8 symboli typu BTCUSDT, oddzielonych przecinkami")
    sc = {"symbols": syms, "risk_pct": float(e("SCALP_RISK_PCT") or 0.2), "max_pos": int(e("SCALP_MAX_POSITIONS") or 3), "max_trades": int(e("SCALP_MAX_TRADES_DAY") or 30),
          "max_loss_r": float(e("SCALP_MAX_DAILY_LOSS_R") or 5), "max_x": float(e("SCALP_MAX_NOTIONAL_X") or 1.0),
          "explore": (e("SCALP_EXPLORE") or "yes").lower() != "no" and c["mode"] in ("demo", "testnet")}
    sc["min_day"] = max(0, min(10, int(e("SCALP_MIN_TRADES_DAY") or 2))) if c["mode"] in ("demo", "testnet") else 0
    if not (0 < sc["risk_pct"] <= 0.5):
        sys.exit("SCALP_RISK_PCT musi być w (0, 0.5]")
    if not (0 < sc["max_x"] <= c["max_lev"]):
        sys.exit(f"SCALP_MAX_NOTIONAL_X musi być w (0, {c['max_lev']:g}] (limit dźwigni bota)")
    sc["can_trade"] = c["mode"] in ("demo", "testnet") or (c["mode"] == "live" and e("SCALP_CONFIRM_LIVE") == "I_ACCEPT_THE_RISK")
    return sc


def account_limits(c, d):
    """Limity konta z panelu (dźwignia, kapitał) obowiązują też scalpera: te same reguły co u bota dziennego
    (w trybie live panel może je tylko zmniejszyć). Bez tego bot dzienny uznałby pozycję scalpera za niezgodną z zasadami."""
    e = dict(c)
    try:
        bb.apply_settings(e, bb.read_settings(d))
    except Exception:  # noqa: BLE001 — uszkodzone ustawienia nie mogą zatrzymać scalpera
        return c
    return {**c, "max_lev": min(e["max_lev"], c["max_lev"]) if c["mode"] == "live" else e["max_lev"], "max_capital": e["max_capital"]}


def account_block(d, now_s):
    """Wyłączniki konta wyliczane przez bota dziennego (strata dzienna, tygodniowa, spadek kapitału) zatrzymują też scalpera."""
    risk = bb.read_risk(d)
    if now_s - (risk.get("ts") or 0) < 1800:
        for b in risk.get("breakers") or []:
            if b.get("key") in ("weekly", "drawdown"):
                return "wyłącznik konta: " + str(b.get("label") or b.get("key"))
    st = bb.read_status(d)
    if st.get("halted") and now_s - (st.get("ts") or 0) < 1800 and time.strftime("%Y-%m-%d", time.gmtime(st.get("ts") or 0)) == time.strftime("%Y-%m-%d", time.gmtime(now_s)):
        return "dzienny limit straty konta osiągnięty: przerwa do końca dnia UTC"
    return None


# ----------------------------------------------------------------- dane rynkowe
def fetch_bars(pub, sym, now_s, pages=1):
    """Zamknięte świece 5 min, od najstarszej: [ts_s, o, h, l, c, obrót]."""
    out, end = {}, None
    for _ in range(pages):
        p = {"category": "linear", "symbol": sym, "interval": "5", "limit": 1000 if pages > 1 else 300}
        if end:
            p["end"] = end
        rows = pub._request("/v5/market/kline", p)["list"]
        if not rows:
            break
        for r in rows:
            out[int(r[0]) // 1000] = [int(r[0]) // 1000, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
        end = min(int(r[0]) for r in rows) - 1
    return [b for t, b in sorted(out.items()) if t + BAR_S <= now_s]


# ----------------------------------------------------------------- zlecenia
class Live:
    def __init__(self, d, c, sc, api, pub):
        self.d, self.c, self.sc, self.api, self.pub, self.inst = d, c, sc, api, pub, {}

    def instrument(self, sym):
        if sym not in self.inst:
            i = self.pub._request("/v5/market/instruments-info", {"category": "linear", "symbol": sym})["list"][0]
            self.inst[sym] = (float(i["lotSizeFilter"]["qtyStep"]), float(i["lotSizeFilter"]["minOrderQty"]), float(i["priceFilter"]["tickSize"]))
        return self.inst[sym]

    def account(self):
        eq = float(self.api.ok("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": "USDT"})["list"][0]["totalEquity"])
        pos = [p for p in self.api.ok("GET", "/v5/position/list", {"category": "linear", "settleCoin": "USDT"})["list"] if float(p["size"]) > 0]
        return (min(eq, self.c["max_capital"]) if self.c["max_capital"] else eq), pos

    def reconcile(self, lv, pos, now_s):
        """Zamknięte pozycje -> dziennik z wynikiem w R; przekroczony limit czasu -> zamknięcie rynkowe."""
        by = {p["symbol"]: p for p in pos}
        for sym, t in list(lv["open"].items()):
            p = by.get(sym)
            if p is None:
                if now_s - t["ts"] < 20:            # zlecenie jeszcze się rozlicza
                    continue
                rows = self.api.ok("GET", "/v5/position/closed-pnl", {"category": "linear", "symbol": sym, "startTime": int(t["ts"] * 1000) - 5000, "limit": 20})["list"]
                if not rows and now_s - t.setdefault("gone", now_s) < CLOSE_WAIT_S:
                    continue                         # giełda podaje wynik z opóźnieniem: bez niego zgubilibyśmy stratę w dziennym limicie
                pnl = sum(float(x["closedPnl"]) for x in rows)
                r = pnl / (t["qty"] * t["sd"]) if rows else None
                row = {"ts": int(now_s), "sym": sym, "strat": t["strat"], "side": t["side"], "qty": t["qty"], "entry": t["entry"], "pnl": round(pnl, 4), "r": round(r, 3) if r is not None else None,
                       "held_s": int(now_s - t["ts"]), "mode": self.c["mode"], "explore": t.get("explore", False), "forced": t.get("forced", False)}
                with open(os.path.join(self.d, LEDGER), "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                lv["day_r"] += r or 0.0
                if (r or 0) < 0:
                    lv["cool"][sym] = now_s + COOLDOWN_S
                bt.notify(f"[scalp {sym}] zamknięto {t['strat']}: {pnl:+.2f} USDT ({(r or 0):+.2f}R)" if rows else f"[scalp {sym}] {t['strat']}: pozycja zniknęła bez wyniku z giełdy (zlecenie niewypełnione albo brak danych)", quiet=True)
                del lv["open"][sym]
            elif now_s - t["ts"] > t["hold_s"]:
                t.pop("gone", None)
                self.api.ok("POST", "/v5/order/create", {"category": "linear", "symbol": sym, "side": "Sell" if p["side"] == "Buy" else "Buy", "orderType": "Market",
                                                         "qty": p["size"], "reduceOnly": True, "positionIdx": 0})
                t["hold_s"] += 120                   # nie ponawiaj co obrót; rozliczenie przy następnym odczycie
                bt.log(f"[scalp {sym}] limit czasu: zamknięcie rynkowe")

    def blocked(self, lv, st_bot, now_s):
        if not self.sc["can_trade"]:
            return "tryb bez zleceń (dryrun albo live bez SCALP_CONFIRM_LIVE)"
        if not read_settings(self.d)["enabled"]:
            return "wyłączony w panelu"
        if st_bot.get("paused"):
            return "bot wstrzymany w panelu (pauza)"
        if os.path.exists(os.path.join(self.d, "STOP")):
            return "plik STOP (wyłącznik awaryjny)"
        acct = account_block(self.d, now_s)
        if acct:
            return acct
        if lv["day_n"] >= self.sc["max_trades"]:
            return f"limit {self.sc['max_trades']} transakcji na dobę"
        if lv["day_r"] <= -self.sc["max_loss_r"]:
            return f"dzienna strata {lv['day_r']:.1f}R: przerwa do końca dnia UTC"
        return None

    def enter(self, lv, sym, strat, plan, mult, price, capital, pos, now_s, forced=False):
        if sym in lv["open"] or len(lv["open"]) >= self.sc["max_pos"] or lv["cool"].get(sym, 0) > now_s:
            return None
        if any(p["symbol"] == sym for p in pos):
            return "pozycja na tym aktywie już istnieje"
        if self.api.ok("GET", "/v5/order/realtime", {"category": "linear", "symbol": sym})["list"]:
            return "na aktywie są otwarte zlecenia"
        step, min_qty, tick = self.instrument(sym)
        used = sum(t["qty"] * t["entry"] for t in lv["open"].values())
        max_x = min(self.sc["max_x"], self.c["max_lev"])          # dźwignię mógł obniżyć panel
        room = capital * max_x - used
        qty = min(capital * self.sc["risk_pct"] * mult / 100 / plan["stop_dist"], room / price, capital * max_x / 2 / price)
        qty = math.floor(qty / step + 1e-9) * step
        if qty < min_qty or qty <= 0:
            return "rozmiar poniżej minimum giełdy"
        sg = plan["side"]
        link = f"sc-{strat}-{int(now_s)}"
        self.api.ok("POST", "/v5/position/set-leverage", {"category": "linear", "symbol": sym, "buyLeverage": f"{self.c['max_lev']:g}", "sellLeverage": f"{self.c['max_lev']:g}"}, allow=(110043,))
        self.api.ok("POST", "/v5/order/create", {"category": "linear", "symbol": sym, "side": "Buy" if sg == 1 else "Sell", "orderType": "Market", "qty": bt.fmt_qty(qty, step),
                                                 "stopLoss": bt.fmt_px(price - sg * plan["stop_dist"], tick), "takeProfit": bt.fmt_px(price + sg * plan["tp_dist"], tick),
                                                 "tpslMode": "Full", "positionIdx": 0, "orderLinkId": link})
        lv["open"][sym] = {"strat": strat, "side": sg, "qty": round(qty, 8), "entry": price, "sd": plan["stop_dist"], "ts": now_s, "hold_s": plan["hold"] * BAR_S, "link": link, "explore": mult < 1, "forced": forced}
        lv["day_n"] += 1
        bt.log(f"[scalp {sym}] {strat} {'long' if sg == 1 else 'short'} {bt.fmt_qty(qty, step)} po ~{price:g}, stop {plan['stop_dist'] / price * 100:.2f}%, cel {plan['tp_dist'] / price * 100:.2f}%")
        return None


# ----------------------------------------------------------------- pętla
def cycle(d, c, sc, pub, live, s, now_s, new_bar, flow=None):
    """Jeden obrót: (co 5 min) dane, cień, bramka, wejścia; (zawsze) nadzór otwartych pozycji. Zwraca listę uwag."""
    notes, sh, lv = [], s["shadow"], s["live"]
    day = time.strftime("%Y-%m-%d", time.gmtime(now_s))
    if lv["day"] != day:
        lv.update(day=day, day_r=0.0, day_n=0)
    capital = pos = acct_err = None
    if live:
        live.c = account_limits(c, d)
    if live and (lv["open"] or new_bar) and sc["can_trade"]:
        try:
            capital, pos = live.account()
            live.reconcile(lv, pos, now_s)
        except Exception as e:  # noqa: BLE001 — awaria konta nie zatrzymuje cienia; otwarte pozycje mają stop i cel na giełdzie
            capital = pos = None
            acct_err = f"konto niedostępne: {str(e)[:100]}"
            bt.notify("scalper: " + acct_err + " (cień działa dalej, nowych zleceń nie będzie)", key="scalp_acct", cooldown_h=6)
    if not new_bar:
        return notes
    if acct_err:
        notes.append(acct_err)
    fresh, prices = {}, {}
    for sym in sc["symbols"]:
        try:
            seed = sym not in s["seeded"]
            bars = fetch_bars(pub, sym, now_s, SEED_PAGES if seed else 1)
            if len(bars) < 60:
                continue
            imb = lean = rd = None
            if not seed:
                x = book_imbalance(pub._request("/v5/market/orderbook", {"category": "linear", "symbol": sym, "limit": 50}))
                if x is not None:
                    if now_s - s["ob_t"].get(sym, 0) > 2 * BAR_S + 60:
                        s["ob"][sym] = []                # odczyt sprzed postoju: nie łączymy go z bieżącym
                    s["ob"][sym] = (s["ob"].get(sym, []) + [round(x, 3)])[-6:]
                    s["ob_t"][sym] = now_s
                    imb = s["ob"][sym]
                rd = flow.reading(sym, bars[-1][0] + BAR_S) if flow else None
                if rd and sh["fl_t"].get(sym) != rd[0]:
                    lean = rd[1]                         # jeden godzinowy odczyt wskaźnika = najwyżej jedno wejście strategii fl
            sig = feed(sh, sym, bars, imb, lean=lean)
            t_fl = sh["open"].get("fl:" + sym)
            if rd and t_fl and t_fl["ts"] == bars[-1][0]:
                sh["fl_t"][sym] = rd[0]
            if seed:
                s["seeded"].append(sym)              # sygnałów z rozbiegu nie handlujemy
            elif sig and now_s - bars[-1][0] - BAR_S < 90:
                fresh[sym], prices[sym] = sig, bars[-1][4]
        except Exception as e:  # noqa: BLE001
            notes.append(f"{sym}: {type(e).__name__}: {str(e)[:80]}")
    st = stats(sh["trades"], now_s)
    led = read_ledger(d)
    brake = real_brake(led, now_s)
    allowed, how = gate(st, sc["explore"], s["gate_on"], brake)
    block = live.blocked(lv, bb.read_settings(d), now_s) if live else "tryb bez zleceń"
    if not block and capital is None:
        block = acct_err or "konto niedostępne"
    if not block and how == "rozpoznanie" and lv["day_n"] >= EXPLORE_MAX_TRADES:
        block = f"rozpoznanie: limit {EXPLORE_MAX_TRADES} transakcji na dobę"
    if not block and fresh:
        for sym, sig in fresh.items():
            ok = [(strat, plan, allowed[strat], False) for strat, plan in sig.items() if strat in allowed and not vetoed(st, strat, sym)]
            if not ok and sc.get("min_day") and lv["day_n"] < sc["min_day"] and lv["day_n"] < EXPLORE_MAX_TRADES:
                best = max(sig, key=lambda k: st[k]["mean_r"])                 # wymuszona aktywność (demo): najlepsza w cieniu, połowa ryzyka
                ok = [(best, sig[best], 0.5, True)]
            for strat, plan, mult, forced in ok[:1]:
                try:
                    why = live.enter(lv, sym, strat, plan, mult, prices[sym], capital, pos, now_s, forced=forced)
                    if why:
                        notes.append(f"{sym} {strat}: {why}")
                except Exception as e:  # noqa: BLE001
                    notes.append(f"{sym} {strat}: zlecenie odrzucone: {str(e)[:100]}")
    rs = [x["r"] for x in led if x.get("r") is not None]
    real_by = {}
    for strat in STRATS:
        rows = [(x.get("ts", 0), x["r"]) for x in led if x.get("strat") == strat and x.get("r") is not None and x.get("ts", 0) >= now_s - BRAKE_DAYS * 86400][-BRAKE_LAST:]
        if rows:
            real_by[strat] = {"n": len(rows), "mean_r": round(_mean([r for _, r in rows]), 3), "t": round(tstat(rows), 2)}
    _write(os.path.join(d, OUT), {"ts": now_s, "mode": c["mode"], "symbols": sc["symbols"], "strategies": st, "allowed": allowed, "gate": how, "blocked": block,
                                  "gate_on": sorted(k for k in s["gate_on"] if k in STRATS), "brake": brake, "real_by": real_by,
                                  "explore": sc["explore"], "min_day": sc.get("min_day", 0), "forced_today": sum(1 for x in lv["open"].values() if x.get("forced")) + sum(1 for x in led if x.get("forced") and time.strftime("%Y-%m-%d", time.gmtime(x.get("ts", 0))) == day), "risk_pct": sc["risk_pct"], "cost_pct": COST_PCT * 100, "min_stop_pct": MIN_STOP_PCT * 100,
                                  "open": [{"sym": k, **{f: v[f] for f in ("strat", "side", "qty", "entry", "ts")}} for k, v in lv["open"].items()],
                                  "shadow_open": len(sh["open"]), "day": {"n": lv["day_n"], "r": round(lv["day_r"], 2)},
                                  "real": {"n": len(rs), "mean_r": round(_mean(rs), 3), "total_r": round(sum(rs), 2), "pnl": round(sum(x.get("pnl") or 0 for x in led), 2), "last": led[-8:][::-1]},
                                  "notes": notes[:8], "criteria": {"min_n": GATE_MIN_N, "min_mean": GATE_MIN_MEAN, "min_t": GATE_MIN_T, "days": GATE_DAYS, "last": GATE_LAST, "off_mean": GATE_OFF_MEAN,
                                                                    "explore_min_n": EXPLORE_MIN_N, "explore_min_mean": EXPLORE_MIN_MEAN, "brake_min_n": BRAKE_MIN_N, "brake_t": BRAKE_T}})
    return notes


def main():
    once = "--once" in sys.argv
    lock = open(os.path.join(HERE, ".scalp.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("inna instancja scalpera już działa")
    bt.load_env()
    c = bt.cfg()
    sc = scalp_cfg(c)
    pub = bt.make_pub(c)
    live = Live(HERE, c, sc, bt.Api(c), pub) if c["mode"] != "dryrun" else None
    bt.log(f"scalper: tryb {c['mode']}, aktywa {','.join(sc['symbols'])}, ryzyko {sc['risk_pct']}%, zlecenia: {'tak' if sc['can_trade'] else 'nie (tylko cień)'}, rozpoznanie: {'tak' if sc['explore'] else 'nie'}")
    s, last_bar = load_state(HERE), 0
    flow = bf.Flow(HERE, sc["symbols"])
    bf.Worker(flow, log=bt.log).start()            # dane o przepływie w tle; pierwsze pobranie trwa ok. minuty
    while True:
        now_s = time.time()
        new_bar = int(now_s) // BAR_S != last_bar and int(now_s) % BAR_S >= 4
        before = json.dumps(s["live"], sort_keys=True)
        try:
            notes = cycle(HERE, c, sc, pub, live, s, now_s, new_bar, flow)
            if new_bar:
                last_bar = int(now_s) // BAR_S
                for n in notes:
                    bt.log("scalper: " + n)
        except Exception as e:  # noqa: BLE001
            bt.notify(f"scalper BŁĄD: {type(e).__name__}: {str(e)[:200]}", key="scalp_err:" + type(e).__name__, cooldown_h=6)
        try:
            if new_bar or json.dumps(s["live"], sort_keys=True) != before:      # stan zapisujemy przy każdej zmianie pozycji i co świecę, nie co 20 s
                _write(os.path.join(HERE, STATE), s)
        except OSError as e:
            bt.notify(f"scalper: nie mogę zapisać stanu ({e})", key="scalp_state", cooldown_h=6)
        bt.flush_notify()
        bt.LOGBUF.clear()
        if once:
            break
        time.sleep(20)


if __name__ == "__main__":
    main()
