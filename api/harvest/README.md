# Cosecha de metadatos del BuscadorSISE (Amparo Indirecto, 2011–2026)

Proceso externo, sin Django, que recorre el índice del BuscadorSISE y guarda **una fila JSON cruda por línea** de cada resultado de tipo «Amparo Indirecto» en la ventana 01-01-2011 a 30-04-2026 (4,191,156 hits declarados al 25 de agosto de 2026). Corre de noche, es reanudable y no necesita más dependencias que `requests`.

## Lo primero: elige la partición

Hay dos modos y **no dan el mismo resultado**.

- `--partition day` (**el recomendado y el que viene por omisión**): una petición por día; si un día no cabe en una página, se parte por circuito y, si hace falta, por tipo, especialidad y nombre oficial de órgano. Cada rebanada cabe entera en una sola respuesta, así que no hay frontera de paginación y **no se pierde ni se duplica nada**.
- `--partition month`: pagina el mes con páginas de 1000. Es lo que se planeó al principio, pero la medición del 25 de agosto lo desaconseja: la API corta el resultado crudo en `size` y luego vuelve a expandir cada asunto, de modo que dos páginas consecutivas ni se solapan ni empalman. En marzo de 2019 devolvió 31,112 filas con 920 duplicados exactos y **30,192 filas únicas frente a 31,036 hits declarados: 2.9 % perdido**. El modo `day` sobre el mismo mes devolvió 31,110 filas únicas, cero duplicados, y contiene todas las del modo `month` más 918 que este nunca vio. El costo en tiempo es el mismo (196 s contra 202 s para ese mes).

## Token

El token es un JWT anónimo que dura 8 horas exactas y no tiene refresh. Se obtiene solo con cargar la página; no hay login ni reCAPTCHA.

```bash
node api/harvest/mint_token.mjs      # abre la página headless y escribe token.txt
```

El script reutiliza el Playwright que ya está en la caché de npx (no instala nada) y al terminar imprime la hora de expiración. Si algún día no encuentra Playwright, copia el token a mano: abre <https://ejusticia.cjf.gob.mx/BuscadorSISE/> en Chrome, pestaña Network, haz una búsqueda cualquiera, elige una petición a `apisise.cjf.gob.mx`, copia el valor de la cabecera `Authorization` (sin el prefijo `Bearer `) y pégalo en `api/harvest/token.txt`.

`token.txt` está en `.gitignore`. El cosechador avisa en la bitácora cuando faltan menos de 10 minutos para que expire y se detiene limpio, con el estado guardado, en cuanto expira.

Un token de 8 horas no cubre dos noches, y el proceso sí sobrevive al día: por eso el cosechador **relee `token.txt` cada vez que despierta de la espera horaria** (y antes de empezar cada mes, si el archivo cambió de fecha de modificación). Basta con reminterar antes de las 23:00 y el proceso sigue solo; si el token releído también está vencido, aborta limpio como antes.

## Lanzar

```bash
api/harvest/run_tonight.sh
```

Lanza con `nohup`, deja el PID en un archivo `harvest.pid` junto al script e imprime los comandos de consulta y de paro. Variables de entorno que acepta: `PARTITION` (day o month; por omisión `day`), `SCHEDULE` (por omisión `23:00-06:45`), `START` y `END`.

La ventana horaria significa que el proceso se puede lanzar a las 22:00 y se queda dormido, revisando cada 5 minutos, hasta las 23:00; a las 06:45 pausa el mes en curso y espera a la noche siguiente. Si lo dejas vivo, sigue solo; si lo matas, relanzarlo retoma donde iba.

Para un solo mes, o para probar:

```bash
python3 api/harvest/harvest_metadata.py --months 2019-03 --partition day
```

## Consultar y detener

```bash
python3 api/harvest/harvest_metadata.py --status     # tabla por mes y ETA
tail -f api/harvest/logs/harvest.log
kill -TERM $(cat api/harvest/harvest.pid)            # termina la página en curso y guarda
```

`--status` lee solo `state.json`: no toca la red ni necesita token. Muestra, por mes, estado, hits declarados, filas escritas y rebanadas hechas sobre el total; cuántas rebanadas quedaron envenenadas; luego el ritmo medido, la estimación de lo que falta y las cifras de pérdida que se explican más abajo.

## Dónde caen los datos

