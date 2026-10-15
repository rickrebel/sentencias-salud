# pdf_text — de PDF de BuscadorSISE a párrafos citables

Convierte cada sentencia descargada en tres artefactos: un `.txt` con los párrafos numerados y, al final, las notas al pie numeradas aparte, que la IA leerá y citará; un `.json` con el índice estable de párrafos y notas (página, caja, número de líneas, texto) y un `.tail.txt` con la cola administrativa en crudo. Trabaja sobre la salida de `pdftotext` (poppler) más la fuente por carácter que da `pdfplumber` (en `api/requirements.txt`).

La limpieza es geométrica, no textual: parte del XML de `pdftotext -bbox-layout` porque la geometría resuelve de un golpe lo que el texto plano no puede —la marca de agua y el sello FIREL rotados, los encabezados y pies, las notas al pie, y el orden de lectura, que poppler destroza en los documentos justificados de 2011-2016 porque la marca de agua parte la línea de cuerpo en dos—. Las reglas vienen de la exploración de agosto de 2026 (`api/scripts/exploration/pdf-cleaning/`, que se conserva intacta como historia), de la calibración de septiembre y del diseño del 5 de octubre de 2026, punto por punto.

Los PDF escaneados, sin capa de texto, salen como `empty`: el limpiador no hace OCR, y esos documentos quedan a la espera de un paso de OCR aparte.

## CLI

    python3 -m pdf_text.run --in <dir-o-lista> --out <dir> [--workers N]

`--in` acepta directorios y archivos sueltos; los PDF se reconocen por número mágico y no por extensión, porque el servidor del OAJ entrega muchos con extensión `.docx`. Los directorios se recorren sin bajar a sus subdirectorios salvo que se pase `--recursive`. Los archivos byte a byte idénticos se procesan una sola vez. Cada corrida es reanudable: un documento se salta si su `.json` y su `.txt` ya existen y el `.json` es más nuevo que el PDF (`--force` ignora ese caché). `--workers` es 4 por omisión y nunca pasa de 15 procesos.

## Contrato de salida

Por documento, `<id>.txt` con un párrafo por bloque en la forma `[P0001|p3] texto…`, donde `P0001` es el identificador estable del párrafo y `p3` la página del PDF en la que empieza. Después del último párrafo, si el documento tiene notas al pie, una línea separadora `=== NOTAS AL PIE ===` y una nota por bloque en la misma forma, `[N0001|p5] 1 texto…`, numeradas en orden de aparición (página y posición) y con su número volado original al inicio del texto; los `P` no cambian de numeración por las notas. `<id>.json` con el mismo contenido más la geometría: `index` (por párrafo: `id`, `page`, `bbox`, `lines`, `text`, `redactions`, `redaction_lengths`, y `split` cuando un párrafo larguísimo hubo de trocearse por oración), `footnotes` (por nota: `id` `N…`, `page`, `bbox`, `lines`, `num`, `text`, `redactions`, `redaction_lengths`, los mismos campos que un párrafo para que `N0007` resuelva igual que `P0037`; las notas se separan del cuerpo pero no se tiran: traen las citas de tesis, de fojas y de artículos), `repeated_discarded`, `pages_pdf`, `pages_useful`, `tail_first_page`, `tail_kind`, `pages_firel`, `acuse_pages`, `acuse_interior_pages`, `acuse_interleaved`, `pages_dropped_nonacuse`, `cleaner_version`, `method`, los campos de verificación y los de diagnóstico del limpiador (`body_size`, `limits`, `indent`, `labels`, `footnote_check`, `doy_fe`, `doubts`, entre otros).

El testado de la fuente —corridas de dos o más asteriscos— se sustituye por el token literal `[TESTADO]` tanto en el `.txt` como en el `.json`; el largo original de cada corrida se conserva en `redaction_lengths`, que es el único indicio del tamaño de lo tachado.

`<id>.tail.txt` es `pdftotext -raw` de la cola, sin ninguna regla del limpiador: no es texto citable, pero dice quién firmó, cuándo y qué se testó.

Por corrida, `conversion_log.csv` en el directorio de salida, una fila por documento. El log se fusiona con el que ya estuviera ahí: una corrida parcial no borra las filas de los documentos que no tocó.

## El limpiador, paso a paso

`clean2.py` es la secuencia y `clean2_geo.py` la lectura geométrica. `analyze()` devuelve todas las decisiones antes de armar el índice, para que el visualizador dibuje exactamente lo que produce el `.txt`.

