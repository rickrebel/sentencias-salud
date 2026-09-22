import json, glob, os, sys
from multiprocessing import Pool
D="/home/rick/dev/ibero/sentencias-salud/api/harvest/data/metadata"
def f(p):
    o=set(); oj=set()
    for line in open(p,encoding='utf-8'):
        r=json.loads(line)
        if r.get('Materia')=='Administrativa':
            o.add(r.get('OrganoNombreOficial'))
            oj.add((r.get('OrganoNombreOficial'), r.get('CircuitoRegion')))
    return o, oj
if __name__=='__main__':
    O=set(); OJ=set()
    with Pool(18) as pool:
        for o,oj in pool.imap_unordered(f, sorted(glob.glob(D+'/*.jsonl'))):
            O|=o; OJ|=oj
    print('OrganoNombreOficial distintos (materia Administrativa, 2011-2026):', len(O))
    print('pares organo x circuito:', len(OJ))