- Un archivo `AAAA-MM.jsonl` por mes dentro de `api/harvest/data/metadata/` — una fila cruda por línea, tal como la devuelve la API, sin transformar.
- `api/harvest/data/metadata/state.json` — estado por mes: `status`, `total_hits`, rebanadas con su rango de fechas y filtros, filas, bytes escritos y segundos acumulados. Las rebanadas que se partieron dejan además un registro en `parents` con sus hits declarados y las filas que sumaron sus hijos.
- `api/harvest/logs/harvest.log` — rotativa, 10 MB × 5, una línea por petición.

La reanudación **de la cosecha principal** se apoya en el contador de bytes: si el proceso muere a media escritura, al relanzar se trunca el archivo del mes hasta el último límite bueno y se sigue desde ahí. Nunca quedan líneas partidas.

La pasada de rescate **no da esa garantía**: añade al `.jsonl` sin frontera de bytes y guarda el estado después de escribir, así que una muerte a media escritura puede dejar una línea partida que nadie limpia. Y un `SIGTERM` pierde la rebanada en curso entera —hasta unos 90 s de trabajo—, porque las filas de una rebanada solo se escriben cuando termina de acorralarla.

## Lo que ya está cosechado

La cosecha principal **terminó el 7 de septiembre de 2026**: los **184 meses** de 2011-01 a 2026-04 están en `done`, con **4,170,387 filas al cierre de la cosecha principal**, todos en modo `day`. Con lo que añadió la pasada de rescate, hoy hay **4,317,334 filas** en `data/metadata/`. Los meses terminados se saltan al relanzar. El `state.json` recuerda la partición con la que se cosechó el directorio y **rechaza** ejecutar la otra sobre él, para que no se mezclen meses completos con meses paginados.

Los archivos arrastran **652 líneas duplicadas por la terna** `(AsuntoNeunId, SintesisOrden, NumeroOrden)` —489 en 2014-01 y 47 en 2014-05, el resto disperso—, heredadas del código viejo que paginaba y reintentaba, no de la pasada de rescate. No se limpian en los archivos: **la deduplicación va en la carga**, que es donde la terna funciona como clave.

Lo que quedó sin cosechar son 11,613 rebanadas menos 11,308 completas: **233 envenenadas** (todas de 2014; 207 días enteros y 26 hojas ya partidas de 2014-01), **30 fallidas** (2011 y 2013-10..12) y **42 parciales**. Las dos primeras clases las recupera la pasada de rescate; las parciales se quedan como están.

## Pasada de rescate

`rescue_poisoned.py` recupera las rebanadas `poisoned` y `failed` acorralándolas **por listas de valores hasta el órgano culpable**: tercios de la lista de 32 estados, luego tercios del catálogo de órganos dentro del estado culpable. Dentro del órgano culpable recorre hoja por hoja páginas de `size=1`, único punto donde se pagina, porque una fila envenenada solo se aísla así. Tercios y no mitades porque con varios culpables posibles el costo por culpable es (k/ln k)·ln n, mínimo entero en k = 3.

```bash
python3 api/harvest/rescue_poisoned.py --days 30-05-2014 --dry-run   # una rebanada, sin escribir
python3 api/harvest/rescue_poisoned.py                               # la pasada entera
python3 api/harvest/rescue_poisoned.py --status                      # recuperado y perdido por mes
tail -f api/harvest/logs/rescue.log
```

Escribe las filas recuperadas en el `.jsonl` del mes con la misma deduplicación por terna, y deja en cada rebanada del `state.json` un bloque `rescue` (hits, filas, asuntos, perdidos, peticiones por nivel, culpables y hojas opacas). La rebanada pasa a `rescued` si no quedó ningún bloque ilegible, y a `rescued_partial` si quedaron hojas opacas o páginas envenenadas; la pérdida por diferencia se informa aparte, porque la fuente casi siempre pierde algo. Es reanudable: salta lo que ya tiene bloque `rescue`. Los estados culpables y sus órganos se guardan en `rescue_memory` y sirven de atajo —una petición «todos los estados menos los culpables conocidos»—, nunca de suposición: si ese bloque devuelve 500, se acorrala la rebanada entera; solo se memoriza al culpable cuya respuesta fue 500 + `text/html`, no al que falló por red, porque únicamente el envenenamiento es estable en el tiempo.

Hay un tercer desenlace además de sana y envenenada: la **hoja opaca**, donde ni `size=1` responde. Ahí no se recupera ni una fila y la pérdida solo se deduce por diferencia contra los hits declarados de la rebanada; cuando la fuente tampoco declara hits, la pérdida queda en `null` —desconocida, que no es cero— y los dos `--status` la cuentan aparte, en «rebanadas sin cifra de pérdida».

### Resultado de la pasada

