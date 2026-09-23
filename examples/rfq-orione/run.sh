#!/usr/bin/env bash
# Avvia Ollama, rizzo-pii e Annona per la demo, poi apre l'interfaccia. Ctrl+C non serve:
# restano accesi in background; ./stop.sh li spegne.
set -euo pipefail

KIT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$KIT/../.." && pwd)"
WORK="$KIT/.work"
export PATH="$WORK/bin:$WORK/node/bin:$PATH"
export ANNONA_HOME="$WORK/home"
export ANNONA_INBOX="$WORK/inbox"   # gli allegati dall'interfaccia: la policy li autorizza qui
[ -f "$ANNONA_HOME/policy.yaml" ] || { echo "Prima lancia ./setup.sh"; exit 1; }
[ -f "$KIT/.env" ] && set -a && . "$KIT/.env" && set +a

up() { curl -sf "$1" >/dev/null; }
wait_for() { for _ in $(seq "$2"); do up "$1" && return 0; sleep 2; done; echo "Non risponde: $1"; exit 1; }

if ! up localhost:11434/api/tags; then (exec nohup ollama serve >"$WORK/ollama.log" 2>&1) & fi
if ! up localhost:5005/health; then (cd "$WORK/rizzo-pii" && exec nohup env/bin/python src/app/app.py >"$WORK/rizzo.log" 2>&1) & fi
if ! up localhost:7070/health; then (cd "$REPO" && exec nohup ./start.sh >"$WORK/annona.log" 2>&1) & fi

wait_for localhost:11434/api/tags 15
wait_for localhost:5005/health 60
wait_for localhost:7070/health 60

"$REPO/env/bin/annona" substrates
echo
echo "Pratiche della demo: $KIT/Pratiche"
echo "Interfaccia:         http://127.0.0.1:7070"
[ -n "${NO_OPEN:-}" ] || { command -v open >/dev/null && open http://127.0.0.1:7070; } || true
