"""Surya OCR 2 full-page mode (venv ~/.venvs/ocr-surya, Python 3.12).

One batch per document; per-page seconds are the batch wall time divided
by its pages (vLLM runs the pages concurrently). Block HTML is flattened
to text, one block per paragraph, in the model's reading order; blocks
Surya itself skips (pictures) are dropped.
"""
import argparse
import json
import os
import time

PORT = 8011
os.environ.setdefault("SURYA_INFERENCE_BACKEND", "vllm")
os.environ.setdefault("SURYA_INFERENCE_URL", f"http://127.0.0.1:{PORT}/v1")
os.environ.setdefault("SURYA_INFERENCE_PARALLEL", "32")

from common import Bench, list_pages  # noqa: E402
from vlm_server import Server  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--no-resume", action="store_true")
ap.add_argument("--limit", type=int)
ap.add_argument("--no-mtp", action="store_true")
args = ap.parse_args()

bench = Bench("surya", resume=not args.no_resume)
model = "datalab-to/surya-ocr-2"
# 3090 values from surya's own launcher (_gpu_settings for 24 GB).
extra = ["--max-num-seqs", "32", "--max-num-batched-tokens", "8192"]
if not args.no_mtp:
    extra += ["--speculative-config",
              json.dumps({"method": "mtp", "num_speculative_tokens": 2})]
server = Server(bench, model, model, PORT, extra)

from bs4 import BeautifulSoup  # noqa: E402
from PIL import Image  # noqa: E402
from surya.inference import SuryaInferenceManager  # noqa: E402
from surya.recognition import RecognitionPredictor  # noqa: E402

rec = RecognitionPredictor(SuryaInferenceManager())

todo = [i for i in list_pages() if not bench.done(i[0], i[1])]
if args.limit:
    todo = todo[:args.limit]
docs = {}
for item in todo:
    docs.setdefault(item[0], []).append(item)
try:
    for docid, items in docs.items():
        imgs = [Image.open(p).convert("RGB") for _, _, p in items]
        t = time.time()
        preds = rec(imgs)
        per = (time.time() - t) / len(items)
        for (_, page, _), pred in zip(items, preds):
            blocks = [b for b in pred.blocks if not b.skipped]
            paras = [BeautifulSoup(b.html, "html.parser").get_text("\n")
                     .strip() for b in blocks]
            raw = json.dumps([{"label": b.label, "html": b.html,
                               "bbox": b.bbox, "error": b.error}
                              for b in pred.blocks], ensure_ascii=False)
            bench.write(docid, page, "\n\n".join(p for p in paras if p) + "\n",
                        per, raw=raw, amortized=True, blocks=len(blocks),
                        errors=sum(b.error for b in pred.blocks),
                        labels=sorted({b.label for b in blocks}))
finally:
    server.stop()
    bench.finish(pages=len(todo), model_load_s=round(server.load, 2),
                 model=model, mtp=not args.no_mtp)
