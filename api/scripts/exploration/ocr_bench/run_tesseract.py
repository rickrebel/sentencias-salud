"""Baseline: tesseract spa, default page segmentation, one process per page.

Runs with the system Python. OMP_THREAD_LIMIT=1 and a pool of 15 workers
keeps it inside the 15-core budget.
"""
import argparse
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from common import Bench, list_pages

ap = argparse.ArgumentParser()
ap.add_argument("--workers", type=int, default=15)
ap.add_argument("--no-resume", action="store_true")
args = ap.parse_args()

bench = Bench("tesseract", resume=not args.no_resume)
env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
version = subprocess.run(["tesseract", "--version"], capture_output=True,
                         text=True).stdout.splitlines()[0]


def ocr(item):
    docid, page, png = item
    t = time.time()
    out = subprocess.run(["tesseract", str(png), "-", "-l", "spa"],
                         capture_output=True, text=True, env=env).stdout
    return docid, page, out, time.time() - t


todo = [i for i in list_pages() if not bench.done(i[0], i[1])]
with ThreadPoolExecutor(args.workers) as pool:
    for docid, page, text, secs in pool.map(ocr, todo):
        bench.write(docid, page, text, secs)
bench.finish(pages=len(todo), model_load_s=0, version=version,
             workers=args.workers, concurrency_note="per-page seconds are "
             "single-thread latency; wall_s is with the worker pool")
