# Pruebas

Un solo nivel montado: **pytest sin red**. Las peticiones se simulan con dobles de la biblioteca estándar (una sesión falsa que sustituye a `requests.Session`), así que las pruebas no abren ningún socket ni escriben en `api/harvest/data/` ni en `api/media/`. No hay configuración de pytest ni credenciales: cada carpeta `tests/` se basta a sí misma y la suite completa corre desde `api/`:

```bash
cd api && python3 -m pytest harvest/tests pdf_text/tests scripts/sampling/tests -q     # 33 pruebas
```

## Cosechador (`api/harvest/tests/`)

```bash
cd api/harvest && python3 -m pytest tests/
```

`tests/test_poisoned.py` cubre el clasificador de rebanadas envenenadas del cosechador:

- Un `500` con `Content-Type: text/html` marca la rebanada `poisoned` con una sola búsqueda, más la sonda `size=1` cuando el flag está activo, y sin reintentos.
- Una respuesta `200` con JSON escribe sus filas y cierra la rebanada como hasta ahora.
- Un `requests.Timeout` y un `500` que no es HTML siguen reintentando tres veces; si al volver del diferimiento responde el 500 permanente, pasa a `poisoned`.
- El `state.json` real (sobre una copia temporal) carga, y `--status` y `--list-poisoned` lo reportan; una entrada sin el contador `requests` ya no revienta.

`tests/test_rescue.py` cubre la pasada de rescate (`rescue_poisoned.py`), reutilizando la sesión falsa y las fixtures de `test_poisoned.py`. Cinco funciones:

- Una hoja cuya sonda `size=1` devuelve `500` + `text/html` termina `opaque` sin pedir una sola página.
- El atajo de la memoria de culpables cae al acorralamiento completo por tercios cuando el bloque «todos los estados menos los conocidos» devuelve `500`, en vez de dar por buena la memoria.
- Un culpable aislado por fallo de red —reintentos agotados, no envenenamiento— no se persiste en `rescue_memory`.
- `append_rows()` no reescribe las filas cuya terna ya está en el archivo y devuelve el conteo correcto.
- Una rebanada cuya fuente no declara hits conserva `rows_missing` en `None` —pérdida desconocida, que no es cero—, deja de anunciarse en la lista `failed` del mes y no suma al `total_hits`.

```bash
cd api/harvest && python3 -m pytest tests/ -q     # 16 pruebas
```

## Limpiador de PDF (`api/pdf_text/tests/`)

`tests/test_clean.py` cubre `clean2.py` con una prueba que escribe un PDF pequeño en el directorio temporal de pytest con `tests/pdf_writer.py` (Helvetica, sin datos personales ni binarios en el repo) y necesita `pdftotext` de poppler, sin el cual se salta. Muerde con `clean2` (comprobado revirtiendo la regla de `verify.grade`):

- Un documento cuyos renglones son todos encabezado o folio deja 0 caracteres limpios con residuo 0, y su estado no puede ser `ok` (sale `empty`).

El resto de `clean2.py`, `clean2_geo.py` y `viz.py` (visualizador) no tiene pruebas todavía: las listas propuestas viven en las tasks del grafo que construyen cada uno (`docs/tasks/`), y se escriben cuando Ricardo las fije.

```bash
cd api && python3 -m pytest pdf_text/tests -q     # 1 prueba
```

## Muestra y descarga determinista (`api/scripts/sampling/tests/`)

`tests/test_download_sample.py` cubre `download_sample.py` con una sesión falsa inyectada en `Fetcher`, las pausas anuladas y todas sus rutas (media, mapa de órganos, log del piloto, URLs resueltas) redirigidas al directorio temporal de pytest:

- `candidates()` ordena la primera ronda según la época de la sentencia (antigua hasta 2015, moderna desde 2019, intercalada entre ambas), prueba la primera ronda de cada `idOrgano` antes de cualquier ventana y no repite nombres. La ventana moderna no pasa de max(S, N): baja desde S−1 y, cuando S < N, sigue de N−1 hacia S+1; la antigua recorre [min(S,N)−2, max(S,N)+2] de la más cercana a la más lejana, y en la época intermedia el intercalado no pierde la cola de la familia más larga.
- El emparejamiento de una URL resuelta con su fila de metadatos es por estructura del nombre y no por posición: el caso del piloto, asunto 8243038, donde `06680000082430380043002` va con la fila S=43 N=2 y `…0024001` no va con ninguna.
- Los desenlaces de `probe_row`: acierto en la primera ronda da `pdf`, acierto en la ventana da `suspect`, candidatos agotados dan `not-reached`, un 5xx persistente da `error` tras tres reintentos sobre el mismo nombre, y un archivo ya en disco no gasta petición.
- La reanudación: las filas con desenlace final se saltan, una `no-idorgano` se reintenta cuando el mapa ya tiene su juzgado (buscado por el nombre sin vigencia) y sigue saltándose si no, y una `error` se reintenta.

`tests/test_probe_prefs.py` cubre el orden aprendido de la primera ronda (`probe_prefs.json`) con un archivo de preferencias temporal. Es provisional: fija la ponderación tal como está con 1 050 nombres conocidos y se afinará cuando las reglas aprendidas vivan en la base de datos. Siete funciones:

- Un juzgado cuyo registro del mismo año es todo de la familia antigua pone `old` primero en una sentencia de 2020, y un juzgado ausente de las preferencias conserva el orden por época.
- Un solo avistamiento de una regla rara (`modern-n`) no supera a `modern-s`; cuatro sí.
- La frontera de familia: un documento moderno del juzgado fechado antes de la fila, sin ninguno antiguo después, pone la familia moderna primero aun en 2012.
- `candidates(..., prefs={})` es el orden por época en 2011, 2017 y 2020, y la ventana es idéntica con y sin preferencias.
- `load_prefs` con una ruta inexistente deja `PREFS == {}`, y `probe_row` pasa a `candidates()` el nombre del juzgado sin vigencia y la fecha en ISO.
- El aprendiz (`gather()`) lee el log de sondas y el `resolved_urls_fallback.jsonl` de cada `--sample-dir`, y conserva ambas fuentes tras el dedupe (usa dos NEUN distintos, así que no ejercita la fusión de una fila presente en dos muestras).
- El clasificador del aprendiz (`classify()`) reconoce `modern-s`, `modern-n`, `window-modern`, `old`, `old-002`, `window-old` y `other`, y un nombre de otro NEUN (`foreign`) queda fuera de `build_prefs`.

`tests/test_build_organ_map.py` cubre `base_name`: quita las tres formas de sufijo de vigencia —`(28/10/2003- 30/09/2024)`, `(2/4/2001) - (30/11/2022)` y `(1/6/2004 - 15/11/2022)`— y deja intacto cualquier otro paréntesis.

```bash
cd api && python3 -m pytest scripts/sampling/tests -q     # 16 pruebas
```

No hay pruebas e2e todavía: no hay interfaz.
