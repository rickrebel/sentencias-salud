#!/usr/bin/env bash
# Lanza la cosecha en segundo plano con la ventana nocturna por defecto.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$HERE/harvest.pid"
SCHEDULE="${SCHEDULE:-23:00-06:45}"
START="${START:-2011-01}"
END="${END:-2026-04}"
PARTITION="${PARTITION:-day}"

if [[ "$PARTITION" == "month" ]]; then
  echo "AVISO: partición 'month' pagina y pierde ~3% de las filas." >&2
  echo "       Para la cosecha completa: PARTITION=day $0" >&2
fi

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Ya hay una cosecha viva (PID $(cat "$PIDFILE")): no hace falta" >&2
  echo "  relanzarla. Relee token.txt cada vez que despierta de la espera" >&2
  echo "  horaria, así que basta con remintear antes de las 23:00:" >&2
  echo "    node $HERE/mint_token.mjs" >&2
  echo "  Si de verdad quieres relanzar, primero mátala y comprueba:" >&2
  echo "    kill -TERM \$(cat $PIDFILE)" >&2
  echo "    kill -0 \$(cat $PIDFILE) && echo viva || echo muerta" >&2
  exit 1
fi

if [[ ! -s "$HERE/token.txt" ]]; then
  echo "Falta $HERE/token.txt. Corre: node $HERE/mint_token.mjs" >&2
  exit 1
fi

mkdir -p "$HERE/logs"
nohup python3 "$HERE/harvest_metadata.py" \
  --start "$START" --end "$END" --schedule "$SCHEDULE" \
  --partition "$PARTITION" \
  > "$HERE/logs/nohup.out" 2>&1 &
echo $! > "$PIDFILE"

cat <<EOF
Cosecha lanzada. PID $(cat "$PIDFILE"), partición $PARTITION,
ventana $SCHEDULE, meses $START..$END.

Ver avance:
  python3 $HERE/harvest_metadata.py --status
  tail -f $HERE/logs/harvest.log

Detener (guarda el estado al terminar la página en curso):
  kill -TERM \$(cat $PIDFILE)

Comprobar que sigue vivo:
  kill -0 \$(cat $PIDFILE) && echo viva || echo muerta
EOF
