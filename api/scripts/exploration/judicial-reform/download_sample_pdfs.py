"""Download the public PDF of every ruling in the October sample.

The public URL is deterministic: <idOrgano:4><NEUN:12><NumeroOrden:3>.pdf
(modern) or <idOrgano:4><NEUN:12><SintesisOrden:4><NumeroOrden:3>.docx (old,
served as PDF). `idOrgano` comes from resolve_public_urls.mjs, one per court;
NumeroOrden/SintesisOrden come from the harvested metadata row.

When the file is not there the server answers HTTP 200 with a ~582-byte HTML
notice pointing to the PNT, and that notice does not distinguish «no public
version» from «wrong file name»: both outcomes are logged as `no_pdf`.

Usage:
    python3 download_sample_pdfs.py [--sample CSV] [--outdir DIR]
"""

import argparse
import csv
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
EXTERNAL = os.path.join(REPO, 'api', 'harvest', 'data', 'external')
DEFAULT_SAMPLE = os.path.join(
    EXTERNAL, 'analysis-2026-09-08', 'october_pdf_sample_proposal.csv')
DEFAULT_OUTDIR = os.path.join(EXTERNAL, 'october-pdfs-2026-09-08')
METADATA = os.path.join(REPO, 'api', 'harvest', 'data', 'metadata')
UA = ('sentencias-salud (Centro Interdisciplinario para la '
      'Ensenanza Clinica del Derecho, Ibero CDMX; ricardo@yeeko.org)')
MONTHS = ['2025-10.jsonl', '2025-11.jsonl', '2025-12.jsonl']


def harvest_rows(neuns):
    """The metadata row of each sampled ruling, by NEUN + sentencia date."""
    found = {}
    for name in MONTHS:
        path = os.path.join(METADATA, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as fh:
            for line in fh:
                if '"AsuntoNeunId"' not in line:
                    continue
                row = json.loads(line)
                key = str(row['AsuntoNeunId'])
                if key not in neuns:
                    continue
                if row['FechaDeLaSentencia'] != neuns[key]['fecha_sentencia']:
                    continue
                found.setdefault(key, row)
    return found


def fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get('Content-Type', ''), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get('Content-Type', ''), e.read()[:2000]
    except Exception as e:
        return -1, repr(e), b''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', default=DEFAULT_SAMPLE)
    ap.add_argument('--outdir', default=DEFAULT_OUTDIR)
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    sample = {r['asunto_neun_id']: r
              for r in csv.DictReader(open(args.sample, encoding='utf-8'))}
    meta = harvest_rows(sample)
    urls = json.load(open(os.path.join(args.outdir, 'public_urls.json'),
                          encoding='utf-8'))
    by_court = {}
    for neun, info in urls.items():
        if info.get('idOrgano') and neun in sample:
            by_court[sample[neun]['organo']] = info['idOrgano']

    log = []
    for neun, row in sample.items():
        rec = {'grupo': row['grupo'], 'organo': row['organo'],
               'asunto_neun_id': neun, 'numero_alias': row['numero_alias'],
               'fecha_sentencia': row['fecha_sentencia'],
               'titular_firmante': row['titular_firmante']}
        m = meta.get(neun)
        org = by_court.get(row['organo'])
        if not m or not org:
            rec.update(estado='sin_datos', archivo='', bytes=0,
                       nota='falta fila de metadatos o idOrgano')
            log.append(rec)
            continue
        # El nombre construido con NumeroOrden falla en la mitad de los casos:
        # el sufijo del archivo no siempre es ese campo. La URL que el propio
        # buscador resolvió (resolve_public_urls.mjs) va primero.
        names = []
        resolved = urls.get(neun, {}).get('url') or ''
        if 'arch=' in resolved:
            names.append(urllib.parse.unquote(
                resolved.split('arch=')[1].split('&')[0]))
        names += ['%s%012d%03d.pdf' % (org, int(neun), int(m['NumeroOrden'])),
                  '%s%012d%04d%03d.docx' % (org, int(neun),
                                            int(m['SintesisOrden']),
                                            int(m['NumeroOrden']))]
        dest = os.path.join(args.outdir, '%s_%s.pdf' % (row['grupo'], neun))
        if os.path.exists(dest) and os.path.getsize(dest) > 10000:
            rec.update(estado='ya_estaba', archivo=os.path.basename(dest),
                       bytes=os.path.getsize(dest), nota='')
            log.append(rec)
            continue
        got = None
        for arch in names:
            url = ('https://sise.cjf.gob.mx/SVP/word1.aspx?arch=%s&sec=x&svp=1'
                   % urllib.parse.quote(arch))
            code, ctype, body = fetch(url)
            time.sleep(1.5)
            if code == 200 and 'pdf' in ctype.lower() and len(body) > 10000:
                got = (arch, body)
                break
        if got:
            with open(dest, 'wb') as fh:
                fh.write(got[1])
            rec.update(estado='ok', archivo=os.path.basename(dest),
                       bytes=len(got[1]), nota=got[0])
        else:
            rec.update(estado='no_pdf', archivo='', bytes=0,
                       nota='aviso PNT en ambos patrones (%s)' % names[0])
        print(neun, rec['estado'], rec['bytes'], flush=True)
        log.append(rec)

    out = os.path.join(args.outdir, 'download_log.csv')
    with open(out, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(log[0].keys()))
        w.writeheader()
        w.writerows(log)
    print('escrito', out)


if __name__ == '__main__':
    main()
