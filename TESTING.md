# Pruebas

Un solo nivel montado: **pytest sin red**. Las peticiones se simulan con dobles de la biblioteca estándar (una sesión falsa que sustituye a `requests.Session`), así que las pruebas no abren ningún socket ni escriben en `api/harvest/data/`.

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

No hay pruebas e2e todavía: no hay interfaz.
