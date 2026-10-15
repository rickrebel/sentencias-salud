"""Start/stop a local `vllm serve` for the VLM engines and time its load.

Surya and Chandra ship docker launchers; there is no docker here, so the
same arguments go to the pip-installed vLLM (venv ~/.venvs/ocr-chandra).
"""
import os
import signal
import time

from common import start_vllm, wait_http


class Server:
    def __init__(self, bench, model, served_name, port, extra=()):
        self.bench = bench
        t = time.time()
        log = bench.dir / "vllm-server.log"
        self.proc, cmd = start_vllm(model, served_name, port, log, extra)
        bench.event("server_cmd", cmd=cmd)
        wait_http(f"http://127.0.0.1:{port}/v1/models", proc=self.proc)
        self.load = time.time() - t
        bench.event("model_loaded", seconds=round(self.load, 2))

    def stop(self):
        os.killpg(self.proc.pid, signal.SIGTERM)
        try:
            self.proc.wait(60)
        except Exception:
            os.killpg(self.proc.pid, signal.SIGKILL)
