# pdf_text — de PDF de BuscadorSISE a párrafos citables

Convierte cada sentencia descargada en tres artefactos: un `.txt` con los párrafos numerados que la IA leerá y citará, un `.json` con el índice estable de esos párrafos (página, caja, número de líneas, texto) y un `.tail.txt` con la cola administrativa en crudo. Python puro sobre la salida de `pdftotext` (poppler): no necesita entorno virtual ni dependencias.

La limpieza es geométrica, no textual: trabaja sobre el XML de `pdftotext -bbox-layout` porque la geometría resuelve de un golpe lo que el texto plano no puede —la marca de agua y el sello FIREL rotados 90°, los encabezados y pies, las notas al pie, y el orden de lectura, que poppler destroza en los documentos justificados de 2011-2016 porque la marca de agua parte la línea de cuerpo en dos—. Las reglas y las constantes vienen de la exploración de agosto de 2026 (`api/scripts/exploration/pdf-cleaning/`, que se conserva intacta como historia) y de la calibración del 22 de septiembre de 2026 sobre 186 documentos (los 87 de la muestra más los 99 del piloto).

## CLI

    python3 -m pdf_text.run --in <dir-o-lista> --out <dir> [--workers N]

`--in` acepta directorios y archivos sueltos; los PDF se reconocen por número mágico y no por extensión, porque el servidor del CJF entrega muchos con extensión `.docx`. Los directorios se recorren sin bajar a sus subdirectorios salvo que se pase `--recursive`. Los archivos byte a byte idénticos se procesan una sola vez. Cada corrida es reanudable: un documento se salta si su `.json` y su `.txt` ya existen y el `.json` es más nuevo que el PDF (`--force` ignora ese caché). `--workers` es 4 por omisión y nunca pasa de 15 procesos.

## Contrato de salida

Por documento, `<id>.txt` con un párrafo por bloque en la forma `[P0001|p3] texto…`, donde `P0001` es el identificador estable del párrafo y `p3` la página del PDF en la que empieza; y `<id>.json` con el mismo contenido más la geometría: `index` (por párrafo: `id`, `page`, `bbox`, `lines`, `text`, `redactions`, `redaction_lengths`, y `split` cuando un párrafo larguísimo hubo de trocearse por oración), `footnotes` (texto y página de cada nota al pie, que se separan pero no se tiran: traen las citas de tesis y del DOF), `repeated_discarded`, `pages_pdf`, `pages_useful`, `tail_first_page`, `tail_kind`, `pages_firel`, `acuse_pages`, `acuse_interleaved`, `pages_dropped_nonacuse`, `cleaner_version`, `method` y los campos de verificación.

El testado del CJF —corridas de tres o más asteriscos— se sustituye por el token literal `[TESTADO]` tanto en el `.txt` como en el `.json`; el largo original de cada corrida se conserva en `redaction_lengths`, que es el único indicio del tamaño de lo tachado.

Por corrida, `conversion_log.csv` en el directorio de salida, una fila por documento. El log se fusiona con el que ya estuviera ahí: una corrida parcial no borra las filas de los documentos que no tocó.

## Encabezados y pies: bandas móviles

Las bandas fijas de agosto (0.09 y 0.92 del alto de página) se comían el cuerpo: 935 líneas de altura de cuerpo con minúscula inicial caían dentro de ellas, en 57 de los 87 PDF de la muestra del 22 de septiembre, y con ellas párrafos enteros quedaban mutilados sin que ningún indicador lo notara. Los límites se derivan ahora documento por documento, a partir de la geometría de todas sus páginas:

El cuerpo se reparte por todo el alto de la página; el encabezado y el pie se apilan en la misma coordenada, página tras página, con el mismo texto. Un renglón —el conjunto de cajas que comparten centro vertical, porque el folio suele ir al lado del rubro del asunto— se identifica por su firma: sus textos con los dígitos sustituidos por `#` y ordenados alfabéticamente. Neutralizar los dígitos es lo que reconoce al folio, que cambia de número pero no de lugar, y a «A.R. 150/2021»; ordenar los textos es lo que reconoce al encabezado que se refleja en páginas pares e impares. Un renglón es fijo si su firma reaparece a la misma altura —dentro de 0.35 alturas de línea— en al menos `max(3, 30 % de las páginas)`: el porcentaje deja entrar un encabezado que solo está en las nones (50 % de las páginas) y la constante impide que dos líneas de cuerpo parecidas basten.

Cada página se recorre desde sus dos orillas hacia adentro, hasta cuatro renglones por orilla, mientras el renglón sea fijo; esos renglones son encabezado o pie y se etiquetan uno por uno. Lo que queda entre ambos recorridos es el bloque grueso de cuerpo. Los renglones detectados fijan además una banda —hasta el `y1` más bajo del encabezado, desde el `y0` más alto del pie, con un cuarto de altura de línea de holgura— que atrapa el ruido que aparece una sola vez a esa misma altura. La banda nunca invade el bloque de cuerpo, y por eso no basta por sí sola: un encabezado de dos renglones en páginas nones puede bajar más que la primera línea de cuerpo de las pares, y solo el etiquetado renglón a renglón lo alcanza.

El folio acota el pie desde su punto más alto, sin regla aparte: es un renglón fijo como cualquier otro y su `y0` es el borde superior de la banda. La primera página no recibe trato especial: si su membrete no se repite, se queda como párrafos del cuerpo.

