"""Asystent głosowy (intent.js, assistant.js): polecenia po polsku (czyste funkcje, node), poprawność rysunku postaci (SVG jako XML, wszystkie odwołania istnieją),
składnia skryptów. Przeglądarkowe zachowanie sceny (mikrofon, głośnik, animacje) sprawdzam osobno w Chromium na atrapach Web Speech API."""
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = 0


def check(c, m):
    global ok
    assert c, m
    ok += 1


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return f.read()


# ----------------------------------------------------------------- rysunek postaci: SVG poprawny jako XML, odwołania do gradientów, przycięć i <use> istnieją
src = read("assistant.js")
m = re.search(r"const SVG = u => `(.*?)`;", src, re.S)
check(m, "w assistant.js jest szablon SVG postaci")
svg = m.group(1).replace("${u}", "t1-")
check("${" not in svg, "w szablonie nie zostały nierozwiązane zmienne")
root = ET.fromstring(svg)
ns = "{http://www.w3.org/2000/svg}"
ids = {e.get("id") for e in root.iter() if e.get("id")}
refs = set(re.findall(r"url\(#([^)]+)\)", svg)) | {h.lstrip("#") for h in re.findall(r'href="(#[^"]+)"', svg)}
check(refs <= ids, f"odwołania bez definicji w SVG: {sorted(refs - ids)}")
check(root.tag == ns + "svg" and root.get("viewBox") == "0 0 300 320" and root.get("role") == "img" and root.get("aria-label"), "SVG ma viewBox, rolę i opis dla czytników ekranu")
classes = {c for e in root.iter() for c in (e.get("class") or "").split()}
for need in ("body", "sway", "tilt", "nod", "brow-l", "brow-r", "eye-l", "eye-r", "pupils", "lid", "lidlow", "m-fill", "m-lip", "m-clip", "m-teeth", "m-tongue", "stache", "ring", "rp", "fx-think", "fx-worry", "spark"):
    check(need in classes, f"w SVG brakuje części animowanej: {need}")
check(len(svg) < 20000, "rysunek postaci jest lekki (poniżej 20 KB)")
for state in ("idle", "listen", "think", "happy", "worry"):
    check(f".alf.st-{state}" in src, f"CSS ma stan postaci {state}")
check(".alf.speaking" in src and "prefers-reduced-motion" in src, "mówienie jest cechą niezależną od nastroju, a animacje respektują „ogranicz ruch”")

# ----------------------------------------------------------------- polecenia po polsku (node)
node = shutil.which("node")
if node:
    r = subprocess.run([node, os.path.join(ROOT, "tests", "intent_cases.js")], capture_output=True, text=True, timeout=60)
    check(r.returncode == 0 and r.stdout.startswith("ok"), f"intent.js: {r.stderr[-1200:] or r.stdout[-300:]}")
    for f in ("intent.js", "assistant.js", "calm.js"):
        r = subprocess.run([node, "--check", os.path.join(ROOT, f)], capture_output=True, text=True)
        check(r.returncode == 0, f"{f}: błąd składni: {r.stderr[:300]}")
else:
    print("node niedostępny: pominięto testy poleceń i składni")
print("assistant ok", ok)
