"""Regression tests of the pdf_text cleaner.

Tests 1-4 run on synthetic page geometry, the same dicts `read_lines`
builds from `pdftotext -bbox-layout`; tests 5-6 write small PDFs to
tmp_path, because the tail cut, the raw tail and the verification go
through poppler.
"""

import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from pdf_text import clean as cl  # noqa: E402
from pdf_text import tail as tl  # noqa: E402
from pdf_text import verify as vf  # noqa: E402
from pdf_writer import write_pdf  # noqa: E402

W, H = 612.0, 792.0
LINE_H, STEP = 12.0, 16.0
# Letters, not digits: digits are neutralized in row signatures, so body
# lines tagged with page numbers would look like a repeated header.
TAGS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'


def line(y0, text, x0=72.0, h=LINE_H):
    return {'x0': x0, 'y0': y0, 'x1': min(540.0, x0 + 5.0 * len(text)),
            'y1': y0 + h, 't': text}


def body_lines(tag, top, count=12):
    return [line(top + STEP * k, 'Cuerpo %s%s del considerando que sigue'
                 % (tag, TAGS[k].lower() * 2)) for k in range(count)]


def page(n, lines):
    return {'n': n, 'w': W, 'h': H, 'lines': lines}


def all_text(pages):
    return ' '.join(p['text'] for p in cl.paragraphs(pages,
                                                      cl.repeated(pages)))


def by_text(pages, text):
    return [l for p in pages for l in p['lines'] if l['t'] == text]


def test_band_keeps_first_body_line_near_top_edge():
    """1. No header at all, and the first body line of every page sits
    above 0.09 of the page height, where the fixed August band cut."""
    pages = []
    for n in range(1, 7):
        tag = TAGS[n]
        first = line(50.0, 'PRIMERO %s. Se tiene por presentada la demanda'
                     % tag)
        pages.append(page(n, [first] + body_lines(tag, 50.0 + STEP)))
    assert pages[0]['lines'][0]['y1'] < 0.09 * H
    cl.classify(pages)
    text = all_text(pages)
    for n in range(1, 7):
        first = 'PRIMERO %s. Se tiene por presentada la demanda' % TAGS[n]
        assert by_text(pages, first)[0]['c'] == 'cuerpo'
        assert first in text


def test_two_row_header_on_odd_pages_is_labeled_row_by_row():
    """2. The second header row of odd pages reaches lower than the first
    body line of even pages, so only the row walk can label it."""
    head1, head2 = 'JUICIO DE AMPARO INDIRECTO', 'EXPEDIENTE 1485/2009'
    pages = []
    for n in range(1, 9):
        tag = TAGS[n]
        if n % 2:
            ls = [line(40.0, head1), line(56.0, head2)]
            ls += body_lines(tag, 80.0)
        else:
            ls = body_lines(tag, 45.0)
        pages.append(page(n, ls))
    cl.classify(pages)
    heads = by_text(pages, head1) + by_text(pages, head2)
    assert len(heads) == 8
    assert all(l['c'] == 'encabezado' for l in heads)
    # The band alone would not reach the second row: it stops above the
    # even pages' first body line.
    assert pages[0]['top_lim'] < 56.0 + LINE_H
    assert 'EXPEDIENTE' not in all_text(pages)
    for n in range(2, 9, 2):
        assert by_text(pages, pages[n - 1]['lines'][0]['t'])[0]['c'] \
            == 'cuerpo'


def test_folio_with_changing_number_bounds_footer():
    """3. `Hoja 9` and `Hoja 10` share a shape once digits are neutralized;
    the folio is a fixed row and its highest y0 bounds the footer."""
    pages, folio_y0 = [], []
    for n in range(1, 13):
        y0 = 740.0 + (n % 3) * 0.5
        folio_y0.append(y0)
        ls = body_lines(TAGS[n], 72.0, count=20)
        ls.append(line(y0, 'Hoja %d' % (n + 8), x0=500.0))
        pages.append(page(n, ls))
    cl.classify(pages)
    folios = [l for p in pages for l in p['lines']
              if l['t'].startswith('Hoja ')]
    assert all(l['c'] == 'pie' for l in folios)
    last_body_y1 = max(l['y1'] for p in pages for l in p['lines']
                       if l['c'] == 'cuerpo')
    bot = pages[0]['bot_lim']
    assert last_body_y1 < bot <= min(folio_y0)
    assert 'Hoja' not in all_text(pages)


