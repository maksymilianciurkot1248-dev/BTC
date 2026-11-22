#!/usr/bin/env python3
"""Kopia zapasowa stanu bota na Dysk Google (rclone), raz na dobę (btc-backup.timer, 03:50 UTC).

Co trafia do kopii (jedno archiwum btc-RRRRMMDD-GGMM.tar.gz, UTC):
  - pliki danych z katalogu bota: *.json, *.jsonl, *.csv (ustawienia, stan botów, dzienniki transakcji, cień, laboratorium, dane użytkownika)
  - bazy SQLite *.db jako spójne migawki (API kopii SQLite, nie zwykłe kopiowanie otwartego pliku)
  - katalog api_ext (Twoje rozszerzenia API, których nie ma w repozytorium)
  - MANIFEST.json z sumami SHA-256
Czego w kopii NIE ma: kluczy i haseł (*.env, .ssh, konfiguracja rclone), katalogu venv, kodu programu (jest w repozytorium GitHub).
Dodatkowa osłona: plik, w którego treści widać klucz (sk-ant-, PRIVATE KEY, API_SECRET= itp.), jest pomijany i zgłaszany w statusie.
Obok archiwum trafiają czytelne raporty CSV (raporty/): zarobki, transakcje bota dziennego, transakcje scalpera (do podglądu w aplikacji Dysk Google).

Bezpieczeństwo dostępu: skonfiguruj rclone z zakresem „drive.file” (dostęp tylko do plików utworzonych przez rclone, nie do reszty Dysku).
Wysyłka jest sprawdzana (rozmiar pliku na Dysku = lokalny), a rotacja kasuje stare kopie dopiero po udanej wysyłce:
  najnowsze 30 dobowych, po jednej z ostatnich 26 tygodni, po jednej z ostatnich 24 miesięcy (BACKUP_KEEP_DAILY/WEEKLY/MONTHLY w backup.env).
Kasowane są wyłącznie pliki o nazwie btc-RRRRMMDD-GGMM.tar.gz.

Ustawienia (opcjonalne, plik backup.env): BACKUP_REMOTE (domyślnie gdrive:btc-backup), BACKUP_KEEP_*, BACKUP_RCLONE_CONF.
Użycie:  python btc_backup.py                       kopia teraz
         python btc_backup.py --status              ostatni wynik
         python btc_backup.py --check               czy rclone i Dysk odpowiadają (i ile jest wolnego miejsca)
         python btc_backup.py --list                kopie na Dysku
         python btc_backup.py --fetch NAZWA [--to KATALOG]   pobiera i wypakowuje kopię do osobnego katalogu (nic nie nadpisuje)
"""
import csv
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

STATUS = "backup_status.json"
TMP = ".backup_tmp"
NAME_RE = re.compile(r"^btc-(\d{8})-(\d{4})\.tar\.gz$")
REMOTE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,39}:[A-Za-z0-9_./ -]{0,100}$")
DEFAULT_REMOTE = "gdrive:btc-backup"
KEEP = {"daily": 30, "weekly": 26, "monthly": 24}
DATA_SUFFIX = (".json", ".jsonl", ".csv")
MAX_FILE = 64 * 1024 * 1024
SECRET_MARKS = (b"sk-ant-", b"BEGIN PRIVATE KEY", b"BEGIN OPENSSH PRIVATE KEY", b"BEGIN RSA PRIVATE KEY", b"API_SECRET=", b"API_KEY=", b"TELEGRAM_BOT_TOKEN=")
CONF_REL = os.path.join(".config", "rclone", "rclone.conf")


class BackupError(Exception):
    pass


def log(msg):
    print(f"{datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')} backup: {msg}", flush=True)


def _iso(ts):
    try:
        return datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


