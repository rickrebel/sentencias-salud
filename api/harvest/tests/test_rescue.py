"""Pasada de rescate: hoja opaca, memoria de culpables y escritura.

Mismo trato que `test_poisoned.py`: sesión falsa, ningún socket y nada
escrito fuera del `tmp_path` de pytest.
"""

import argparse
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))))

import harvest_metadata as hm  # noqa: E402
import rescue_poisoned as rp  # noqa: E402
from test_poisoned import (FakeResponse, FakeSession, html_500,  # noqa: E402
                           json_page, no_sleep, row)

assert no_sleep  # fixture autouse importada, no la borre el linter

ORGAN = rp.ORGAN_KEY
STATE = rp.STATE_KEY


class CatalogResponse(FakeResponse):
    """`fetch_catalog` valida con `raise_for_status`, que FakeResponse
    no necesita para las búsquedas."""

    def raise_for_status(self):
        return None


def catalog_page(**lists):
    payload = {'data': {field: [{'value': v} for v in values]
                        for field, values in lists.items()}}
    return CatalogResponse(200, 'application/json', payload)


def is_search(body):
    return 'Paginate' in body


def a_slice(start='05-12-2013', **filters):
    return {'start': start, 'end': start, 'status': 'poisoned',
            'hits': None, 'filters': filters}


# --- (1) hoja opaca ---------------------------------------------------

def test_opaque_leaf_asks_no_pages():
    """Si la sonda `size=1` devuelve 500 + text/html no hay nada que
    paginar: la hoja se marca opaca sin gastar una sola página."""
    session = FakeSession(lambda body: html_500())
    slc = a_slice(**{ORGAN: ['Juzgado Primero de Distrito']})
    job = rp.Pass(session, slc, {}, {}, {})

    assert job.run(['Jalisco', 'Morelos']) == []

    assert job.rows == []
    assert job.opaque == [{'hits': None}]
    assert job.bad_pages == 0
    # una sonda y ninguna página: 'hoja' no llega a existir
    assert job.counts == {'hoja_probe': 1}
    assert len(session.calls) == 1
    assert session.calls[0]['Paginate']['size'] == 1


# --- (2) la memoria es atajo, no suposición ---------------------------

def test_shortcut_falls_back_to_full_cornering_on_500():
    """La memoria ahorra peticiones cuando acierta; cuando el bloque
    «todos menos los conocidos» devuelve 500, se acorrala entero."""
    states = ['S1', 'S2', 'S3', 'S4', 'S5', 'S6']

    def answer(body):
        if not is_search(body):
            return catalog_page(estado=['S6'], organo=['O-malo'])
        asked = body['FiltrosPrimarios'].get(STATE) or []
        organs = body['FiltrosPrimarios'].get(ORGAN) or []
        if 'O-malo' in organs or (not organs and 'S6' in asked):
            return html_500()
        return json_page([], 0)

    session = FakeSession(answer)
    # S1 viene de una rebanada anterior: es el atajo que se va a probar
    memory = {'S1': ['O-viejo']}
    job = rp.Pass(session, a_slice(), {}, memory, {})

    assert job.run(states) == ['S6']

    assert job.counts['atajo'] == 1
    # tercios de 6 (3 bloques) + tercios del bloque malo (2) = 5, no 1:
    # el atajo falló y el acorralamiento completo corrió igual
    assert job.counts['estado'] == 5
    assert memory['S6'] == ['O-malo']


def test_network_failure_culprit_is_not_memorised():
    """Un órgano que agota los reintentos por red no es un culpable
    estable: memorizarlo envenenaría el atajo de todas las siguientes."""
    def answer(body):
        if not is_search(body):
            return catalog_page(estado=['Jalisco'],
                                organo=['O-envenenado', 'O-caido'])
        organs = body['FiltrosPrimarios'].get(ORGAN) or []
        if 'O-caido' in organs:
            raise requests.Timeout('gateway')
        if 'O-envenenado' in organs:
            return html_500()
        return json_page([], 0)

    session = FakeSession(answer)
    memory = {}
    job = rp.Pass(session, a_slice(), {}, memory, {})

    assert sorted(job.hunt_state('Jalisco')) == ['O-caido', 'O-envenenado']

    assert memory == {'Jalisco': ['O-envenenado']}
    # los dos siguen tratándose como hojas: solo cambia qué se recuerda
    assert len(job.opaque) == 2


# --- (3) escritura y pérdida desconocida ------------------------------

def test_append_rows_skips_keys_already_on_disk(tmp_path):
    path = str(tmp_path / '2014-01.jsonl')
    with open(path, 'w', encoding='utf-8') as handle:
        for n in (1, 2):
            handle.write(json.dumps(row(n)) + '\n')

    keys = hm.existing_keys(path)
    written, size = rp.append_rows(path, [row(1), row(2), row(3)], keys,
                                   False)

    assert written == 1
    assert size == len(json.dumps(row(3), ensure_ascii=False) + '\n')
    lines = open(path, encoding='utf-8').readlines()
    assert [json.loads(x)['AsuntoNeunId'] for x in lines] == [1, 2, 3]

    # el set queda actualizado: repetir la llamada no reescribe nada
    again, _ = rp.append_rows(path, [row(1), row(3)], keys, False)
    assert again == 0
    assert len(open(path, encoding='utf-8').readlines()) == 3


def test_unknown_hits_keep_rows_missing_as_none(tmp_path, monkeypatch):
    """Cuando la fuente no declara hits la pérdida es desconocida, y
    eso no es cero: `rows_missing` se queda en None y el mes no suma."""
    session = FakeSession(lambda body: (
        catalog_page(estado=['Jalisco']) if not is_search(body)
        else html_500()))
    monkeypatch.setattr(hm, 'make_session', lambda token: session)

    token = tmp_path / 'token.txt'
    token.write_text('no-es-un-jwt', encoding='utf-8')
    label = hm.slice_label(a_slice(**{ORGAN: ['Juzgado Primero']}))
    state = {'months': {'2013-12': {
        'status': 'done', 'rows': 10, 'bytes': 0, 'requests': 0,
        'total_hits': 10, 'failed': [label],
        'slices': [a_slice(**{ORGAN: ['Juzgado Primero']})]}}}
    hm.save_state(str(tmp_path), state)

    args = argparse.Namespace(out=str(tmp_path), token=str(token),
                              months=None, days=None, limit=None,
                              dry_run=False, status=False)
    assert rp.rescue(args) == 0

    entry = hm.load_state(str(tmp_path))['months']['2013-12']
    slc = entry['slices'][0]
    assert slc['status'] == 'rescued_partial'
    assert slc['rescue']['lost'] is None
    assert slc['rows_missing'] is None
    # sin hits declarados no hay nada que sumar al control del mes
    assert entry['total_hits'] == 10
    # y la etiqueta deja de anunciarse como sin cosechar
    assert 'failed' not in entry
    assert hm.missing_of(entry) == 0
