"""Uruchamia test z zegarem przesuniętym o N dni (bez zmiany zegara systemu): wykrywa testy, które psują się z upływem czasu.
Użycie: python3 clock_shift.py DNI test_x.py     |     wszystkie: for t in test_*.py; do python3 clock_shift.py 90 $t >/dev/null 2>&1 || echo $t; done
Znany artefakt: test_ai.py i test_api.py porównują czas modyfikacji pliku (zegar systemu) z przesuniętym zegarem, więc tu nie przechodzą;
to nie jest zależność od daty. Stan z 16.11.2026: przy +1, +30 i +200 dniach pozostałe testy przechodzą.
"""
import datetime as _d
import runpy
import sys
import time as _t

SHIFT = float(sys.argv[1]) * 86400
_time, _gm, _loc, _strf, _ns = _t.time, _t.gmtime, _t.localtime, _t.strftime, _t.time_ns
_t.time = lambda: _time() + SHIFT
_t.time_ns = lambda: _ns() + int(SHIFT * 1e9)
_t.gmtime = lambda s=None: _gm(_t.time() if s is None else s)
_t.localtime = lambda s=None: _loc(_t.time() if s is None else s)
_t.strftime = lambda fmt, tt=None: _strf(fmt, _t.localtime() if tt is None else tt)
_delta = _d.timedelta(seconds=SHIFT)


class datetime(_d.datetime):
    @classmethod
    def now(cls, tz=None):
        return super().now(tz) + _delta

    @classmethod
    def utcnow(cls):
        return super().utcnow() + _delta

    @classmethod
    def today(cls):
        return super().today() + _delta


class date(_d.date):
    @classmethod
    def today(cls):
        return _d.date.fromordinal((_d.datetime.now() + _delta).toordinal())


_d.datetime, _d.date = datetime, date
sys.argv = sys.argv[2:]
runpy.run_path(sys.argv[0], run_name="__main__")