Corrida del **7 al 8 de septiembre de 2026** sobre las **263 rebanadas** de la cola (233 envenenadas y 30 fallidas): **146,947 filas recuperadas**, **5,596 asuntos perdidos**, **387 hojas opacas**, **7,139 peticiones**. La corrida se cortó sola a las 09:25 del 8 con un 401 —la máquina se suspendió a media petición y el token de 8 h expiró mientras dormía—, y el relanzamiento retomó las 65 rebanadas que faltaban sin repetir ni perder ninguna. Ninguna de las 263 salió `rescued`: en todas quedó al menos una hoja opaca o una página envenenada.

Para 2014, que es donde estaba el 99 % del volumen, la pérdida queda en **5,442 filas de las 177,367 declaradas por la fuente, el 3.1 %**: 5,289 en hojas opacas y 153 filas envenenadas una por una. La unidad es la fila, la misma en la que la fuente declara sus conteos, y en 2014 una fila es casi siempre un asunto.

## Fallas conocidas de la fuente

Dos cosas distintas devuelven HTTP 500 y el script las trata distinto:

- **Cuelgue aleatorio**: alrededor del 10 % de las peticiones se quedan colgadas hasta el corte del gateway a los 60 s. Se reintenta hasta 3 veces con backoff y, si sigue fallando, la rebanada se difiere al final del mes: en la práctica responde bien al rato.
- **Rebanada envenenada**: hay rangos de fechas que devuelven 500 **siempre**, con cuerpo `text/html` (la página de error por omisión de Django, 145 bytes) y con cualquier `size` útil. Es un registro que el backend no puede serializar y que tumba toda consulta que lo incluya: 03-01-2011 falla entero, y con él fallaría cualquier plan que pidiera enero de 2011 de una sola vez.

La diferencia entre las dos es **determinista y se lee en la respuesta**, no en el reloj: una envenenada responde `500` con `Content-Type: text/html`, una sana responde `application/json` y un cuelgue del gateway no responde nada (`requests.Timeout` a los 60 s). La latencia no distingue: medido el 26 de agosto, las envenenadas tardan entre 0.3 y 6.7 s, igual que las sanas.

### Qué hace la cosecha con cada una

- **Envenenada** (500 + `text/html`): no se reintenta, no se difiere y **no se parte**. Se marca la rebanada `poisoned` en `state.json` con su rango de fechas, sus filtros y sus hits, se escribe un `WARNING` y se sigue con la siguiente. Los hits se averiguan con una petición extra de `size=1`, que sobre una rebanada envenenada sí responde 200; `--no-probe-poisoned-hits` la ahorra y deja `hits: null`.
- **Cuelgue o 500 que no es `text/html`**: se reintenta 3 veces con backoff, la rebanada se difiere al final del mes y, si al volver a ella vuelve a fallar, queda en `failed`. Si al volver responde el 500 permanente, se marca `poisoned`.
- **No cabe en una página**: es el único motivo por el que hoy se parte una rebanada, que era para lo que existía la partición. La escalera es rango de fechas por bisección hasta un solo día, luego `CircuitoRegion` (34 valores), `OrganoTipo` (5), `EspecialidadOrgano` (22) y `OrganoNombreOficial`, este último pedido a `catalogos/` con los filtros ya fijados de la rebanada, de modo que la lista son los ~10 órganos de esa combinación y no los ~800 del país. Si aun con las cuatro dimensiones fijadas un día sigue sin caber, queda en `failed`.

Esas cuatro dimensiones son particiones exhaustivas del índice: comprobado el 25 de agosto, la suma de hits de los hijos da exactamente el total del padre (por especialidad, y por órgano en 17-01-2011 [PRIMER CIRCUITO] [Juzgado de Distrito] [Administrativa]: 37 = 37, residuo 0). Si alguna vez no cuadra, el cosechador escribe un `WARNING` con el faltante y lo contabiliza.

**Lo que ya no se hace**: paginar la hoja con páginas de 100, 10 y 1 para dejar sola a la fila culpable. Esa era la última rama de la escalera y se retiró el 26 de agosto, porque la paginación no es fiable por diseño —el orden del resultado cambia entre dos llamadas idénticas, así que solo una petición que cubra la rebanada entera la lee fielmente— y porque aislar la fila mala cuesta decenas de peticiones por rebanada. La función `harvest_by_page` sigue en el archivo, sin usarse, reservada por si alguna vez hiciera falta; la **pasada de rescate** que lee las `poisoned` y las `failed` vive aparte, en `rescue_poisoned.py`.

