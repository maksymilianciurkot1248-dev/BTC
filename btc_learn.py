#!/usr/bin/env python3
"""Codzienna nauka systemu (timer btc-learn), bez udziału człowieka:
  1. wybiera wszechświat: najbardziej płynne kontrakty USDT na Bybit (+ aktywa wybrane ręcznie w panelu),
  2. dociąga ich dzienną historię do bazy,
  3. buduje mapę przewagi (btc_edge): które aktywa mają udowodnioną przewagę po kosztach i jakie filtry kontekstu działają
     (korekta FDR wspólna dla obu strategii, histereza: wejście przy p skoryg. <= 0,05, wycofanie dopiero powyżej 0,20),
  4. zapisuje zmiany w dzienniku nauki (widoczny w zakładce Bot) i w historii decyzji (edge_history.json),
  5. prowadzi tryb cienia (btc_shadow) i laboratorium wariantów (btc_lab): diagnoza sygnałów, test wariantów, wnioski o awans.
Nauka może tylko ZAWĘŻAĆ lub rozszerzać listę aktywów w granicach ustawionych przez Ciebie. Nigdy nie zmienia dźwigni, ryzyka,
trybu ani kluczy. Użycie:  python btc_learn.py --db btc_data.db --dir /opt/btc
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import btc_analysis as ba  # noqa: E402
import btc_botcfg as bb  # noqa: E402
import btc_collect as bc  # noqa: E402
import btc_edge as be  # noqa: E402

UNIVERSE_N = 25
MIN_TURNOVER_USD = 50_000_000
STABLES = {"USDCUSDT", "USDEUSDT", "FDUSDUSDT", "TUSDUSDT", "DAIUSDT", "USDTUSDT", "USDPUSDT"}


def fetch_tickers(pub):
    """Wszystkie kontrakty USDT z Bybit (obrót 24 h malejąco): [(obrót, symbol)]."""
    res = pub._request("/v5/market/tickers", {"category": "linear"})
    rows = []
    for t in res.get("list") or []:
        sym = str(t.get("symbol", ""))
        try:
            to = float(t.get("turnover24h") or 0)
        except ValueError:
            continue
        if bb.SYMBOL_RE.match(sym) and sym not in STABLES:
            rows.append((to, sym))
    rows.sort(reverse=True)
    return rows


def fetch_universe(pub, n=UNIVERSE_N, min_turnover=MIN_TURNOVER_USD, rows=None):
    rows = fetch_tickers(pub) if rows is None else rows
    return [s for to, s in rows if to >= min_turnover][:n]


def run(d, db, pub, now=None, say=print):
    now = now or int(time.time())
    ev = lambda kind, msg: (say(f"[{kind}] {msg}"), bb.append_event(d, kind, msg, fname=bb.LEARN_LOG))  # noqa: E731
    manual = bb.read_settings(d).get("symbols") or []
    try:
        tick = fetch_tickers(pub)
        uni = fetch_universe(pub, rows=tick)
        bb.write_catalog(d, [[s, round(to)] for to, s in tick[:600]])      # katalog do podpowiedzi w panelu (np. złoto, tokeny akcji)
    except Exception as e:  # noqa: BLE001
        uni = []
        ev("alert", f"nie udało się pobrać listy aktywów z Bybit: {e}")
    prev = bb.read_edge(d)
    if not uni:                       # bez świeżej listy zostajemy przy poprzednim wszechświecie
        uni = list((prev.get("assets") or {}).keys())
    universe = list(dict.fromkeys(["BTCUSDT", *uni, *manual]))
    ok, bad = 0, []
    for sym in universe:
        try:
            be.refresh_candles(db, pub, sym, now * 1000)
            ok += 1
        except Exception as e:  # noqa: BLE001 — jedno aktywo nie zatrzymuje reszty
            bad.append(f"{sym}: {str(e)[:60]}")
    ev("info", f"Dane: historia dzienna dla {ok} z {len(universe)} aktywów" + (f"; błędy: {'; '.join(bad[:3])}" if bad else ""))
    import btc_lab
    reg, champ = btc_lab.variants(d), btc_lab.champion(d)          # zespół sygnałów oceniamy w wariancie, którym handluje bot
    maps, errs = be.build_maps(db, universe, now, ("plan", "ensemble"), prev=prev, ens=(champ, reg[champ]["params"]))    # wspólna korekta FDR i histereza względem poprzedniej mapy
    new, ens = maps["plan"], maps.get("ensemble")
    for msg in errs.values():
        ev("alert", f"nie udało się ocenić zespołu sygnałów: {msg}")
    old_ens = {"allowed": prev.get("allowed_ens") or [], "assets": prev.get("assets_ens") or {}}
    if ens:         # ta sama ocena dla zespołu sygnałów (btc_decision): osobna lista dopuszczonych aktywów
        new["assets_ens"], new["allowed_ens"], new["held_ens"] = ens["assets"], ens["allowed"], ens["held"]
        ev("info", f"Zespół sygnałów (wariant {champ}): {len(ens['allowed'])} z {len(universe)} aktywów dopuszczonych" + (f" ({', '.join(ens['allowed'][:8])})" if ens["allowed"] else ""))
        if (prev.get("ens_variant") or "v1") != champ and prev.get("assets_ens"):
            ev("info", f"Zmienił się wariant zespołu sygnałów ({prev.get('ens_variant') or 'v1'} → {champ}): jego aktywa wchodzą do mapy od nowa, według pełnych kryteriów")
    h = new["hysteresis"]
    ev("info", f"Korekta FDR wspólna dla obu strategii (rodzina {new['params']['family']} testów): pełne kryteria wejścia spełnia {h['entry_pass_joint']} par strategia × aktywo "
               f"(przy korekcie osobno dla każdej strategii: {h['entry_pass_solo']})" + ("" if h["prior_used"] else ". Brak świeżej poprzedniej mapy: wszystkie aktywa wchodzą od nowa, według pełnych kryteriów"))
    bb.write_edge(d, new)
    trans = {"plan": be.transitions(prev, new)}
    if ens:
        trans["ensemble"] = be.transitions(old_ens, ens)
    bb.write_edge_history(d, be.update_history(bb.read_edge_history(d), trans, now))        # historia decyzji: dopuszczone / wycofane / utrzymane
    for kind, msg in be.diff_maps(prev, new):
        ev(kind, msg)
    if ens:
        for kind, msg in be.diff_maps(old_ens, ens):
            ev(kind, "[zespół] " + msg)
    try:        # tryb cienia: dziennik obu strategii bez zleceń (wyniki poza próbą)
        import btc_shadow
        n, sh = btc_shadow.run(d, db, universe, now)
        ev("info", f"Tryb cienia: krok dla {n} par aktywo × strategia. Zamknięte transakcje na żywo: " + ", ".join(f"{v['name']} {v['n']}" for v in sh["strategies"].values()))
    except Exception as e:  # noqa: BLE001
        ev("alert", f"tryb cienia: {str(e)[:100]}")
    try:        # laboratorium wariantów: diagnoza sygnałów, test historyczny, porównanie z cienia, wnioski o awans
        lab, lmsgs = btc_lab.run(d, db, universe, now)
        for kind, msg in lmsgs:
            ev(kind, msg)
        ch = next(v for v in lab["variants"] if v["id"] == lab["champion"])
        ev("info", f"Laboratorium: mistrz {lab['champion']} (historia {ch['hist']['mean_r']:+.2f}R na {ch['hist']['n']} transakcjach, cień {ch['fwd']['n']}); "
                   + "pretendenci: " + (", ".join(f"{v['id']} [{v.get('verdict', '')[:60]}]" for v in lab["variants"] if v["role"] == "challenger") or "brak"))
    except Exception as e:  # noqa: BLE001
        ev("alert", f"laboratorium: {type(e).__name__}: {str(e)[:100]}")
    try:        # analiza cudzych transakcji (Hyperliquid), tylko gdy dodano portfele do obserwacji
        import btc_copy
        _res, cmsgs = btc_copy.run(d)
        for kind, msg in cmsgs:
            ev(kind, msg)
    except Exception as e:  # noqa: BLE001
        ev("alert", f"kopiowanie: {type(e).__name__}: {str(e)[:100]}")
    allowed = new["allowed"]
    ev("info", f"Mapa przewagi: {len(allowed)} z {len(universe)} aktywów dopuszczonych" + (f" ({', '.join(allowed[:8])})" if allowed else ", bot automatyczny nie otworzy nowych pozycji"))
    return new


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("BTC_DB", os.path.join(HERE, "btc_data.db")))
    ap.add_argument("--dir", default=HERE)
    a = ap.parse_args()
    ba.load_dotenv([os.path.join(HERE, ".env")])
    run(a.dir, bc.DB(a.db), ba.BybitClient(base_url=os.environ.get("BYBIT_BASE_URL")))


if __name__ == "__main__":
    main()
