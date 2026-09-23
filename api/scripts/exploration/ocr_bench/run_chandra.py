"""Chandra OCR 2 via vLLM (venv ~/.venvs/ocr-chandra, Python 3.12).

Prompt `ocr_layout` (chandra's default). The page text is chandra's
markdown with headers/footers included, so it transcribes the same page
area as the other engines; the raw layout HTML is kept beside it. One
batch per document; per-page seconds are amortized like surya's.
"""
import argparse
import time

from common import Bench, list_pages
from vlm_server import Server

PORT = 8012
ap = argparse.ArgumentParser()
ap.add_argument("--no-resume", action="store_true")
ap.add_argument("--limit", type=int)
ap.add_argument("--seqs", default="16")
ap.add_argument("--batched-tokens", default="2048")
ap.add_argument("--name", default="chandra",
                help="output dir under out/, for tuning runs")
ap.add_argument("--doc", help="only this docid")
ap.add_argument("--rebuild", action="store_true",
                help="re-derive the .txt from the saved .raw, no GPU")
args = ap.parse_args()

# Image blocks carry an English alt-text description of seals and
# stamps; that is not page text, so they are left out of the markdown.
MD_OPTS = dict(include_headers_footers=True, include_images=False)

if args.rebuild:
    from chandra.output import parse_markdown
    from common import OUT
    for raw in sorted((OUT / args.name).glob("*/*.raw")):
        md = parse_markdown(raw.read_text(encoding="utf-8"), **MD_OPTS)
        raw.with_suffix(".txt").write_text(md.strip() + "\n",
                                           encoding="utf-8")
    raise SystemExit

bench = Bench(args.name, resume=not args.no_resume)
model = "datalab-to/chandra-ocr-2"
# chandra's launcher scales an H100 baseline by VRAM: 24 GB gives
# 2048 batched tokens and 16 sequences; MTP stays off as it defaults.
extra = ["--max-num-seqs", args.seqs,
         "--max-num-batched-tokens", args.batched_tokens]
server = Server(bench, model, "chandra", PORT, extra)

from PIL import Image  # noqa: E402
from chandra.model import InferenceManager  # noqa: E402
from chandra.model.schema import BatchInputItem  # noqa: E402

mgr = InferenceManager(method="vllm")
api = f"http://127.0.0.1:{PORT}/v1"

only = {args.doc} if args.doc else None
todo = [i for i in list_pages(only) if not bench.done(i[0], i[1])]
if args.limit:
    todo = todo[:args.limit]
docs = {}
for item in todo:
    docs.setdefault(item[0], []).append(item)
try:
    for docid, items in docs.items():
        batch = [BatchInputItem(image=Image.open(p).convert("RGB"),
                                prompt_type="ocr_layout")
                 for _, _, p in items]
        t = time.time()
        outs = mgr.generate(batch, vllm_api_base=api, **MD_OPTS)
        per = (time.time() - t) / len(items)
        for (_, page, _), out in zip(items, outs):
            bench.write(docid, page, out.markdown.strip() + "\n", per,
                        raw=out.raw, amortized=True, tokens=out.token_count,
                        error=out.error)
finally:
    server.stop()
    bench.finish(pages=len(todo), model_load_s=round(server.load, 2),
                 model=model, seqs=args.seqs,
                 batched_tokens=args.batched_tokens)