# ----------------------------------------------------------------- rclone
class Rclone:
    """Cienka warstwa nad poleceniem rclone (bez powłoki: argumenty jako lista)."""

    def __init__(self, conf, exe="rclone"):
        self.conf, self.exe = conf, exe

    def _run(self, args, timeout=7200):
        try:
            p = subprocess.run([self.exe, *args, f"--config={self.conf}"], capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise BackupError("brak programu rclone: sudo apt-get install -y rclone") from None
        except subprocess.TimeoutExpired:
            raise BackupError(f"rclone {args[0]}: przekroczono czas") from None
        return p.returncode, p.stdout, p.stderr

    def _ok(self, args, timeout=7200):
        rc, out, err = self._run(args, timeout)
        if rc != 0:
            last = [x for x in err.strip().splitlines() if x.strip()][-1:] or ["błąd bez opisu"]
            raise BackupError(f"rclone {args[0]}: {last[0][:200]}")
        return out

    def version(self):
        return (self._ok(["version"], 60).splitlines() or ["rclone"])[0]

    def lsjson(self, path):
        rc, out, err = self._run(["lsjson", "--files-only", "--no-modtime", "--no-mimetype", path], 300)
        if rc != 0:
            if "not found" in err.lower():          # pierwszy raz: katalogu jeszcze nie ma
                return []
            raise BackupError(f"rclone lsjson: {([x for x in err.strip().splitlines() if x.strip()] or ['błąd'])[-1][:200]}")
        try:
            return json.loads(out or "[]")
        except ValueError:
            raise BackupError("rclone lsjson: nieczytelna odpowiedź") from None

    def copyto(self, src, dst):
        self._ok(["copyto", src, dst, "--transfers=1", "--drive-chunk-size=64M", "--retries=3", "--low-level-retries=10", "--stats=0"])

    def deletefile(self, path):
        self._ok(["deletefile", path, "--drive-use-trash=false"], 300)

    def about(self, remote):
        """Miejsce na Dysku (może się nie udać przy zakresie drive.file: wtedy None)."""
        try:
            rc, out, _ = self._run(["about", "--json", remote.split(":", 1)[0] + ":"], 60)
            d = json.loads(out) if rc == 0 else None
            return {k: d.get(k) for k in ("total", "used", "free")} if isinstance(d, dict) else None
        except (BackupError, ValueError):
            return None


def join(base, name):
    return base + name if base.endswith(":") else base.rstrip("/") + "/" + name


# ----------------------------------------------------------------- konfiguracja i stan
def config(d, env):
    remote = (env.get("BACKUP_REMOTE") or DEFAULT_REMOTE).strip()
    if not REMOTE_RE.match(remote) or ".." in remote.split(":", 1)[1].split("/") or remote.split(":", 1)[1].startswith("-"):
        raise BackupError("BACKUP_REMOTE ma zły format (np. gdrive:btc-backup)")
    keep = {}
    for k, default in KEEP.items():
        try:
            v = int(env.get("BACKUP_KEEP_" + k.upper()) or default)
        except ValueError:
            raise BackupError(f"BACKUP_KEEP_{k.upper()}: oczekiwano liczby") from None
        keep[k] = max(3 if k == "daily" else 0, min(v, 400))
    conf = env.get("BACKUP_RCLONE_CONF") or os.path.join(d, CONF_REL)
    return {"remote": remote, "keep": keep, "conf": conf}


def read_status(d):
    try:
        with open(os.path.join(d, STATUS), encoding="utf-8") as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {"ts": 0}
    except (OSError, ValueError):
        return {"ts": 0}


def write_status(d, st):
    p = os.path.join(d, STATUS)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(p + ".tmp", p)


# ----------------------------------------------------------------- zawartość kopii
def looks_secret(data):
    return any(m in data for m in SECRET_MARKS)


def collect(d):
    """(pliki danych, bazy SQLite) z katalogu bota (tylko najwyższy poziom, bez dowiązań)."""
    files, dbs = [], []
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if os.path.islink(p) or not os.path.isfile(p) or ".tmp" in name:
            continue
        low = name.lower()
        if low.endswith(".db"):
            dbs.append(name)
        elif low.endswith(DATA_SUFFIX):
            files.append(name)
    return files, dbs


def snapshot_db(src, dst):
    """Spójna migawka bazy SQLite (także w trybie WAL) przez API kopii, bez blokowania zapisów na dłużej niż to potrzebne."""
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
    try:
        out = sqlite3.connect(dst)
        try:
            con.backup(out)
            res = out.execute("PRAGMA quick_check(1)").fetchone() if os.path.getsize(dst) < 2 * 1024 ** 3 else ("ok",)
        finally:
            out.close()
    finally:
        con.close()
    return not res or res[0] == "ok"


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_archive(d, out_path, now, tmpdir):
    """Buduje archiwum. Zwraca (liczba plików, pominięte, ostrzeżenia)."""
    files, dbs = collect(d)
    manifest = {"v": 1, "created": _iso(now), "files": []}
    skipped, warnings = [], []
    with tarfile.open(out_path, "w:gz", compresslevel=6) as tar:
        def add_bytes(arc, data, mtime):
            ti = tarfile.TarInfo(arc)
            ti.size, ti.mtime, ti.mode = len(data), int(mtime), 0o640
            tar.addfile(ti, io.BytesIO(data))
            manifest["files"].append({"name": arc, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        for name in files:
            p = os.path.join(d, name)
            try:
                st = os.stat(p)
                if st.st_size > MAX_FILE:
                    skipped.append(f"{name}: za duży ({st.st_size // 1048576} MB)")
                    continue
                with open(p, "rb") as f:
                    data = f.read(MAX_FILE + 1)
            except OSError as e:
                skipped.append(f"{name}: {e.strerror or e}")
                continue
            if looks_secret(data):
                skipped.append(f"{name}: wygląda na plik z kluczem, pominięty")
                continue
            add_bytes("data/" + name, data, st.st_mtime)
        for name in dbs:
            snap = os.path.join(tmpdir, name)
            try:
                if not snapshot_db(os.path.join(d, name), snap):
                    warnings.append(f"{name}: kontrola spójności bazy zgłosiła błędy (kopia zrobiona, sprawdź bazę)")
                sha, size = _sha256_file(snap), os.path.getsize(snap)
                tar.add(snap, arcname="data/" + name)
                manifest["files"].append({"name": "data/" + name, "size": size, "sha256": sha})
            except (sqlite3.Error, OSError) as e:
                skipped.append(f"{name}: baza nie została skopiowana ({str(e)[:80]})")
            finally:
                try:
                    os.remove(snap)
                except OSError:
                    pass
        ext = os.path.join(d, "api_ext")
        if os.path.isdir(ext) and not os.path.islink(ext):
            for root, dirs, fs in os.walk(ext):
                dirs[:] = [x for x in dirs if x != "__pycache__" and not os.path.islink(os.path.join(root, x))]
                for f in sorted(fs):
                    p = os.path.join(root, f)
                    if os.path.islink(p) or os.path.getsize(p) > 4 * 1024 * 1024:
                        continue
                    with open(p, "rb") as fh:
                        data = fh.read()
                    if looks_secret(data):
                        skipped.append(f"api_ext/{f}: wygląda na plik z kluczem, pominięty")
                        continue
                    add_bytes("data/api_ext/" + os.path.relpath(p, ext).replace(os.sep, "/"), data, os.path.getmtime(p))
        mb = json.dumps(manifest, ensure_ascii=False, indent=1).encode()
        ti = tarfile.TarInfo("MANIFEST.json")
        ti.size, ti.mtime, ti.mode = len(mb), int(now), 0o640
        tar.addfile(ti, io.BytesIO(mb))
    return len(manifest["files"]), skipped, warnings


# ----------------------------------------------------------------- raporty CSV
def _cell(v):
    if isinstance(v, float):
        return str(round(v, 6)).replace(".", ",")
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") and not re.fullmatch(r"-?\d+([.,]\d+)?", s) else s


def csv_bytes(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow([_cell(x) for x in r])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def _jsonl(path, n=5000):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f.readlines()[-n:]:
                try:
                    x = json.loads(ln)
                    if isinstance(x, dict):
                        out.append(x)
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def reports(d):
    """{nazwa pliku: zawartość CSV}. Brak danych = brak raportu."""
    import btc_userdata
    out = {}
    z = btc_userdata.read(d, "zarobki")
    if z["found"] and z["value"]["entries"]:
        out["zarobki.csv"] = csv_bytes(["data", "pomysl", "rodzaj", "kwota", "notatka"], [[e["date"], e["idea"], "przychod" if e["kind"] == "in" else "koszt", e["amt"], e["note"]]
                                                                                           for e in sorted(z["value"]["entries"], key=lambda e: e["date"])])
    rows = _jsonl(os.path.join(d, "bot_trades.jsonl"))
    if rows:
        out["transakcje_bota_dziennego.csv"] = csv_bytes(["zamkniecie_utc", "aktywo", "strona", "strategia", "wariant", "wejscie", "stop", "ilosc", "wynik_usdt", "r", "powod", "tryb"],
                                                         [[_iso(x.get("closed") or x.get("ts")), x.get("symbol"), x.get("side"), x.get("strategy"), x.get("variant"), x.get("entry"), x.get("stop"),
                                                           x.get("fill_qty"), x.get("pnl"), x.get("r"), x.get("reason"), x.get("mode")] for x in rows])
    rows = _jsonl(os.path.join(d, "scalp_trades.jsonl"))
    if rows:
        out["transakcje_scalpera.csv"] = csv_bytes(["zamkniecie_utc", "aktywo", "strategia", "strona", "ilosc", "wejscie", "wynik_usdt", "r", "czas_s", "tryb", "rozpoznanie", "wymuszona"],
                                                   [[_iso(x.get("ts")), x.get("sym"), x.get("strat"), "long" if x.get("side") == 1 else "short", x.get("qty"), x.get("entry"), x.get("pnl"), x.get("r"),
                                                     x.get("held_s"), x.get("mode"), "tak" if x.get("explore") else "nie", "tak" if x.get("forced") else "nie"] for x in rows])
    return out


# ----------------------------------------------------------------- rotacja
def _stamp(name):
    m = NAME_RE.match(name)
    return datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M") if m else None


def retention(names, keep=KEEP):
    """Nazwy do skasowania. Zostaje: najnowsze keep[daily], najnowsza z każdego z ostatnich keep[weekly] tygodni i keep[monthly] miesięcy.
    Pliki o innych nazwach są ignorowane (nigdy ich nie kasujemy)."""
    items = sorted(((_stamp(n), n) for n in names if _stamp(n)), reverse=True)
    keepers = {n for _, n in items[:keep["daily"]]}
    weeks, months = {}, {}
    for dt, n in items:                                  # od najnowszej: pierwsza w grupie jest najnowsza
        weeks.setdefault(dt.isocalendar()[:2], n)
        months.setdefault((dt.year, dt.month), n)
    keepers |= {weeks[k] for k in sorted(weeks, reverse=True)[:keep["weekly"]]}
    keepers |= {months[k] for k in sorted(months, reverse=True)[:keep["monthly"]]}
    return [n for _, n in items if n not in keepers]


# ----------------------------------------------------------------- przebieg
def run_backup(d, env=None, rc=None, now=None):
    """Jedna kopia. Zwraca status; przy błędzie zapisuje status i zgłasza BackupError."""
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    prev, t0 = read_status(d), time.time()
    cfg = None
    st = {"ts": int(now), "ok": False, "remote": None, "last_ok_ts": prev.get("last_ok_ts")}
    tmpdir = os.path.join(d, TMP)
    try:
        cfg = config(d, env)
        st["remote"] = cfg["remote"]
        if rc is None:
            if not os.path.isfile(cfg["conf"]):
                raise BackupError("Dysk Google nie jest podłączony (brak pliku rclone.conf): zob. instrukcję w vps/INSTRUKCJA_VPS.md, sekcja „Kopia zapasowa”")
            rc = Rclone(cfg["conf"])
        rc.version()
        shutil.rmtree(tmpdir, ignore_errors=True)
        os.makedirs(tmpdir, mode=0o700)
        files, dbs = collect(d)
        need = int(1.5 * sum(os.path.getsize(os.path.join(d, n)) for n in files + dbs if os.path.exists(os.path.join(d, n)))) + 100 * 1048576
        if shutil.disk_usage(d).free < need:
            raise BackupError(f"za mało miejsca na dysku serwera do zrobienia kopii (potrzeba ok. {need // 1048576} MB)")
        name = time.strftime("btc-%Y%m%d-%H%M.tar.gz", time.gmtime(now))
        local = os.path.join(tmpdir, name)
        n_files, skipped, warnings = build_archive(d, local, now, tmpdir)
        size = os.path.getsize(local)
        base = cfg["remote"]
        rc.copyto(local, join(base, name))
        listing = rc.lsjson(base)
        got = next((x.get("Size") for x in listing if x.get("Name") == name), None)
        if got != size:
            raise BackupError(f"kontrola po wysyłce nie powiodła się: na Dysku {got} B, lokalnie {size} B")
        for rname, data in reports(d).items():                      # raporty są dodatkiem: ich błąd nie unieważnia kopii
            try:
                p = os.path.join(tmpdir, rname)
                with open(p, "wb") as f:
                    f.write(data)
                rc.copyto(p, join(join(base, "raporty"), rname))
            except (BackupError, OSError) as e:
                warnings.append(f"raport {rname}: {str(e)[:100]}")
        deleted = 0
        try:
            names = [x["Name"] for x in rc.lsjson(base)]
            for old in retention(names, cfg["keep"]):
                rc.deletefile(join(base, old))
                deleted += 1
        except BackupError as e:                                    # rotacja nie może unieważnić udanej kopii
            warnings.append(f"rotacja: {str(e)[:120]}")
        try:
            count = len([1 for x in rc.lsjson(base) if NAME_RE.match(x.get("Name", ""))])
        except BackupError:
            count = None
        st.update(ok=True, name=name, size=size, files=n_files, skipped=skipped, warnings=warnings, deleted=deleted, archives=count, last_ok_ts=int(now),
                  about=rc.about(base), duration_s=round(time.time() - t0, 1), error=None)
    except BackupError as e:
        st.update(error=str(e)[:300], duration_s=round(time.time() - t0, 1))
        write_status(d, st)
        raise
    except Exception as e:  # noqa: BLE001 — nieprzewidziany błąd też ma zostawić ślad w statusie
        st.update(error=f"{type(e).__name__}: {str(e)[:200]}", duration_s=round(time.time() - t0, 1))
        write_status(d, st)
        raise BackupError(st["error"]) from e
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    write_status(d, st)
    return st


def safe_extract(tar_path, dest):
    """Wypakowanie bez ścieżek wychodzących poza katalog i bez dowiązań/urządzeń."""
    os.makedirs(dest, exist_ok=True)
    root = os.path.realpath(dest)
    with tarfile.open(tar_path, "r:gz") as tar:
        for m in tar.getmembers():
            target = os.path.realpath(os.path.join(dest, m.name))
            if not (m.isreg() or m.isdir()) or not (target == root or target.startswith(root + os.sep)):
                raise BackupError(f"podejrzany wpis w archiwum: {m.name}")
        try:
            tar.extractall(dest, filter="data")
        except TypeError:                                          # starszy Python bez parametru filter
            tar.extractall(dest)


def fetch(d, name, to, rc, cfg):
    if not NAME_RE.match(name):
        raise BackupError("zła nazwa kopii (oczekiwano btc-RRRRMMDD-GGMM.tar.gz, patrz --list)")
    dest = os.path.join(to, name[:-7])
    os.makedirs(to, exist_ok=True)
    local = os.path.join(to, name)
    rc.copyto(join(cfg["remote"], name), local)
    safe_extract(local, dest)
    return dest


def notify_failure(msg):
    """Jedno powiadomienie na dobę o tym samym błędzie (Telegram, jeśli skonfigurowany)."""
    try:
        import btc_trader as bt
        bt.load_env()
        bt.notify("kopia zapasowa nieudana: " + msg[:250], key="backup_fail", cooldown_h=24)
        bt.flush_notify()
    except BaseException as e:  # noqa: BLE001 — powiadomienie nie może zasłonić właściwego błędu
        log(f"powiadomienie: {type(e).__name__}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    d = os.environ.get("BTC_BOT_DIR", HERE)
    try:
        if "--status" in argv:
            print(json.dumps(read_status(d), ensure_ascii=False, indent=1))
            return 0
        if any(a in argv for a in ("--check", "--list", "--fetch")):
            cfg = config(d, os.environ)
            if not os.path.isfile(cfg["conf"]):
                raise BackupError("brak pliku rclone.conf: Dysk Google nie jest podłączony")
            rc = Rclone(cfg["conf"])
            if "--check" in argv:
                print(rc.version(), "| katalog:", cfg["remote"], "| kopii:", len([x for x in rc.lsjson(cfg["remote"]) if NAME_RE.match(x.get("Name", ""))]), "| miejsce:", rc.about(cfg["remote"]) or "n/d (zakres drive.file)")
            elif "--list" in argv:
                for x in sorted(rc.lsjson(cfg["remote"]), key=lambda x: x.get("Name", "")):
                    print(f"{x.get('Name')}  {x.get('Size', 0) / 1048576:.1f} MB")
            else:
                i = argv.index("--fetch")
                to = argv[argv.index("--to") + 1] if "--to" in argv else os.path.join(d, "restore")
                print("wypakowano do:", fetch(d, argv[i + 1] if i + 1 < len(argv) else "", to, rc, cfg))
            return 0
        st = run_backup(d)
        log(f"gotowe: {st['name']}, {st['size'] / 1048576:.1f} MB, {st['files']} plików, skasowano starych: {st['deleted']}" + (f", uwagi: {len(st['warnings']) + len(st['skipped'])}" if st["warnings"] or st["skipped"] else ""))
        return 0
    except BackupError as e:
        log(f"BŁĄD: {e}")
        if not any(a in argv for a in ("--check", "--list", "--fetch", "--status")):
            notify_failure(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
