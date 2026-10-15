"""Compare the OCR engines' page texts. System Python; stdlib plus
rapidfuzz when present (difflib otherwise) and the `hunspell` CLI.

Writes out/metrics.json, out/pages.csv and the gold set, and prints a
markdown table. No ground truth exists: every figure here is a proxy
(dictionary hits, agreement between engines), not accuracy.
"""
import argparse
import csv
import html
import json
import re
import shutil
import statistics as st
import subprocess
from pathlib import Path

from common import BENCH, OUT, PAGES, list_pages

try:
    from rapidfuzz.distance import Levenshtein

    def similarity(a, b):
        return Levenshtein.normalized_similarity(a, b)
    SIM = "rapidfuzz normalized Levenshtein"
except ImportError:
    import difflib

    def similarity(a, b):
        return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
    SIM = "difflib ratio"

CLASSIC = ["tesseract", "paddle"]
ALPHA = "A-Za-zÁÉÍÓÚÜÑáéíóúüñ"
WORD = re.compile(f"[{ALPHA}]+")
ACCENT = re.compile("[ÁÉÍÓÚÜÑáéíóúüñ]")
PJF = re.compile(r"P\s?J\s?F")
VP = re.compile(r"Versi[oó]n\s*P[uú]blica", re.I)
TESTADO = re.compile(r"TESTAD[OA]", re.I)


def strip_markup(text):
    """Markdown/HTML from the VLMs down to plain text."""
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"(\*\*|__|`)", "", text)
    text = re.sub(r"^\s*\|?[-:| ]{3,}\|?\s*$", "", text, flags=re.M)
    return text.replace("|", " ")


def norm(text):
    return re.sub(r"\s+", " ", strip_markup(text)).strip()


def loops(text, min_rep=4, max_n=8):
    """Longest consecutive repetition of a word n-gram (n <= max_n) seen at
    least `min_rep` times: the decoder-loop signature of VLMs."""
    toks = re.findall(r"\w+", text.lower())
    worst = None
    for n in range(1, max_n + 1):
        i = 0
        while i + n <= len(toks):
            gram = toks[i:i + n]
            if n == 1 and len(gram[0]) < 2:
                i += 1
                continue
            rep = 1
            while toks[i + rep * n:i + (rep + 1) * n] == gram:
                rep += 1
            if rep >= min_rep and (worst is None or rep * n > worst[1] * n):
                worst = (" ".join(gram), rep)
            i += max(1, (rep - 1) * n) if rep > 1 else 1
    return worst


def noise_lines(text):
    """Lines of 1-2 non-digit characters: how a vertical watermark or
    speckle comes out of line-based engines."""
    return sum(1 for ln in strip_markup(text).splitlines()
               if 0 < len(ln.strip()) <= 2 and not ln.strip().isdigit())


def misspelled(words):
    out = subprocess.run(["hunspell", "-d", "es_MX", "-l"],
                         input="\n".join(words), capture_output=True,
                         text=True).stdout
    return set(out.split())


