"""`base_name` folds the incarnations of a court into one map key."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))))

from build_organ_map import base_name  # noqa: E402

COURT = 'Primer Tribunal Unitario del Tercer Circuito'


@pytest.mark.parametrize('suffix', [
    ' (28/10/2003- 30/09/2024)',
    ' (2/4/2001) - (30/11/2022)',
    ' (1/6/2004 - 15/11/2022)',
    '(02/04/2001 -01/09/2024)',
])
def test_base_name_strips_vigencia(suffix):
    assert base_name(COURT + suffix) == COURT


def test_base_name_keeps_other_parentheses():
    name = COURT + ' (con residencia en Guadalajara)'
    assert base_name(name) == name