Las rebanadas envenenadas se listan con `--list-poisoned`, una por línea en JSON (mes, etiqueta, fechas, filtros y hits), que es justamente la entrada de la pasada de rescate. `--retry-failed` y `--retry-partial` **no las tocan**: reabrirlas solo repetiría el mismo 500.

Las hojas de las que no se recupera ni una fila se marcan `failed`, se registran en la bitácora y salen en `--status`; `--retry-failed` las reabre. Las que quedaron a medias en cosechas anteriores siguen en `partial` con su `rows_missing` e `isolated_bad`.

Al terminar la cosecha completa hay que pasar `--retry-failed`, que reabre las hojas `failed` (cero filas) y **no** las `partial`. Para esas está `--retry-partial`, que se puede combinar con el anterior:

```bash
python3 api/harvest/harvest_metadata.py --partition day --retry-failed --retry-partial
```

Una hoja reabierta se vuelve a correr **entera**, no desde donde se quedó. Las filas que su primera pasada ya escribió siguen en el `.jsonl`, así que al escribir se descartan las que ya están (por la terna `AsuntoNeunId` + `SintesisOrden` + `NumeroOrden`) y solo se añaden las nuevas. La contabilidad de la hoja y de sus padres se revierte al reabrirla, de modo que nada se cuenta dos veces; si la segunda pasada trae menos filas que la primera, el conteo del mes queda por debajo de lo que hay en el archivo, y la deduplicación al consolidar —que hay que hacer de todos modos— es la que manda.

### Contabilidad de lo perdido

`--status` cierra con dos cifras:

- **filas perdidas contabilizadas** (`rows_missing`): la diferencia entre los hits que declaraba una rebanada y las filas que de verdad se escribieron. Se cobra al nivel más bajo que la conoce —la hoja paginada— y cada padre solo carga con lo que sus hijos no reportaron ya, así que nada se cuenta dos veces. `total_hits` cuenta filas (resoluciones), no asuntos, así que la diferencia se mide en filas; una rebanada que escribe más filas que hits declarados aporta cero, no un saldo negativo.
- **rebanadas envenenadas** (`poisoned`): cuántas rebanadas devolvieron 500 + `text/html` y con cuántos hits declarados, que es el tamaño de lo que la pasada de rescate tiene por delante. Sus hits se cargan como pérdida a sus padres, así que la cifra anterior ya los incluye.
- **filas envenenadas aisladas** (`isolated_bad`): herencia de la escalera de páginas retirada el 26 de agosto; cuenta las páginas de tamaño 1 que seguían devolviendo 500 en los meses cosechados antes de ese día. La cosecha nueva no la incrementa.

## Qué hacer después con los datos

Las filas son de resolución, no de asunto: un mismo expediente genera varias. La llave para deduplicar es la terna **(`AsuntoNeunId`, `SintesisOrden`, `NumeroOrden`)**. En modo `day` no debería haber duplicados dentro de un mes, pero conviene deduplicar de todos modos al consolidar, porque las rebanadas partidas por circuito pueden traslaparse si un asunto cambia de órgano.

```bash
cat api/harvest/data/metadata/*.jsonl | python3 -c "
import json, sys
seen = set()
for line in sys.stdin:
    row = json.loads(line)
    key = (row['AsuntoNeunId'], row['SintesisOrden'], row['NumeroOrden'])
    if key not in seen:
        seen.add(key)
        sys.stdout.write(line)
" > todo-dedupe.jsonl
```

El campo `AsuntoNeunId` es además la llave para bajar después el PDF, por una cadena de tres saltos: (1) la búsqueda entrega el `AsuntoNeunId` y los campos de orden (`SintesisOrden`, `NumeroOrden`); (2) el `idOrgano`, que no viene en la búsqueda ni en la ficha por expediente, se aprende una vez por órgano de cualquier URL ya resuelta (los primeros cuatro dígitos de `arch=`); (3) con ambos se arma el nombre del documento en `sise.cjf.gob.mx/SVP/word1.aspx?arch=…&sec=…&svp=1`, que responde `application/pdf` **sin autenticación** e ignora la extensión, con dos patrones de nombre (moderno `<idOrgano><NEUN><SintesisOrden:3>`, antiguo `<idOrgano><NEUN><SintesisOrden:4><NumeroOrden:3>`). El detalle completo —patrones de nombre, mapa parcial de `idOrgano` y los desenlaces cuando el archivo no existe— está en la referencia sobre la fuente en `docs/` (submódulo privado: si no tienes acceso, esta descripción es todo lo que hace falta para reconstruir la cadena).
