# Conteos del universo cosechado

Tres scripts de solo lectura sobre `api/harvest/data/metadata/*.jsonl`, con su salida en `api/harvest/data/universe/` (ignorado por git): `count_universo.py` cuenta filas y asuntos únicos del universo entero y de cada recorte candidato (U1–U5), deduplicando por la terna, y escribe `universo_counts.json`; `count_strata.py` arma la matriz de estratificación año × circuito × tipo de órgano sobre materia Administrativa y escribe `estratos_admin.csv`; `count_organos.py` cuenta órganos distintos y pares órgano × circuito, e imprime a pantalla.

Los tres importan `classify` de `../explore-content/classify.py` (por `sys.path`, no como paquete) para marcar las resoluciones de fondo.
