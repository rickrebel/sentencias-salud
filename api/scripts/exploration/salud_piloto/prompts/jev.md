<!--
Jev prompt of the salud pilot (arms jev_meta and jev_content). The
runner reads the `## …` sections below; HTML comments never reach the
model. A decisions model writes nothing: it gets a `state` (the
metadata block or the clean2 text) and typed questions. The methodology
(definition, levels, categories, borde values) lives in scheme.json and
is shared with the Sonnet arms; this file only holds the input format
and the wording of the questions built from the scheme:
  - nivel: one `choice` over the scheme's levels, criteria = their
    definitions, context = estado + the scheme's definition;
  - one `noul` per category, worded with `categoría` ({nombre} is the
    category's name), context = estado, its definition sent as the
    criterion;
  - borde: one `choice` over the scheme's borde values;
  - dónde: the tag that best supports the level (two passes over
    windows of tags when there are more than 255).
While the word PLACEHOLDER is anywhere in this file or in scheme.json,
run.py refuses a full run (no --ids / --limit / --allow-placeholder).
-->

## estado: metadatos

El estado son los metadatos que el Consejo de la Judicatura Federal publica de un asunto de amparo indirecto en México: expediente, órgano, circuito y, por cada resolución, fecha, materia, tema y síntesis capturados por el juzgado. Cada línea empieza con una etiqueta entre corchetes, como [tema_1], que identifica el campo.

## estado: texto

El estado es el texto completo de una o más resoluciones de un asunto de amparo indirecto en México, en versión pública (los datos personales aparecen como [TESTADO]). Cada párrafo empieza con una etiqueta como [P0142|p7], donde P0142 identifica el párrafo y p7 es la página; las notas al pie van al final, tras «=== NOTAS AL PIE ===», con etiquetas como [N0003|p5]. Si el asunto tiene varias resoluciones, cada una empieza con «=== DOCUMENTO 2 DE 3 ===» y sus etiquetas llevan el número de documento delante, como [D2-P0013|p4].

## categoría

¿Lo que se reclama o se resuelve en el asunto cae, al menos en parte, en la categoría «{nombre}»?

## dónde

¿Qué campo, párrafo o nota al pie (por su etiqueta) sustenta mejor el lugar que ocupa el derecho a la salud en el asunto?

## dónde: tramo

¿Qué tramo de párrafos o notas al pie (por sus etiquetas) sustenta mejor el lugar que ocupa el derecho a la salud en el asunto?
