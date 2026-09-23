#!/usr/bin/env bash
# Prepara la demo «Progetto Orione» su un Mac Apple Silicon o su Linux (DGX Spark compreso).
# Non chiede sudo tranne per installare Ollama su Linux se manca. Rilanciarlo è innocuo.
#
#   ./setup.sh                      # sceglie il modello in base alla RAM
#   MODEL=qwen2.5:72b ./setup.sh    # o lo decidi tu
#
# Tutto quello che scarica finisce in .work/ (fuori da git).
set -euo pipefail

KIT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$KIT/../.." && pwd)"
WORK="$KIT/.work"
mkdir -p "$WORK/bin"
export PATH="$WORK/bin:$WORK/node/bin:$HOME/.local/bin:$PATH"

say() { printf '\n\033[1;35m▸ %s\033[0m\n' "$*"; }
OS="$(uname -s)"; ARCH="$(uname -m)"

# ── RAM → modello ────────────────────────────────────────────────────────────
if [ "$OS" = Darwin ]; then RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
else RAM_GB=$(( $(awk '/MemTotal/{print $2}' /proc/meminfo) / 1048576 )); fi
if [ -z "${MODEL:-}" ]; then
  # ponytail: soglie a occhio sulla RAM, non sulla GPU; su DGX si può forzare il 72b
  if   [ "$RAM_GB" -ge 48 ]; then MODEL=qwen2.5:32b
  elif [ "$RAM_GB" -ge 16 ]; then MODEL=qwen2.5:14b
  else MODEL=qwen2.5:7b; fi
fi
say "$OS/$ARCH, ${RAM_GB} GB di RAM → modello locale $MODEL"

# ── uv (porta con sé Python 3.12) ────────────────────────────────────────────
if ! command -v uv >/dev/null; then
  say "Installo uv"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$WORK/bin" INSTALLER_NO_MODIFY_PATH=1 sh
fi

# ── Node ≥ 20 per compilare l'interfaccia ───────────────────────────────────
node_ok() { command -v node >/dev/null && [ "$(node -p 'process.versions.node.split(".")[0]')" -ge 20 ]; }
if ! node_ok; then
  say "Scarico Node 22"
  case "$OS/$ARCH" in
    Darwin/arm64) NP=darwin-arm64 ;; Darwin/x86_64) NP=darwin-x64 ;;
    Linux/aarch64) NP=linux-arm64 ;; Linux/x86_64) NP=linux-x64 ;;
    *) echo "Piattaforma non prevista: $OS/$ARCH"; exit 1 ;;
  esac
  V=v22.20.0
  curl -fsSL "https://nodejs.org/dist/$V/node-$V-$NP.tar.gz" | tar -xz -C "$WORK"
  rm -rf "$WORK/node" && mv "$WORK/node-$V-$NP" "$WORK/node"
fi

# ── Ollama ──────────────────────────────────────────────────────────────────
if ! command -v ollama >/dev/null; then
  say "Installo Ollama"
  if [ "$OS" = Darwin ]; then
    if command -v brew >/dev/null; then brew install ollama
    else echo "Scarica Ollama da https://ollama.com/download, aprilo una volta e rilancia ./setup.sh"; exit 1; fi
  else
    curl -fsSL https://ollama.com/install.sh | sh
  fi
fi
if ! curl -sf localhost:11434/api/tags >/dev/null; then (exec nohup ollama serve >"$WORK/ollama.log" 2>&1) & fi
for _ in $(seq 30); do curl -sf localhost:11434/api/tags >/dev/null && break; sleep 1; done
say "Scarico il modello $MODEL (la prima volta è lungo)"
ollama pull "$MODEL"

# ── Annona ──────────────────────────────────────────────────────────────────
say "Installo Annona"
cd "$REPO"
[ -x env/bin/python ] || uv venv --seed --python 3.12 env
env/bin/pip install -q -r requirements.txt && env/bin/pip install -q -e .
if [ ! -f ui/dist/index.html ]; then (cd ui && npm install --silent && npm run build); fi

# ── rizzo-pii, il redattore locale ──────────────────────────────────────────
RZ="$WORK/rizzo-pii"
if [ ! -d "$RZ" ]; then
  say "Installo rizzo-pii"
  git clone -q https://github.com/Rizzo-AI-Academy/rizzo-pii "$RZ"
fi
[ -x "$RZ/env/bin/python" ] || uv venv --seed --python 3.12 "$RZ/env"
"$RZ/env/bin/pip" install -q -r "$RZ/requirements.txt"
[ -d "$RZ/models/rizzo-pii-0.3B-v1.5.0" ] || "$RZ/env/bin/hf" download rizzoaiacademy/rizzo-pii-0.3B \
  --revision v1.5.0 --local-dir "$RZ/models/rizzo-pii-0.3B-v1.5.0"

# ── Policy e skill ──────────────────────────────────────────────────────────
say "Scrivo la policy della demo"
export ANNONA_HOME="$WORK/home"
mkdir -p "$ANNONA_HOME"
[ -f "$KIT/.env" ] && set -a && . "$KIT/.env" && set +a
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then PUBLIC="frontier, local-gpu"; KEEP=1; else PUBLIC="local-gpu"; KEEP=0; fi
awk -v keep="$KEEP" '/^#FRONTIER/{skip=!keep; next} /^#\/FRONTIER/{skip=0; next} !skip' "$KIT/policy.template.yaml" \
  | sed -e "s|__PRATICHE__|$KIT/Pratiche|g" -e "s|__MODEL__|$MODEL|g" -e "s|__PUBLIC__|$PUBLIC|g" \
  > "$ANNONA_HOME/policy.yaml"
env/bin/annona skills-install catalog/skills/rfq-triage >/dev/null
env/bin/annona policy validate
[ "$KEEP" = 1 ] || echo "Nessuna ANTHROPIC_API_KEY: demo solo locale. Mettila in $KIT/.env e rilancia per gli esiti 2 e 3."

say "Pronto. Avvia con: ./run.sh"
