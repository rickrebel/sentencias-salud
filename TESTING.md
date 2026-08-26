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

No hay pruebas e2e todavía: no hay interfaz.
