"""Shared plumbing for the OCR bench runners (stdlib only: every engine
runs in its own venv with a different Python)."""
import json
import os
import subprocess
import time
from pathlib import Path

API = Path(__file__).resolve().parents[3]
BENCH = API / "media" / "pdf-ocr-bench"
PAGES = BENCH / "pages"
OUT = BENCH / "out"


def list_pages(only=None):
    """(docid, page, path) sorted; `only` is an optional set of docids."""
    for doc in sorted(p for p in PAGES.iterdir() if p.is_dir()):
        if only and doc.name not in only:
            continue
        for png in sorted(doc.glob("*.png")):
            yield doc.name, png.stem, png


class Bench:
    """Per-engine output dir, per-page timing and a background VRAM sampler.

    VRAM is the whole GPU as nvidia-smi reports it, so the idle baseline
    (desktop, other processes) is recorded too and subtracted in measure.py.
    """

    def __init__(self, engine, resume=True):
        self.engine = engine
        self.dir = OUT / engine
        self.dir.mkdir(parents=True, exist_ok=True)
        self.resume = resume
        self.timing = open(self.dir / "timing.jsonl", "a", encoding="utf-8")
        self.vram_log = self.dir / "vram.log"
        self.baseline = self._vram_now()
        self.sampler = subprocess.Popen(
            ["nvidia-smi", "--query-gpu=memory.used",
             "--format=csv,noheader,nounits", "-lms", "500"],
            stdout=open(self.vram_log, "w"), stderr=subprocess.DEVNULL)
        self.t0 = time.time()
        self.event("start", baseline_vram_mib=self.baseline)

    @staticmethod
    def _vram_now():
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True).stdout
        return int(out.split()[0]) if out.strip() else None

    def event(self, kind, **data):
        row = {"event": kind, "ts": round(time.time(), 3), **data}
        self.timing.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.timing.flush()

    def path(self, docid, page, suffix=".txt"):
        return self.dir / docid / f"{page}{suffix}"

    def done(self, docid, page):
        return self.resume and self.path(docid, page).exists()

    def write(self, docid, page, text, seconds, raw=None, **extra):
        p = self.path(docid, page)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if raw is not None:
            self.path(docid, page, ".raw").write_text(raw, encoding="utf-8")
        self.event("page", doc=docid, page=page, seconds=round(seconds, 3),
                   chars=len(text), **extra)

    def finish(self, **data):
        self.sampler.terminate()
        self.sampler.wait()
        vals = [int(x) for x in self.vram_log.read_text().split()
                if x.strip().isdigit()]
        self.event("summary", wall_s=round(time.time() - self.t0, 2),
                   peak_vram_mib=max(vals) if vals else None,
                   baseline_vram_mib=self.baseline, **data)
        self.timing.close()


def wait_http(url, timeout=1800, proc=None):
    """Poll an HTTP endpoint until 200; returns seconds waited."""
    import urllib.request
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"server exited with {proc.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return time.time() - t0
        except Exception:
            pass
        time.sleep(2)
    raise TimeoutError(url)


def start_vllm(model, served_name, port, log_path, extra=()):
    """Launch `vllm serve` from the vLLM venv with the args the engine's own
    docker launcher uses (tuned there for a 24 GB card)."""
    vllm = os.environ.get("OCR_VLLM_BIN",
                          str(Path.home() / ".venvs/ocr-chandra/bin/vllm"))
    cmd = [vllm, "serve", model, "--port", str(port),
           "--host", "127.0.0.1", "--served-model-name", served_name,
           "--dtype", "bfloat16", "--max-model-len", "18000",
           "--gpu-memory-utilization", "0.85", "--enable-prefix-caching",
           "--mm-processor-kwargs",
           json.dumps({"min_pixels": 3136, "max_pixels": 6291456}),
           *extra]
    log = open(log_path, "w")
    # flashinfer's sampler JIT-compiles with nvcc, and there is no CUDA
    # toolkit on this machine; vLLM's own sampler needs none.
    env = {**os.environ, "VLLM_USE_FLASHINFER_SAMPLER": "0"}
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                            env=env, start_new_session=True)
    return proc, cmd