Como red de última instancia, una línea de altura de cuerpo que empieza con minúscula nunca es encabezado ni pie: en estas sentencias solo puede ser la continuación de un párrafo. Corta el recorrido desde la orilla y rescata a la línea que la banda iba a tragarse. `band_body_lines` cuenta esos rescates: en la muestra y en el piloto vale 0 en los 186 documentos, y cualquier valor alto diría que la banda de ese documento quedó ancha.

El precio de un umbral conservador se paga en documentos de dos a cinco páginas, donde `max(3, 30 %)` no se alcanza y un membrete corto sobrevive como párrafo suelto. Es la dirección correcta del error: nunca se pierde cuerpo.

## La cola: certificación y acuse FIREL

Una página es acuse si una de sus líneas *es* el título `EVIDENCIA CRIPTOGR…` (en versalitas y línea corta) o si acumula al menos tres marcadores del certificado (`Autoridad Certificadora`, `Archivo Firmado`, `FIRMANTE`, `No Serie:`, `Revocaci`, `Cadena de Firma`, `OCSP`), porque las páginas de continuación del acuse no llevan título. La coincidencia del título tiene que ser de línea y con mayúsculas: el cuerpo de las sentencias dice «(SE ANEXA EVIDENCIA CRIPTOGRÁFICA)» y «la hora de la evidencia criptográfica», y con una coincidencia laxa una de esas menciones trunca el documento entero.

Una página es certificación de versión pública si mide 800 caracteres o menos y trae «hago constar y certific…» junto a «versión pública». Son dos redacciones —«…en términos de lo previsto en los artículos 8, 13, 14, 18…» y «…en esta versión pública no existe información clasificada…»— que comparten ese arranque. El límite de largo es indispensable: la frase también aparece en el cuerpo de algunas sentencias, y un falso positivo trunca el documento. Los 87 de la muestra y los 99 del piloto traen exactamente una, siempre en la última página.

La cola arranca en la primera de las dos páginas, la de certificación o la de acuse, lo que venga antes; de ahí al final no hay cuerpo. Se guarda aparte y en crudo —`pdftotext -raw` de ese rango, sin ninguna regla del limpiador— como `<id>.tail.txt`, porque dice quién firmó, cuándo y qué se testó. `tail_first_page` y `tail_kind` (`certificacion` | `acuse` | `none`) dejan constancia de dónde se cortó y por qué. En la muestra del 22 de septiembre la certificación va siempre *después* del acuse, no antes: 69 documentos cortan por acuse y 18 por certificación, que es el caso de los documentos sin acuse.

`acuse_interleaved` y `pages_dropped_nonacuse` registran qué páginas de la cola no son ni acuse ni certificación. En la muestra del 22 no señalan pérdida —todo eso va al `.tail.txt`— sino páginas que ningún detector identificó: dos, y las dos son hojas de firma del FIREL («Archivo firmado por: …») demasiado cortas para acumular tres marcadores. En la muestra de 20 mil sí señalan pérdida: cuando el título de acuse o la certificación disparan en la página 1, el cuerpo entero se va a la cola y `pages_dropped_nonacuse` suma miles de caracteres con estatus `ok` (arreglo pendiente).

## Versión del limpiador

`cleaner_version` es una constante en `clean.py` y sube con **cualquier** cambio de regla o de constante, también de las que viven en `bands.py` (encabezados y pies) y `tail.py` (la cola). El índice de párrafos solo es estable dado un limpiador fijo: `P0037` significa algo únicamente junto a la versión que lo produjo, y cada valor que la IA extraiga va a citar ese par. Un cambio de versión obliga a reconvertir el corpus antes de comparar citas viejas con nuevas.

## Verificación

Cada documento se contrasta contra una segunda extracción independiente del mismo PDF: `pdftotext -raw` de las páginas útiles (sin la cola). Los dos lados se normalizan igual —sin URLs, sin marca de agua, sin marcas de testado, sin maquetación— y se guarda `text_ratio = len(limpio)/len(crudo)`. Nada se bloquea: el `.txt` se guarda pase lo que pase y el estado solo dice qué mirar a mano.

El ratio crudo no sirve como criterio: no llega a 1.0 en un documento íntegro —el crudo conserva encabezados, pies, folios y el sello de firma que el limpiador tira a propósito— y su valor depende sobre todo de cuánto ruido traiga el PDF, de modo que una banda de ratio no distingue un documento ruidoso de uno mutilado. Se guarda al lado `expected_ratio`, la fracción de caracteres de las páginas útiles que el limpiador decidió conservar, y el grado se decide por `ratio_residual = text_ratio − expected_ratio`.

Un documento sin caracteres limpios es `empty`, aunque el residuo cuadre: sin capa de texto, o con toda su capa clasificada como banda o cola (si todo es encabezado o pie, lo esperado también es 0 y el residuo da 0). El resto se decide por el residuo.

El umbral del residuo es compuesto, un porcentaje más una constante: un documento es `suspect` si `ratio_residual < −(0.015 + 0.02 × expected_ratio)`, es decir, si cae unas 3.4 centésimas por debajo de lo esperado. Calibrado sobre los 184 documentos con medición de la muestra y el piloto, todos genuinos: el residuo va de −0.026 a +0.040, el peor caso es un documento cuyo sello de firma rotado los dos extractores cuentan distinto, y ninguno queda marcado. Con una pérdida sintética de una página de cuerpo el umbral marca 102 de 184 documentos, todos los de hasta unas 36 páginas útiles; en un documento de cien páginas, una página perdida vale menos que el ruido de la medición y ningún umbral sobre este residuo la vería.

`band_body_lines` es la otra mitad de la verificación, y mide lo único que el ratio no puede ver: si una banda se come cuerpo, el ratio no baja, porque esa línea cuenta como ruido descartado a propósito.
