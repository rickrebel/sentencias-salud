# sentencias-salud

Plataforma y metodología replicable para reunir, preseleccionar y codificar las sentencias de amparo indirecto sobre el derecho a la salud dictadas en México entre 2011 y 2026.

## Qué es

Es la base técnica de una investigación del Centro Interdisciplinario para la Enseñanza Clínica del Derecho de la Universidad Iberoamericana Ciudad de México, que dirige María Fernanda Cobo Armijo. Ricardo Sanginés provee la tecnología y codiseña la metodología. El repositorio es público porque el método es en sí mismo un entregable: cualquiera debería poder repetir la cosecha, la conversión de los documentos y el muestreo, y llegar a los mismos datos.

## La fuente

Las sentencias salen del [BuscadorSISE](https://ejusticia.cjf.gob.mx/BuscadorSISE/), el buscador público de versiones públicas de resoluciones del Poder Judicial de la Federación que opera el Órgano de Administración Judicial (OAJ). No existe una descarga masiva oficial: todo lo que hay aquí se obtiene con las mismas consultas que hace el buscador desde el navegador, con el token anónimo que la página emite al cargarse, sin inicio de sesión.

## Qué hay en código

- [`api/harvest/`](api/harvest/README.md) — cosechador de metadatos. Reúne el índice de las resoluciones de amparo indirecto de enero de 2011 a abril de 2026: 4.3 millones de filas, con una cobertura del 99.9 % contra los conteos que la propia fuente declara día por día. Hace una petición por día y nunca pagina, porque el orden de los resultados cambia entre dos llamadas idénticas; los días que la fuente no puede servir se rescatan acotando por listas de valores (estados, luego órganos) hasta el órgano culpable; dentro de él se recorre hoja por hoja de una fila, único punto donde se pagina, porque una fila envenenada solo se aísla así. Python con `requests`, nocturno y reanudable.
- [`api/pdf_text/`](api/pdf_text/README.md) — de PDF a párrafos numerados y citables. La limpieza es geométrica, sobre la salida de `pdftotext -bbox-layout`: separa marca de agua, sello de firma, encabezados, pies, notas al pie y la cola de certificación, y verifica cada documento contra una segunda extracción independiente. Cada párrafo lleva un identificador estable (`P0037`) que, junto con la versión del limpiador, es lo que cita cualquier valor extraído. Incluye un visualizador de diagnóstico (`viz.py`) que dibuja, página por página, qué decidió el limpiador sobre cada caja de texto. Los PDF escaneados, sin capa de texto, quedan como `empty` a la espera de OCR.
- [`api/scripts/sampling/`](api/scripts/sampling/) — muestras sobre el marco de asuntos de materia administrativa (alrededor de 1.4 millones): una muestra estratificada por año × circuito con asignación proporcional, con piso de uno por celda y una cuota dirigida a los órganos que no son Juzgado de Distrito, una muestra aleatoria simple que no comparte asuntos con la anterior, y la descarga determinista de los documentos de cada muestra a partir de sus metadatos.
- [`api/scripts/exploration/`](api/scripts/exploration/) — exploraciones de la fuente y de los documentos que respaldan las decisiones anteriores: pruebas del comportamiento de la API, conteos del universo ([`universe/`](api/scripts/exploration/universe/README.md)), la exploración original de limpieza de PDF y un banco de OCR para las sentencias escaneadas ([`ocr_bench/`](api/scripts/exploration/ocr_bench/README.md)).

Las pruebas automatizadas y cómo correrlas están en [`TESTING.md`](TESTING.md).

## Principios metodológicos

- **La IA propone y cita; una persona promueve.** Todo valor que extrae un modelo viene con los párrafos que lo sostienen, y ningún modelo escribe en las tablas finales.
- **Catálogos vivos.** Las categorías de codificación no están cerradas de antemano: la IA puede proponer valores nuevos y una persona los aprueba o los descarta.
- **Cascada por certeza.** Lo que un modelo barato decide con certeza no se manda a un modelo más caro; lo que una IA más cara y más lista resuelve, con citas, no se manda a las personas. Cada escalón recibe solo lo que el anterior no pudo cerrar, y el costo baja como consecuencia.
- **Muestras antes que universo.** Cada regla se mide primero sobre muestras estratificadas, con pesos de diseño conocidos, antes de aplicarse al conjunto completo.

## Estado

La metodología es provisional hasta que haya ejercicios de codificación y casos revisados que la respalden. Hoy existen el cosechador, el conversor de PDF a texto, el muestreo y las exploraciones; todavía no hay aplicación web (ni backend en Django ni frontend). Lo siguiente es montar la plataforma sobre un motor compartido de catálogos, en el que las categorías y sus valores se definen por esquema.

## Documentación interna

`docs/` es un submódulo privado: contiene transcripciones de reuniones, presupuestos y notas de proceso. Sin acceso, la carpeta aparece vacía y todo lo demás del repositorio funciona igual; los README y los docstrings de cada módulo bastan para reproducir el método.
