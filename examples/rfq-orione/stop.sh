#!/usr/bin/env bash
# Spegne Annona e rizzo-pii. Ollama resta acceso (serve ad altro); per spegnerlo: ./stop.sh --all
for port in 7070 5005; do lsof -ti :"$port" | xargs kill 2>/dev/null || true; done
[ "${1:-}" = --all ] && pkill -x ollama || true
echo "Spento."
