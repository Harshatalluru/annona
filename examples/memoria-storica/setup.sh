#!/usr/bin/env bash
# Script comune a tutti gli esempi: examples/_shared/setup.sh
exec "$(dirname "$0")/../_shared/setup.sh" "$(cd "$(dirname "$0")" && pwd)" "$@"
