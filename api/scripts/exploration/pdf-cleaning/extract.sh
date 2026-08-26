#!/usr/bin/env bash
# Extrae los 48 PDFs de la muestra en tres modos de pdftotext + bbox-layout.
# Los archivos con extensión .docx son PDFs reales (el servidor miente en la
# extensión); por eso se listan por ruta, no por glob de *.pdf.
set -u
BASE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$BASE/limpieza-pdf"
mkdir -p "$OUT"/txt-default "$OUT"/txt-layout "$OUT"/txt-raw "$OUT"/bbox
for f in "$BASE"/pdf-sample-old/* "$BASE"/*.pdf; do
  b="$(basename "${f%.*}")"
  pdftotext -enc UTF-8 -eol unix          "$f" "$OUT/txt-default/$b.txt" 2>/dev/null
  pdftotext -enc UTF-8 -eol unix -layout  "$f" "$OUT/txt-layout/$b.txt"  2>/dev/null
  pdftotext -enc UTF-8 -eol unix -raw     "$f" "$OUT/txt-raw/$b.txt"     2>/dev/null
  pdftotext -enc UTF-8 -bbox-layout       "$f" "$OUT/bbox/$b.xml"        2>/dev/null
done
