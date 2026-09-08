"""Clasificador de actos jurisdiccionales a partir de Tema + Sintesis.

Las etiquetas son ACUÑADAS aquí (no vienen de la fuente): la fuente solo da
texto libre. Ver el reporte para la glosa de cada una.

El índice del BuscadorSISE no publica solo sentencias de fondo: publica
cualquier acto con síntesis (acuerdos, audiencias, suspensiones). Por eso la
primera decisión es el TIPO DE ACTO, y solo dentro de `fondo` hay sentido.
"""
import re
import unicodedata


def norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s.lower()).strip()


# El orden importa: primero se neutralizan las negaciones para que el
# detector de «ampara» no las cuente como concesión.
RE_NIEGA_FONDO = re.compile(
    r"no\s+(?:se\s+)?ampara\s+ni\s+protege"
    r"|no\s+ampara"
    r"|niega\s+(?:el\s+)?(?:amparo|la\s+proteccion)"
    r"|se\s+niega\s+el\s+amparo"
    r"|justicia\s+de\s+la\s+union\s+no\s+ampara"
    r"|niega\s+la\s+proteccion\s+(?:de\s+la\s+justicia|constitucional)"
)
RE_AMPARA = re.compile(
    r"\bampara\b|\bamparan\b|ampara\s+y\s+protege"
    r"|concede\s+(?:el\s+)?(?:amparo|la\s+proteccion)"
    r"|otorga\s+(?:el\s+)?amparo"
    r"|\bconcede\s+el\s+amparo\b"
)
RE_SOBRESEE = re.compile(r"sobresee|sobreseimiento|sobreseer|\bsfa\b")
RE_DESECHA = re.compile(
    r"desech|no\s+presentada|no\s+se\s+admite|inadmis|no\s+admite\s+la\s+demanda"
)
RE_INCOMP = re.compile(r"incompeten|declina|competencia\s+por\s+razon")
RE_SUSP = re.compile(r"suspension|suspende|incidental")
RE_TRAMITE = re.compile(
    r"notifiquese|agreguese|por\s+recibido|tengase\s+por|formese\s+expediente"
    r"|radicacion|previene|prevencion|informe\s+justificado|glosese|vista\s+la"
    r"|se\s+difiere|difiere\s+la\s+audiencia|acuerdo|oficio|requiere"
)
RE_AUDIENCIA = re.compile(
    r"audiencia\s+constitucional|audiencia\s+incidental|declara\s+abierta"
    r"|acta\s+de\s+audiencia|siendo\s+las\s+\w+\s+horas|mesa\s+[ivx]+"
)
RE_RESOLUTIVO = re.compile(
    r"r\s*e\s*s\s*u\s*e\s*l\s*v\s*e|resuelve|\bunico\.|\bprimero\.|sentencia"
    r"|por\s+lo\s+expuesto\s+y\s+fundado"
)

# Salud: el tema del proyecto.
RE_SALUD = re.compile(
    r"\bsalud\b|\bimss\b|\bissste\b|instituto\s+mexicano\s+del\s+seguro"
    r"|seguridad\s+social|medicament|tratamiento\s+medic|\bhospital"
    r"|atencion\s+medic|servicio\s+medic|\bmedic[oa]s?\b|enfermedad"
    r"|\boncolog|quimioterapia|\bvih\b|\bcancer\b|insabi|seguro\s+popular"
    r"|derechohabiente|pension\s+por\s+invalidez|incapacidad\s+permanente"
    r"|\bcofepris\b|prestacion\s+de\s+servicios\s+de\s+salud|\bimss[- ]bienestar"
)
# Núcleo estrecho, sin los términos que también aparecen en materia laboral.
RE_SALUD_ESTRICTO = re.compile(
    r"\bsalud\b|medicament|tratamiento\s+medic|\bhospital"
    r"|atencion\s+medic|servicio\s+medic|\boncolog|quimioterapia|\bvih\b"
    r"|\bcancer\b|insabi|seguro\s+popular|\bcofepris\b|derecho\s+a\s+la\s+salud"
)


def classify(tema, sintesis):
    """Devuelve (acto, sentido, calidad_sintesis)."""
    t = norm(tema)
    s = norm(sintesis)
    txt = t + " || " + s

    niega = bool(RE_NIEGA_FONDO.search(txt))
    # Se neutraliza la negación antes de buscar la concesión.
    txt_amp = RE_NIEGA_FONDO.sub(" NEG ", txt)
    ampara = bool(RE_AMPARA.search(txt_amp))
    sobresee = bool(RE_SOBRESEE.search(txt))
    desecha = bool(RE_DESECHA.search(txt))
    incomp = bool(RE_INCOMP.search(txt))
    susp = bool(RE_SUSP.search(txt))

    if ampara or niega or sobresee:
        acto = "fondo"
        if ampara and (sobresee or niega):
            sentido = "ampara_parcial"
        elif ampara:
            sentido = "ampara"
        elif niega:
            sentido = "niega"
        else:
            sentido = "sobresee"
    elif desecha:
        acto, sentido = "desechamiento", "desecha"
    elif incomp:
        acto, sentido = "incompetencia", "incompetencia"
    elif susp:
        acto, sentido = "suspension", "suspension"
    elif RE_TRAMITE.search(txt) or RE_AUDIENCIA.search(txt):
        acto, sentido = "tramite", "tramite"
    else:
        acto, sentido = "sin_clasificar", "sin_clasificar"

    # Calidad de captura de la síntesis, independiente del acto.
    if len(s) < 5:
        calidad = "vacia"
    elif RE_AUDIENCIA.search(s) and not RE_RESOLUTIVO.search(s):
        calidad = "encabezado_audiencia"
    elif RE_RESOLUTIVO.search(s):
        calidad = "resolutivo"
    elif len(s) < 60:
        calidad = "muy_corta"
    else:
        calidad = "otra_prosa"

    return acto, sentido, calidad


def is_salud(tema, sintesis, estricto=False):
    txt = norm(tema) + " || " + norm(sintesis)
    rx = RE_SALUD_ESTRICTO if estricto else RE_SALUD
    return bool(rx.search(txt))