def timing(engine):
    rows = [json.loads(x) for x in
            (OUT / engine / "timing.jsonl").read_text().splitlines()]
    summ = [r for r in rows if r["event"] == "summary"][-1]
    pages = [r for r in rows if r["event"] == "page"]
    return summ, pages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="tesseract,paddle,paddle-v5latin,"
                    "surya,chandra")
    ap.add_argument("--gold", default="",
                    help="comma list docid/page; default picks four")
    args = ap.parse_args()
    engines = [e for e in args.engines.split(",") if (OUT / e).exists()]
    pages = [(d, p) for d, p, _ in list_pages()]
    texts = {e: {} for e in engines}
    for e in engines:
        for d, p in pages:
            f = OUT / e / d / f"{p}.txt"
            texts[e][(d, p)] = f.read_text(encoding="utf-8") if f.exists() \
                else None

    vocab = set()
    for e in engines:
        for t in texts[e].values():
            if t:
                vocab.update(WORD.findall(strip_markup(t)))
    bad = misspelled(sorted(w for w in vocab if len(w) >= 3))

    per_page = []
    metrics = {}
    for e in engines:
        summ, prow = timing(e)
        lhits = ltoks = 0
        hits = toks = acc = pjf = vp = testado = noise = 0
        loop_pages, trunc, present, errors = [], [], 0, 0
        errors = sum(1 for r in prow if r.get("error") or r.get("errors"))
        ratios, chars = [], 0
        for key in pages:
            t = texts[e][key]
            if t is None:
                continue
            present += 1
            n = norm(t)
            chars += len(n)
            words = WORD.findall(n)
            long = [w for w in words if len(w) >= 3]
            toks_p = len(long)
            hits_p = sum(1 for w in long if w not in bad)
            toks += toks_p
            hits += hits_p
            # Lowercase-only tokens leave out names and all-caps headings,
            # which the dictionary misses whatever the engine reads.
            low = [w for w in long if w.islower()]
            ltoks += len(low)
            lhits += sum(1 for w in low if w not in bad)
            acc += sum(1 for w in words if ACCENT.search(w))
            pjf += len(PJF.findall(n))
            vp += len(VP.findall(n))
            testado += len(TESTADO.findall(n))
            noise += noise_lines(t)
            lp = loops(n)
            if lp:
                loop_pages.append({"page": "/".join(key), "gram": lp[0][:60],
                                   "reps": lp[1]})
            tess = norm(texts["tesseract"][key] or "")
            if len(tess) >= 200:
                ratios.append(len(n) / len(tess))
                if len(n) < 0.2 * len(tess):
                    trunc.append("/".join(key))
            per_page.append({"engine": e, "doc": key[0], "page": key[1],
                             "chars": len(n), "tokens3": toks_p,
                             "dict_hit": round(hits_p / toks_p, 4)
                             if toks_p else "",
                             "loop": f"{lp[0]} x{lp[1]}" if lp else ""})
        words_all = sum(len(WORD.findall(norm(t))) for t in
                        texts[e].values() if t)
        load = summ.get("model_load_s") or 0
        run_s = summ["wall_s"] - load
        peak = summ.get("peak_vram_mib")
        base = summ.get("baseline_vram_mib") or 0
        agree = {}
        for other in engines:
            if other == e:
                continue
            sims = [similarity(norm(texts[e][k]), norm(texts[other][k]))
                    for k in pages if texts[e][k] is not None
                    and texts[other][k] is not None
                    and (texts[e][k].strip() or texts[other][k].strip())]
            agree[other] = round(st.mean(sims), 4) if sims else None
        metrics[e] = {
            "pages_present": present, "pages_total": len(pages),
            "page_errors": errors,
            "run_s": round(run_s, 1), "model_load_s": load,
            "pages_per_s": round(present / run_s, 3) if run_s else None,
            "peak_vram_mib": peak,
            "peak_vram_net_mib": peak - base if peak is not None else None,
            "chars": chars,
            "dict_hit_rate": round(hits / toks, 4) if toks else None,
            "dict_hit_lower": round(lhits / ltoks, 4) if ltoks else None,
            "misses_lower": ltoks - lhits,
            "accent_share": round(acc / words_all, 4) if words_all else None,
            "pjf": pjf, "version_publica": vp, "testado": testado,
            "noise_lines": noise,
            "len_ratio_vs_tesseract_total": round(
                chars / metrics["tesseract"]["chars"], 3)
            if "tesseract" in metrics else 1.0,
            "len_ratio_vs_tesseract_median": round(st.median(ratios), 3)
            if ratios else None,
            "loop_pages": loop_pages, "truncated_pages": trunc,
            "agreement": agree,
        }

    (OUT / "metrics.json").write_text(json.dumps(
        {"similarity": SIM, "engines": metrics}, ensure_ascii=False,
        indent=1), encoding="utf-8")
    with open(OUT / "pages.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(per_page[0]))
        w.writeheader()
        w.writerows(per_page)

    gold = pick_gold(args.gold, pages, texts, engines, per_page)
    gdir = BENCH / "gold"
    shutil.rmtree(gdir, ignore_errors=True)
    for reason, (d, p) in gold.items():
        target = gdir / f"{d}-{p}"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy(PAGES / d / f"{p}.png", target / "page.png")
        (target / "why.txt").write_text(reason + "\n", encoding="utf-8")
        for e in engines:
            (target / f"{e}.txt").write_text(texts[e][(d, p)] or "",
                                             encoding="utf-8")

    print(f"similarity: {SIM}\n")
    cols = ["engine", "pages/s", "load s", "peak VRAM MiB", "dict-hit",
            "dict-hit lower (misses)",
            "accent", "PJF", "Versión Pública", "TESTADO", "noise lines",
            "len vs tess (tot/med)", "agree tess", "agree paddle",
            "loop pages", "truncated", "pages 1:1"]
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for e, m in metrics.items():
        a = m["agreement"]
        row = [e, m["pages_per_s"], m["model_load_s"], m["peak_vram_net_mib"],
               m["dict_hit_rate"],
               f'{m["dict_hit_lower"]} ({m["misses_lower"]})',
               m["accent_share"], m["pjf"],
               m["version_publica"], m["testado"], m["noise_lines"],
               f'{m["len_ratio_vs_tesseract_total"]}/'
               f'{m["len_ratio_vs_tesseract_median"]}',
               a.get("tesseract", "-"), a.get("paddle", "-"),
               len(m["loop_pages"]), len(m["truncated_pages"]),
               f'{m["pages_present"] - m["page_errors"]}/{m["pages_total"]}']
        print("| " + " | ".join(str(x) for x in row) + " |")
    print("\ngold:", json.dumps(gold, ensure_ascii=False))


def pick_gold(spec, pages, texts, engines, per_page):
    if spec:
        items = [tuple(x.split("/")) for x in spec.split(",")]
        return {f"manual {i}": k for i, k in enumerate(items)}
    tess = {(r["doc"], r["page"]): r for r in per_page
            if r["engine"] == "tesseract"}

    def leak(k):
        return sum(len(PJF.findall(texts[e][k] or "")) +
                   len(VP.findall(texts[e][k] or "")) for e in engines)

    def spread(k):
        ns = [norm(texts[e][k] or "") for e in engines]
        sims = [similarity(a, b) for i, a in enumerate(ns) for b in ns[i+1:]]
        return st.mean(sims) if sims else 0

    long = [k for k in pages if tess[k]["chars"] >= 1200]
    y2011 = [k for k in long if k[0] == "10581770"]
    g = {"2011 scan (10581770, middle page)": y2011[len(y2011) // 2]}
    g["watermark-heavy (most PJF/Versión Pública fragments across engines)"] \
        = max(long, key=leak)
    noisy = [k for k in pages if tess[k]["chars"] >= 300
             and tess[k]["dict_hit"] != ""]
    g["no [TESTADO] exists in these 8 PDFs; substitute: noisiest page "
      "(lowest tesseract dict-hit)"] = min(
        noisy, key=lambda k: tess[k]["dict_hit"])
    g["clean (highest mean agreement across engines)"] = max(long, key=spread)
    return g


if __name__ == "__main__":
    main()
