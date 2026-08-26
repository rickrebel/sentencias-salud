#!/usr/bin/env bash
# Relanza la cosecha con el código vigente en disco: mata el proceso
# anterior (si vive), remintea el token y arranca run_tonight.sh.
# Pensado para correr desde un temporizador de systemd de usuario.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$HERE/harvest.pid"
LOG="$HERE/logs/relaunch.log"
mkdir -p "$HERE/logs"
exec >>"$LOG" 2>&1
echo "== relaunch $(date '+%F %T %Z')"

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  OLD="$(cat "$PIDFILE")"
  kill -TERM "$OLD"
  # SIGTERM guarda el estado al terminar la página en curso; esperar.
  for _ in $(seq 1 60); do
    kill -0 "$OLD" 2>/dev/null || break
    sleep 2
  done
  if kill -0 "$OLD" 2>/dev/null; then
    echo "el proceso $OLD no murió tras 120 s; abortando relanzamiento"
    exit 1
  fi
  echo "proceso anterior $OLD terminado"
fi

node "$HERE/mint_token.mjs"
PARTITION=day "$HERE/run_tonight.sh"
