#!/usr/bin/env python3
"""Control de la semilla (a): antes de creer que los desplazados
desaparecieron, hay que comprobar que el cruce de nombres funciona.

Compara la tasa de aparicion en el directorio vivo del OAJ (2026-09-08)
de tres poblaciones que se cruzan con el mismo normalizador:
  - titulares de juzgados que NO cambiaron (career_no_change): control,
    deberian aparecer casi todos
  - titulares desplazados por una persona electa
  - todos los jueces de distrito de las fichas SEVIE previas
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from people_lib import DIREC, OFF, load_labeled, name_key, read  # noqa


def main():
    direc = {}
    for r in read(DIREC):
        if r["nombre"]:
            direc.setdefault(name_key(r["nombre"]), r)
    labeled = load_labeled()

    def rate(label, keys):
        keys = [k for k in keys if k]
        hit = [k for k in keys if k in direc]
        print("%-34s n=%3d  en el directorio de hoy %3d  %5.1f%%"
              % (label, len(keys), len(hit), 100.0 * len(hit) / len(keys)))
        return set(keys) - set(hit)

    ctrl = [name_key(r["titular_dominante_antes"]) for r in labeled.values()
            if r["grupo"] == "career_no_change"]
    disp = [name_key(r["titular_dominante_antes"]) for r in labeled.values()
            if r["grupo"] in ("elected_new", "elected_incumbent")]
    otro = [name_key(r["titular_dominante_antes"]) for r in labeled.values()
            if r["grupo"] == "other_change"]
    off = read(OFF)
    jd = [r["match_key_name_sorted"] for r in off
          if r["is_district_judge"] == "True"]

    rate("control career_no_change", ctrl)
    rate("desplazados por electo", disp)
    rate("other_change (titular previo)", otro)
    rate("fichas SEVIE: jueces de distrito", jd)

    # ¿el titular previo era realmente dominante?
    sh = [float(r["titular_antes_share"]) for r in labeled.values()
          if r["grupo"] in ("elected_new", "elected_incumbent")
          and r["titular_antes_share"]]
    sh.sort()
    print("\nshare del titular dominante antes (desplazados): "
          "mediana %.1f%%, p10 %.1f%%, minimo %.1f%%"
          % (sh[len(sh) // 2], sh[len(sh) // 10], sh[0]))
    print("share < 60%%:", sum(1 for x in sh if x < 60), "de", len(sh))

    # ¿cuantos nombres del directorio no cruzan con nada? mide el ruido
    # del normalizador en la otra direccion
    off_keys = {r["match_key_name_sorted"] for r in off}
    d_jd = [r for r in direc.values()
            if r["nivel"] == "JUZGADOS DE DISTRITO"]
    print("\ndirectorio hoy, juzgados de distrito:", len(d_jd),
          Counter(r["cargo"] for r in d_jd).most_common())
    print("de ellos con ficha SEVIE previa:",
          sum(1 for r in d_jd if name_key(r["nombre"]) in off_keys))


if __name__ == "__main__":
    main()
