"""PaddleOCR 3.7 on GPU (venv ~/.venvs/ocr-paddle, Python 3.13).

Default is PP-OCRv6 medium det+rec. PP-OCRv6 has no Spanish/Latin
recognizer, so `--variant v5-latin` runs PP-OCRv5 with lang="es" (the
latin rec) for comparison. Lines come back in detection order (top to
bottom); they are joined one per line, like tesseract's output.
"""
import argparse
import time

from common import Bench, list_pages

ap = argparse.ArgumentParser()
ap.add_argument("--variant", choices=["v6-medium", "v5-latin"],
                default="v6-medium")
ap.add_argument("--no-resume", action="store_true")
ap.add_argument("--limit", type=int)
args = ap.parse_args()

engine = "paddle" if args.variant == "v6-medium" else "paddle-v5latin"
bench = Bench(engine, resume=not args.no_resume)

t = time.time()
from paddleocr import PaddleOCR  # noqa: E402 (import is part of load time)

opts = dict(use_doc_orientation_classify=False, use_doc_unwarping=False,
            use_textline_orientation=False, device="gpu:0")
if args.variant == "v6-medium":
    ocr = PaddleOCR(ocr_version="PP-OCRv6", **opts)
else:
    ocr = PaddleOCR(ocr_version="PP-OCRv5", lang="es", **opts)
load = time.time() - t
bench.event("model_loaded", seconds=round(load, 2), variant=args.variant)

todo = [i for i in list_pages() if not bench.done(i[0], i[1])]
if args.limit:
    todo = todo[:args.limit]
for docid, page, png in todo:
    t = time.time()
    res = ocr.predict(str(png))[0]
    secs = time.time() - t
    texts = res["rec_texts"]
    scores = res["rec_scores"]
    mean = sum(scores) / len(scores) if scores else None
    bench.write(docid, page, "\n".join(texts) + "\n", secs, lines=len(texts),
                mean_score=round(mean, 4) if mean else None)
bench.finish(pages=len(todo), model_load_s=round(load, 2),
             variant=args.variant)
