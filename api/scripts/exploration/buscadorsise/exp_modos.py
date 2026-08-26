"""Barrido de los 4 modos de busqueda de texto del BuscadorSISE.
Ventana fija 01-01-2011 .. 30-04-2026. Todos los conteos son total_hits."""
import sys, json, time
sys.path.insert(0, '/home/rick/dev/ibero/sentencias-salud/docs/records/assets/2026-08-25-buscadorsise/modos-busqueda')
from sise import hits, cond

R = {}
def rec(clave, filtros, **kw):
    r, _ = hits(filtros, **kw)
    R[clave] = r
    print(clave, r, flush=True)
    return r

DESCRIPTORES = ['salud', 'derecho a la salud', 'medicamento', 'medicamentos',
                'IMSS', 'ISSSTE', 'hospital', 'atención médica', 'enfermedad']

rec('_universo_ventana', {})

# 1. descriptor x modo, solo Contenido
for d in DESCRIPTORES:
    for op in (1, 2, 3):
        rec(f'C|{d}|op{op}', {'Contenido': cond(d, op)})
    rec(f'C|{d}|op4r100', {'Contenido': cond(d, 4, 100)})

# 2. Contenido+Tema (como manda la interfaz) vs solo Contenido, y solo Tema
for d in ['salud', 'derecho a la salud', 'atención médica']:
    rec(f'CT|{d}|op1', {'Contenido': cond(d, 1), 'Tema': cond(d, 1)})
    rec(f'T|{d}|op1', {'Tema': cond(d, 1)})

# 3. OR: al menos una palabra
rec('C|salud medicamento hospital|op2', {'Contenido': cond('salud medicamento hospital', 2)})
rec('C|salud medicamento hospital|op1', {'Contenido': cond('salud medicamento hospital', 1)})
for w in ['salud', 'medicamento', 'hospital']:
    pass  # ya medidos arriba

# 4. Rango de palabras: distancia
for r_ in (1, 2, 3, 5, 10, 20, 50, 100):
    rec(f'C|salud medicamento|op4r{r_}', {'Contenido': cond('salud medicamento', 4, r_)})
rec('C|salud medicamento|op1', {'Contenido': cond('salud medicamento', 1)})
rec('C|salud medicamento|op2', {'Contenido': cond('salud medicamento', 2)})
rec('C|salud medicamento|op3', {'Contenido': cond('salud medicamento', 3)})

# 5. Excepto: complemento?
rec('C|salud|op3+check', {'Contenido': cond('salud', 3)})

# 6. acentos / mayusculas
for v in ['atención médica', 'atencion medica', 'ATENCIÓN MÉDICA', 'Atencion Medica']:
    rec(f'C|{v}|op1|acentos', {'Contenido': cond(v, 1)})

# 7. stemming
for v in ['medicamento', 'medicamentos', 'medicamentito', 'enfermo', 'enferma',
          'enfermedad', 'enfermedades', 'enfermar', 'hospital', 'hospitales',
          'hospitalario', 'hospitalizacion']:
    rec(f'C|{v}|op1|stem', {'Contenido': cond(v, 1)})

# 8. palabras vacias
for v in ['derecho a la salud', 'derecho salud', 'derecho de la salud',
          'derecho la a salud']:
    rec(f'C|{v}|op1|vacias', {'Contenido': cond(v, 1)})

# 9. IdTipoCampo: que pasa si cambia
for tc in (0, 1, 2, 3):
    rec(f'C|salud|op1|tipocampo{tc}', {'Contenido': cond('salud', 1, campo=tc)})

json.dump(R, open('resultados-modos.json', 'w'), ensure_ascii=False, indent=1)
print('OK', len(R))
