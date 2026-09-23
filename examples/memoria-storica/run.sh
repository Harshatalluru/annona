#!/usr/bin/env bash
# Script comune a tutti gli esempi: examples/_shared/run.sh
exec "$(dirname "$0")/../_shared/run.sh" "$(cd "$(dirname "$0")" && pwd)" "$@"