1. **Cajas con fuente.** Cada palabra de poppler recibe de pdfplumber el tamaño, la familia, el color y la matriz de sus caracteres; «misma letra» se compara en tamaño de fuente y no en altura de caja, que depende de la familia.
2. **Líneas visuales primero.** Los segmentos que comparten renglón se fusionan antes de cualquier descarte: en los justificados antiguos cada palabra llega como línea propia.
3. **Cola.** Es el último bloque contiguo de páginas de acuse FIREL y de certificación de versión pública, recorrido desde el final; la certificación solo cuenta en la última o la penúltima página, y una página corta con la frase «hago constar y certific…» en medio del cuerpo no corta nada. Las hojas de firma con rastro débil del FIREL y las páginas vacías entran a la cola solo detrás de un acuse o una certificación. Un acuse completo a mitad del documento se aparta del cuerpo sin cortarlo (`acuse_interior_pages`).
4. **Límites por paridad.** La caja de texto se mide por separado en páginas nones y pares (márgenes espejo), con percentiles en x y moda y máximo compartido en y.
5. **Exclusión por estilo.** Rotados, marca de agua (gris y girada o gigante, o el texto exacto o invertido) y sellos del FIREL (letra diminuta que duplica texto visible, texto azul girado, código de barras) se excluyen por palabra, nunca dentro de un párrafo.
6. **Tamaño de cuerpo.** Es el pico mayor y abundante de tamaños, no la mediana.
7. **Bandas y folio.** Encabezados y pies por renglones fijos de cada paridad; el folio se reconoce porque su número sigue a la página con un desfase constante. Como red, una línea de tamaño de cuerpo que empieza en minúscula nunca es encabezado ni pie (`band_body_lines` cuenta esos rescates).
8. **Notas al pie.** El bloque arranca bajo la raya separadora de Word, o en una línea de letra menor que el cuerpo, bajo la mitad de la página, que empieza con un número de nota volado o confirmado por una llamada de la misma página; una línea a tamaño de cuerpo lo cierra. Las llamadas del cuerpo se cotejan con las notas (`footnote_check`).
9. **Párrafos.** Sangría aprendida por documento, salto vertical, línea corta que cierra oración y cambio de tamaño abren párrafo; los títulos grandes en mayúsculas son párrafo propio. Un párrafo sin señal tipográfica de más de 1 200 caracteres se trocea por oración (`split`).
10. **Reajuste por cuadrante.** Red final: una línea descartada como repetida, folio, encabezado o pie que cae dentro del cuadrante de un párrafo, con el tamaño de su línea vecina, vuelve a él (`rescued`).
11. **Página 1 (con duda abierta).** Queda fuera del cálculo de límites y usa los de las nones; un título de sección arriba del tope se conserva.
12. **Repetidos y testado.** Se descartan las líneas visuales completas que se repiten a la misma altura en la mitad de las páginas; el testado nunca cuenta como repetido.
13. **«Doy fe».** La última aparición se registra como señal de cierre (`doy_fe`), no como corte.

Las reglas que aún se implementan con duda quedan listadas en el campo `doubts` de cada `.json`.

## Versión del limpiador

`cleaner_version` es la constante `CLEANER_VERSION` de `clean2.py` y sube con **cualquier** cambio de regla o de constante, también de las de `clean2_geo.py`. El índice de párrafos y notas solo es estable dado un limpiador fijo: `P0037` o `N0007` significan algo únicamente junto a la versión que los produjo, y cada valor que la IA extraiga va a citar ese par. Un cambio de versión obliga a reconvertir el corpus antes de comparar citas viejas con nuevas.

## Verificación

`verify.py` contrasta cada documento contra una segunda extracción independiente del mismo PDF: `pdftotext -raw` de las páginas útiles (sin la cola). Los dos lados se normalizan igual —sin URLs, sin marca de agua, sin marcas de testado, sin maquetación— y se guarda `text_ratio = len(limpio)/len(crudo)`, donde lo limpio son párrafos y notas. Nada se bloquea: el `.txt` se guarda pase lo que pase y el estado solo dice qué mirar a mano.

El ratio crudo no sirve como criterio: no llega a 1.0 en un documento íntegro —el crudo conserva encabezados, pies, folios y el sello de firma que el limpiador tira a propósito— y su valor depende sobre todo de cuánto ruido traiga el PDF, de modo que una banda de ratio no distingue un documento ruidoso de uno mutilado. Se guarda al lado `expected_ratio`, la fracción de caracteres de las páginas útiles que el limpiador decidió conservar, y el grado se decide por `ratio_residual = text_ratio − expected_ratio`.

Un documento sin caracteres limpios es `empty`, aunque el residuo cuadre: sin capa de texto, o con toda su capa clasificada como banda o cola (si todo es encabezado o pie, lo esperado también es 0 y el residuo da 0). El resto se decide por el residuo.

El umbral del residuo es compuesto, un porcentaje más una constante: un documento es `suspect` si `ratio_residual < −(0.015 + 0.02 × expected_ratio)`, es decir, si cae unas 3.4 centésimas por debajo de lo esperado. Se calibró en septiembre sobre 184 documentos genuinos: el residuo iba de −0.026 a +0.040 y ninguno quedaba marcado; con una pérdida sintética de una página de cuerpo el umbral marcaba todos los documentos de hasta unas 36 páginas útiles. En un documento de cien páginas, una página perdida vale menos que el ruido de la medición y ningún umbral sobre este residuo la vería.

`band_body_lines` es la otra mitad de la verificación, y mide lo único que el ratio no puede ver: si una banda se come cuerpo, el ratio no baja, porque esa línea cuenta como ruido descartado a propósito.

## Visualizador

    python3 -m pdf_text.viz --pdf <ruta> --out <dir>
    python3 -m pdf_text.viz --batch <archivo> --out <dir> [--workers N]
    python3 -m pdf_text.viz --triage --out <árbol> --other <árbol previo> --batch <tsv>

`viz.py` rasteriza cada página a PNG y contornea cada caja de poppler con el color de la decisión del limpiador (cuerpo, rescatado, encabezado, pie, rotado, marca de agua, folio, nota, testado, título, sello, cola), más la caja de cada párrafo, los límites de paridad de la página, las líneas de banda y de notas, la página de cola (acuse, certificación o arrastrada) y la última «Doy fe». Cada documento lleva su `summary.json`, un visor de páginas y un índice; el lote, un índice con todos.

`--triage` no rasteriza: compara cada documento con su gemelo de otro árbol y lo reparte en `limpios` (estado `ok` y ninguna señal), `dudosos` (una o dos señales) y `evidentes` (estado `suspect` o `empty`, tres señales o más, o pérdida de caracteres frente al árbol previo); la regla completa la imprime el propio comando. `limpios` significa «sin señal del triage», no «limpio de composición»: el triage no mira si el cuerpo trae anexos transcritos.
