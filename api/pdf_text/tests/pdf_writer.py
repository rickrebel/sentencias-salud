"""Smallest PDF that pdftotext reads: Helvetica, WinAnsi, one Tj per line.

The tail tests need real pages because the cut and the raw tail go through
poppler; writing them here keeps personal data and binaries out of the repo.
"""

PAGE_W, PAGE_H = 612, 792


def _escape(text: str) -> bytes:
    raw = text.encode('cp1252')
    return raw.replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(
        b')', b'\\)')


def _stream(lines: list[str], size: int) -> bytes:
    out = [b'BT', b'/F1 %d Tf' % size]
    y = PAGE_H - 72
    for text in lines:
        out.append(b'1 0 0 1 72 %d Tm (%s) Tj' % (y, _escape(text)))
        y -= size + 6
    out.append(b'ET')
    return b'\n'.join(out)


def write_pdf(path: str, pages: list[list[str]], size: int = 10) -> str:
    """`pages` is a list of pages, each a list of text lines."""
    n = len(pages)
    # 1 catalog, 2 pages, 3 font, then (page, content) pairs.
    kids = ' '.join('%d 0 R' % (4 + 2 * i) for i in range(n))
    objs = [b'<< /Type /Catalog /Pages 2 0 R >>',
            b'<< /Type /Pages /Kids [%s] /Count %d >>' % (kids.encode(), n),
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica '
            b'/Encoding /WinAnsiEncoding >>']
    for i, lines in enumerate(pages):
        body = _stream(lines, size)
        objs.append(b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] '
                    b'/Resources << /Font << /F1 3 0 R >> >> '
                    b'/Contents %d 0 R >>' % (PAGE_W, PAGE_H, 5 + 2 * i))
        objs.append(b'<< /Length %d >>\nstream\n%s\nendstream'
                    % (len(body), body))
    data = bytearray(b'%PDF-1.4\n')
    offsets = []
    for num, obj in enumerate(objs, 1):
        offsets.append(len(data))
        data += b'%d 0 obj\n%s\nendobj\n' % (num, obj)
    xref = len(data)
    data += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1)
    for off in offsets:
        data += b'%010d 00000 n \n' % off
    data += (b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n'
             % (len(objs) + 1, xref))
    with open(path, 'wb') as fh:
        fh.write(bytes(data))
    return path
