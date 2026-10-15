"""Regression tests of the pdf_text cleaner (clean2).

They write small PDFs to tmp_path, because cleaning and verification go
through poppler and pdfplumber.
"""

import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from pdf_text import clean2 as c2  # noqa: E402
from pdf_text import verify as vf  # noqa: E402
from pdf_writer import write_pdf  # noqa: E402

BODY = ['Vistos para resolver los autos del juicio de amparo indirecto.',
        'RESULTANDO. Por escrito presentado ante la oficialía de partes,',
        'la parte quejosa solicitó el amparo y la protección de la justicia',
        'federal contra actos de las autoridades que señaló como',
        'responsables, por la omisión de brindar atención médica oportuna.']
needs_poppler = pytest.mark.skipif(shutil.which('pdftotext') is None,
                                   reason='pdftotext (poppler) missing')


@needs_poppler
def test_zero_clean_chars_is_never_ok(tmp_path):
    """Every line is a repeated header or folio, so the cleaner keeps 0
    characters and expects to keep 0: the residual is 0, which alone would
    pass the threshold."""
    pages = [['JUZGADO QUINTO DE DISTRITO', 'Hoja %d' % n]
             for n in range(1, 8)]
    pdf = write_pdf(str(tmp_path / 'zero.pdf'), pages)
    idx = c2.clean(pdf, 'zero')
    idx['text_ratio'] = vf.text_ratio(idx, pdf)
    idx['ratio_residual'] = vf.residual(idx)
    assert idx['chars'] == 0 and idx['ratio_residual'] is not None
    assert vf.grade(idx) != 'ok'