def test_lowercase_body_line_in_a_fixed_row_stays_body():
    """4. Last-resort net: a continuation line whose shape repeats at the
    top of several pages looks fixed, but a body-height line starting in
    lowercase is never header."""
    pages, cont = [], []
    for n in range(1, 7):
        text = 'de %d, en el expediente %d/%d' % (2010 + n, n, 2010 + n)
        cont.append(text)
        ls = [line(40.0, 'JUZGADO QUINTO DE DISTRITO'), line(60.0, text)]
        ls += body_lines(TAGS[n], 60.0 + STEP)
        pages.append(page(n, ls))
    cl.classify(pages)
    assert all(l['c'] == 'encabezado'
               for l in by_text(pages, 'JUZGADO QUINTO DE DISTRITO'))
    text = all_text(pages)
    for t in cont:
        assert by_text(pages, t)[0]['c'] == 'cuerpo'
        assert t in text


# --- 5. tail cut, on real PDFs ----------------------------------------------

BODY = ['Vistos para resolver los autos del juicio de amparo indirecto.',
        'RESULTANDO. Por escrito presentado ante la oficialía de partes,',
        'la parte quejosa solicitó el amparo y la protección de la justicia',
        'federal contra actos de las autoridades que señaló como',
        'responsables, por la omisión de brindar atención médica oportuna.']
ACUSE = ['EVIDENCIA CRIPTOGRÁFICA - TRANSACCIÓN',
         'Archivo Firmado: sentencia.doc',
         'Autoridad Certificadora: Consejo de la Judicatura Federal',
         'FIRMANTE', 'No Serie: 70.6a.66.20.63.6a.66',
         'Cadena de Firma: 3f 9a 1c 55']
CERT = ['El licenciado que suscribe hago constar y certifico que en esta',
        'versión pública no existe información clasificada como',
        'confidencial o reservada.']


def long_body_page_with_phrase():
    """A body page over the 800-character cap that quotes the phrase."""
    filler = ('considerando que la autoridad responsable debió garantizar '
              'el acceso a los servicios de salud')
    return ([filler] * 8
            + ['y el secretario manifestó: hago constar y certifico que la',
               'versión pública del expediente se integró sin testar datos.']
            + [filler] * 4)


def tail_of(tmp_path, name, pages):
    pdf = write_pdf(str(tmp_path / (name + '.pdf')), pages)
    idx = cl.clean(pdf, name)
    return idx, vf.tail_text(idx, pdf)


needs_poppler = pytest.mark.skipif(shutil.which('pdftotext') is None,
                                   reason='pdftotext (poppler) missing')


@needs_poppler
def test_tail_cuts_at_whatever_comes_first(tmp_path):
    """5. Acuse then certification cuts at the acuse; certification alone
    cuts there; a long body page quoting the phrase does not cut."""
    body = [BODY, BODY, BODY]

    idx, tail = tail_of(tmp_path, 'acuse', body + [ACUSE, CERT])
    assert (idx['tail_first_page'], idx['tail_kind']) == (4, 'acuse')
    assert idx['pages_useful'] == 3
    assert 'EVIDENCIA CRIPTOGR' in tail and 'hago constar' in tail

    idx, tail = tail_of(tmp_path, 'cert', body + [CERT])
    assert (idx['tail_first_page'], idx['tail_kind']) == (4, 'certificacion')
    assert idx['pages_useful'] == 3
    assert 'hago constar' in tail

    long_page = long_body_page_with_phrase()
    assert len(' '.join(long_page)) > tl.CERT_MAX_CHARS
    idx, tail = tail_of(tmp_path, 'body', [BODY, long_page, BODY])
    assert (idx['tail_first_page'], idx['tail_kind']) == (None, 'none')
    assert idx['pages_useful'] == 3
    assert tail == ''
    assert any('hago constar y certifico' in p['text']
               for p in idx['index'])


@needs_poppler
def test_zero_clean_chars_is_never_ok(tmp_path):
    """6. Every line is a repeated header or folio, so the cleaner keeps 0
    characters and expects to keep 0: the residual is 0, which alone would
    pass the threshold."""
    pages = [['JUZGADO QUINTO DE DISTRITO', 'Hoja %d' % n]
             for n in range(1, 8)]
    pdf = write_pdf(str(tmp_path / 'zero.pdf'), pages)
    idx = cl.clean(pdf, 'zero')
    idx['text_ratio'] = vf.text_ratio(idx, pdf)
    idx['ratio_residual'] = vf.residual(idx)
    assert idx['chars'] == 0 and idx['ratio_residual'] is not None
    assert vf.grade(idx) != 'ok'
