#!/usr/bin/env bash
# Wszystkie testy po kolei; kod wyjscia 1, gdy ktorykolwiek nie przejdzie. Uzycie: bash tests/run_all.sh [python]
cd "$(dirname "$0")" || exit 1
PY="${1:-python3}"; fail=0; n=0
for t in test_*.py; do
  n=$((n + 1))
  if out=$("$PY" "$t" 2>&1); then echo "ok    $t"; else fail=$((fail + 1)); echo "BLAD  $t"; echo "$out" | tail -5 | sed 's/^/      /'; fi
done
echo "testy: $n, nieudane: $fail"
[ "$fail" -eq 0 ]
