"""Kopia zapasowa na Dysk Google: zawartość archiwum (bez kluczy), spójne migawki baz, kontrola po wysyłce, rotacja, błędy, bezpieczne wypakowanie.
rclone jest atrapą: zdalny katalog to zwykły katalog na dysku, więc test nie potrzebuje sieci ani rclone."""
import sys, os, io, json, shutil, sqlite3, tarfile, tempfile, time
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
import btc_backup as bk, btc_userdata as ud
ok = 0
def check(c, m):
    global ok
    assert c, m
    ok += 1
def raises(fn, exc=bk.BackupError):
    try:
        fn()
    except exc as e:
        return str(e)
    return None

class Fake(bk.Rclone):
    """Zdalne „gdrive:ścieżka” = <root>/gdrive/ścieżka."""
    def __init__(self, root):
        super().__init__("/nie/uzywane"); self.root, self.fail, self.truncate, self.calls = root, set(), False, []
    def _map(self, p):
        name, rest = p.split(":", 1)
        return os.path.join(self.root, name, rest.lstrip("/"))
    def _run(self, args, timeout=0):
        self.calls.append(args[0])
        cmd, pos = args[0], [a for a in args[1:] if not a.startswith("--")]
        if cmd in self.fail:
            return 1, "", "ERROR : boom\n"
        if cmd == "version":
            return 0, "rclone v1.60.1\n- os/arch: linux/arm64\n", ""
        if cmd == "lsjson":
            p = self._map(pos[0])
            if not os.path.isdir(p):
                return 3, "", "Failed to lsjson: directory not found\n"
            return 0, json.dumps([{"Name": n, "Size": os.path.getsize(os.path.join(p, n)), "IsDir": False} for n in sorted(os.listdir(p)) if os.path.isfile(os.path.join(p, n))]), ""
        if cmd == "copyto":
            m = lambda p: self._map(p) if ":" in p else p            # zdalne ścieżki mają „nazwa:”, lokalne nie
            dst = m(pos[1]); os.makedirs(os.path.dirname(dst), exist_ok=True)
            data = open(m(pos[0]), "rb").read()
            open(dst, "wb").write(data[: len(data) // 2] if self.truncate else data)
            return 0, "", ""
        if cmd == "deletefile":
            os.remove(self._map(pos[0])); return 0, "", ""
        if cmd == "about":
            return 0, json.dumps({"total": 5 * 1024 ** 4, "used": 10 ** 9, "free": 5 * 1024 ** 4 - 10 ** 9}), ""
        return 1, "", "nieznane polecenie"

def make_app():
    d = tempfile.mkdtemp()
    w = lambda n, t, mode="w": open(os.path.join(d, n), mode).write(t)
    w("bot_settings.json", '{"risk_pct":0.5}'); w("scalp_trades.jsonl", json.dumps({"ts": 1763600000, "sym": "BTCUSDT", "strat": "bo", "side": 1, "qty": 0.01, "entry": 100.0, "pnl": -1.5, "r": -0.9, "held_s": 600, "mode": "demo", "explore": True, "forced": True}) + "\nuszkodzona\n")
    w("bot_trades.jsonl", json.dumps({"closed": 1763500000, "symbol": "=cmd|' /C calc'!A1", "side": "long", "strategy": "ensemble", "entry": 100, "stop": 95, "fill_qty": 1, "pnl": 3.2, "r": 0.6, "reason": "zamknięta", "mode": "demo"}) + "\n")
    w(".notify_state.json", "{}"); w("flow_hist.json", '{"v":1}')
    w("ai.env", "ANTHROPIC_API_KEY=sk-ant-TAJNE\n"); w("trader.env", "TRADER_API_SECRET=TAJNE\n"); w(".env", "BYBIT_LIVE_API_SECRET=TAJNE\n")
    w("zly.json", '{"klucz":"sk-ant-api03-abcdef"}'); w("pol.json.tmp", "x"); w("kod.py", "print(1)")
    big = open(os.path.join(d, "ogromny.json"), "wb"); big.truncate(65 * 1024 * 1024); big.close()
    os.makedirs(os.path.join(d, "api_ext/__pycache__")); w("api_ext/ibkr.py", "GET = {}"); w("api_ext/__pycache__/x.pyc", "x")
    os.makedirs(os.path.join(d, "venv/lib")); w("venv/lib/pakiet.json", "{}"); os.makedirs(os.path.join(d, ".ssh")); w(".ssh/repo_key", "TAJNY KLUCZ")
    con = sqlite3.connect(os.path.join(d, "btc_data.db")); con.execute("PRAGMA journal_mode=WAL"); con.execute("create table c(a int, b text)")
    con.executemany("insert into c values (?,?)", [(i, "x" * 50) for i in range(2000)]); con.commit()
    return d, con
def names_in(path):
    with tarfile.open(path) as t:
        return {m.name for m in t.getmembers()}

d, live_db = make_app()          # live_db: otwarte połączenie z bazą w trybie WAL (tak pracuje kolektor)
ud.write(d, "zarobki", {"entries": [{"idea": "=HYPERLINK(\"x\")", "kind": "in", "amt": 450.5, "date": "2026-11-20", "note": "Kross", "t": 1}, {"idea": "rowery", "kind": "out", "amt": 300, "date": "2026-11-19", "note": "", "t": 2}], "goal": 1000}, base_ts=0)

# 1. zawartość archiwum
tmp = tempfile.mkdtemp(); out = os.path.join(tmp, "a.tar.gz")
n, skipped, warns = bk.build_archive(d, out, 1763600000, tmp)
names = names_in(out)
check({"data/bot_settings.json", "data/scalp_trades.jsonl", "data/bot_trades.jsonl", "data/.notify_state.json", "data/flow_hist.json", "data/userdata.json", "data/btc_data.db", "data/api_ext/ibkr.py", "MANIFEST.json"} <= names, f"dane w archiwum {sorted(names)}")
check(not any(x for x in names if any(b in x for b in ("ai.env", "trader.env", ".env", ".ssh", "venv", "kod.py", ".tmp", "pyc", "ogromny", "zly.json"))), "bez kluczy, venv, kodu, plików tymczasowych, zbyt dużych i z kluczem w treści")
check(any("zly.json" in s and "klucz" in s for s in skipped) and any("ogromny" in s for s in skipped) and not warns, f"pominięte zgłoszone {skipped}")
with tarfile.open(out) as t:
    man = json.load(t.extractfile("MANIFEST.json"))
    check(n == len(man["files"]) and all(len(f["sha256"]) == 64 for f in man["files"]), "manifest z sumami")
    import hashlib
    f0 = next(f for f in man["files"] if f["name"] == "data/bot_settings.json")
    check(hashlib.sha256(t.extractfile("data/bot_settings.json").read()).hexdigest() == f0["sha256"], "suma w manifeście zgadza się z treścią")
    t.extract("data/btc_data.db", tmp, filter="data") if hasattr(tarfile, "data_filter") else t.extract("data/btc_data.db", tmp)
check(sqlite3.connect(os.path.join(tmp, "data/btc_data.db")).execute("select count(*) from c").fetchone()[0] == 2000, "baza SQLite w archiwum jest kompletna (migawka przy otwartym połączeniu WAL)")
check(not os.path.exists(os.path.join(tmp, "btc_data.db")), "migawka tymczasowa usunięta")

# 2. pełny przebieg do „Dysku”
root = tempfile.mkdtemp(); rc = Fake(root); NOW = 1763600000
st = bk.run_backup(d, env={}, rc=rc, now=NOW)
rem = os.path.join(root, "gdrive", "btc-backup")
check(st["ok"] and st["name"] == "btc-20251120-0053.tar.gz" and os.path.getsize(os.path.join(rem, st["name"])) == st["size"] and st["remote"] == "gdrive:btc-backup", f"kopia wysłana i sprawdzona {st}")
check(st["last_ok_ts"] == NOW and st["archives"] == 1 and st["about"]["total"] == 5 * 1024 ** 4 and st["error"] is None, "status")
check(bk.read_status(d)["name"] == st["name"] and not os.path.exists(os.path.join(d, bk.TMP)), "status zapisany, katalog tymczasowy sprzątnięty")
rep = os.path.join(rem, "raporty")
check(set(os.listdir(rep)) == {"zarobki.csv", "transakcje_bota_dziennego.csv", "transakcje_scalpera.csv"}, "raporty CSV obok kopii")
z = open(os.path.join(rep, "zarobki.csv"), encoding="utf-8-sig").read().splitlines()
check(z[0] == "data;pomysl;rodzaj;kwota;notatka" and z[1].startswith("2026-11-19;rowery;koszt;300") and "'=HYPERLINK" in z[2] and "450,5" in z[2], f"CSV zarobków: przecinek dziesiętny, formuły zneutralizowane {z}")
b = open(os.path.join(rep, "transakcje_bota_dziennego.csv"), encoding="utf-8-sig").read()
check("'=cmd" in b and bk._iso(1763500000) in b, "formuła w danych zneutralizowana także w raporcie transakcji")
s = open(os.path.join(rep, "transakcje_scalpera.csv"), encoding="utf-8-sig").read().splitlines()
check(len(s) == 2 and s[1].endswith(";demo;tak;tak") and ";long;" in s[1], f"raport scalpera (uszkodzona linia pominięta) {s[1]}")

# 3. rotacja: nazwy obce nigdy nie są kasowane, stare kopie są przerzedzane dopiero po udanej wysyłce
def stamp(i):
    return time.strftime("btc-%Y%m%d-%H%M.tar.gz", time.gmtime(NOW - i * 86400 + 3 * 3600))
for i in range(1, 400):
    open(os.path.join(rem, stamp(i)), "wb").write(b"x")
open(os.path.join(rem, "moje_notatki.txt"), "w").write("nie ruszać"); open(os.path.join(rem, "btc-stary.tar.gz"), "w").write("obca nazwa")
st2 = bk.run_backup(d, env={}, rc=rc, now=NOW + 86400)
left = sorted(x for x in os.listdir(rem) if bk.NAME_RE.match(x))
check(os.path.exists(os.path.join(rem, "moje_notatki.txt")) and os.path.exists(os.path.join(rem, "btc-stary.tar.gz")), "pliki o obcych nazwach nietknięte")
check(30 <= len(left) <= 30 + 26 + 24 and len(left) < 400 and st2["deleted"] > 300 and st2["archives"] == len(left), f"po rotacji {len(left)} kopii, skasowano {st2['deleted']}")
check(st2["name"] in left, "nowa kopia zostaje")

# 4. funkcja rotacji bezpośrednio
import datetime
mk = lambda dt: dt.strftime("btc-%Y%m%d-%H%M.tar.gz")
base = datetime.datetime(2026, 11, 20, 3, 50)
every = [mk(base - datetime.timedelta(days=i)) for i in range(0, 120)]
dele = bk.retention(every, {"daily": 7, "weekly": 3, "monthly": 2})
keep = [n for n in every if n not in dele]
check(all(mk(base - datetime.timedelta(days=i)) in keep for i in range(7)), "najnowsze 7 dobowych zostaje")
check(len(keep) <= 7 + 3 + 2 and len(keep) >= 8, f"tygodniowe i miesięczne: {len(keep)} zostaje")
check(bk.retention(every[:3], {"daily": 3, "weekly": 0, "monthly": 0}) == [] and bk.retention([], bk.KEEP) == [], "mało kopii: nic nie kasujemy")
check(bk.retention(["x.txt", "btc-1.tar.gz"], bk.KEEP) == [], "obce nazwy ignorowane")

# 5. błędy: brak konfiguracji, brak rclone, zły remote, wysyłka, rozmiar, miejsce
d2 = tempfile.mkdtemp(); open(os.path.join(d2, "a.json"), "w").write("{}")
m = raises(lambda: bk.run_backup(d2, env={}, now=NOW))
check(m and "nie jest podłączony" in m and bk.read_status(d2)["ok"] is False and "podłączony" in bk.read_status(d2)["error"], f"brak rclone.conf: czytelny komunikat w statusie {m}")
cf = os.path.join(d2, "rc.conf"); open(cf, "w").write("[gdrive]\n")
m = raises(lambda: bk.run_backup(d2, env={"BACKUP_RCLONE_CONF": cf}, now=NOW))
check(m and "brak programu rclone" in m or m and "rclone" in m, f"brak programu rclone: {m}")
for bad in ("gdrive", "gdrive:../x", "-x:foo", "gdrive:a;rm -rf", "gdrive:$(x)", "gd rive:x"):
    check(raises(lambda b=bad: bk.config(d2, {"BACKUP_REMOTE": b})), f"zły remote odrzucony: {bad}")
check(bk.config(d2, {"BACKUP_REMOTE": "dysk:Moje kopie/btc"})["remote"] == "dysk:Moje kopie/btc" and bk.config(d2, {})["keep"] == bk.KEEP, "poprawny remote i domyślne limity")
check(bk.config(d2, {"BACKUP_KEEP_DAILY": "1"})["keep"]["daily"] == 3 and raises(lambda: bk.config(d2, {"BACKUP_KEEP_DAILY": "abc"})), "min. 3 dobowe; zła liczba odrzucona")
for failing in ("copyto", "lsjson", "version"):
    root2 = tempfile.mkdtemp(); rc2 = Fake(root2); rc2.fail = {failing}
    prev_ok = bk.read_status(d)["last_ok_ts"]
    m = raises(lambda: bk.run_backup(d, env={}, rc=rc2, now=NOW + 5 * 86400))
    s2 = bk.read_status(d)
    check(m and s2["ok"] is False and s2["last_ok_ts"] == prev_ok and s2["error"], f"{failing}: błąd w statusie, ostatnia udana kopia zapamiętana")
root3 = tempfile.mkdtemp(); rc3 = Fake(root3); rc3.truncate = True
os.makedirs(os.path.join(root3, "gdrive/btc-backup")); open(os.path.join(root3, "gdrive/btc-backup", stamp(40)), "w").write("x")
m = raises(lambda: bk.run_backup(d, env={}, rc=rc3, now=NOW + 6 * 86400))
check(m and "kontrola po wysyłce" in m and os.path.exists(os.path.join(root3, "gdrive/btc-backup", stamp(40))) and "deletefile" not in rc3.calls, f"niepełna wysyłka: błąd i brak rotacji {m}")
real_du = shutil.disk_usage
shutil.disk_usage = lambda p: type("U", (), {"free": 1024})()
m = raises(lambda: bk.run_backup(d, env={}, rc=Fake(tempfile.mkdtemp()), now=NOW + 7 * 86400))
shutil.disk_usage = real_du
check(m and "za mało miejsca" in m, "za mało miejsca na serwerze: nie zaczynamy")
rc4 = Fake(tempfile.mkdtemp()); rc4.fail = {"deletefile"}
os.makedirs(os.path.join(rc4.root, "gdrive/btc-backup"))
for i in range(40, 140): open(os.path.join(rc4.root, "gdrive/btc-backup", stamp(i)), "w").write("x")
s4 = bk.run_backup(d, env={"BACKUP_KEEP_DAILY": "5", "BACKUP_KEEP_WEEKLY": "0", "BACKUP_KEEP_MONTHLY": "0"}, rc=rc4, now=NOW + 8 * 86400)
check(s4["ok"] and any("rotacja" in w for w in s4["warnings"]), "błąd rotacji to ostrzeżenie: udana kopia pozostaje udana")

# 6. odtwarzanie: bezpieczne wypakowanie
good = os.path.join(tmp, "good.tar.gz")
bk.safe_extract(out, os.path.join(tmp, "out1"))
check(os.path.isfile(os.path.join(tmp, "out1", "data", "bot_settings.json")) and os.path.isfile(os.path.join(tmp, "out1", "MANIFEST.json")), "wypakowanie kopii")
for label, build in (("ścieżka ../", lambda t: t.addfile(tarfile.TarInfo("../ucieczka.txt"))), ("ścieżka bezwzględna", lambda t: t.addfile(tarfile.TarInfo("/tmp/ucieczka.txt"))),
                     ("dowiązanie", lambda t: (lambda ti: (setattr(ti, "type", tarfile.SYMTYPE), setattr(ti, "linkname", "/etc/passwd"), t.addfile(ti)))(tarfile.TarInfo("data/link")))):
    bad = os.path.join(tmp, "bad.tar.gz")
    with tarfile.open(bad, "w:gz") as t:
        build(t)
    check(raises(lambda: bk.safe_extract(bad, os.path.join(tmp, "out2"))) and not os.path.exists(os.path.join(tmp, "ucieczka.txt")), f"archiwum z podejrzanym wpisem odrzucone: {label}")
cfg = bk.config(d, {})
fr = Fake(root); dest = bk.fetch(d, st["name"], os.path.join(tmp, "restore"), fr, cfg)
check(os.path.isfile(os.path.join(dest, "data", "userdata.json")), f"fetch: pobranie i wypakowanie do osobnego katalogu {dest}")
check(raises(lambda: bk.fetch(d, "../../etc/passwd", tmp, fr, cfg)) and raises(lambda: bk.fetch(d, "btc-x.tar.gz", tmp, fr, cfg)), "fetch przyjmuje tylko nazwy kopii")
check(bk.read_status(tempfile.mkdtemp()) == {"ts": 0}, "brak statusu = pusty stan")
live_db.close()
print("backup ok", ok)
