#!/usr/bin/env bash
# Launches the sample download in the background; every arg goes to the
# script (e.g. --limit 50, --fallback, --schedule 23:00-06:45).
# --sample-dir DIR picks the sample; PID, output and logs live in DIR.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAMPLE="$HERE/../../harvest/data/samples/2026-09-22-muestra-1100"
ARGS=("$@")
for ((i = 0; i < ${#ARGS[@]}; i++)); do
  case "${ARGS[$i]}" in
    --sample-dir) SAMPLE="${ARGS[$((i + 1))]:?--sample-dir sin valor}" ;;
    --sample-dir=*) SAMPLE="${ARGS[$i]#--sample-dir=}" ;;
  esac
done
SAMPLE="$(cd "$SAMPLE" && pwd)"
PIDFILE="$SAMPLE/download.pid"
OUT="$SAMPLE/download.out"

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Ya hay una descarga viva (PID $(cat "$PIDFILE"))." >&2
  echo "  Para relanzar, primero detenla:" >&2
  echo "    kill -TERM \$(cat $PIDFILE)" >&2
  exit 1
fi

# The script gets the resolved dir, whatever form the caller used.
nohup python3 "$HERE/download_sample.py" "$@" --sample-dir "$SAMPLE" \
  >> "$OUT" 2>&1 &
echo $! > "$PIDFILE"

cat <<MSG
Descarga lanzada. PID $(cat "$PIDFILE"), argumentos: ${*:-ninguno}.
Muestra: $SAMPLE

Ver avance:
  tail -f $OUT
  python3 $HERE/download_sample.py --status --sample-dir $SAMPLE

Detener (termina la fila en curso y escribe el resumen; si está
esperando la ventana horaria, sale en menos de 5 s):
  kill -TERM \$(cat $PIDFILE)

Comprobar que sigue viva:
  kill -0 \$(cat $PIDFILE) && echo viva || echo muerta

Resumen al terminar o al pausar: $SAMPLE/download_summary.json
Log por fila: $SAMPLE/download_log.jsonl
MSG
