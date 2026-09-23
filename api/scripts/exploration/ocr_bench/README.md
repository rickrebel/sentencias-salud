# Banco de OCR para los PDF escaneados

Compara motores de OCR de código abierto sobre los 8 PDF de la muestra que no traen capa de texto (sentencias escaneadas de 2011 a 2023, con la marca de agua «PJF - Versión Pública» impresa en la imagen). Todo lo que produce vive en `api/media/pdf-ocr-bench/`, que git ignora; aquí solo están los scripts.

No hay texto de referencia: las métricas de `measure.py` son indicios (aciertos contra el diccionario, acuerdo entre motores, marca de agua filtrada, bucles, truncados), no exactitud. Para juzgar calidad está el conjunto de oro: cuatro páginas con la salida de cada motor lado a lado y la imagen de la página.

## Estructura

- `common.py`: rutas, listado de páginas, escritura por página, `timing.jsonl` y el muestreo de VRAM con `nvidia-smi -lms 500` en segundo plano. Solo biblioteca estándar, porque cada motor corre con otro Python.
- `vlm_server.py`: arranca `vllm serve` con los mismos argumentos que usan los lanzadores Docker de Surya y Chandra (aquí no hay Docker) y mide el tiempo de carga.
- `run_tesseract.py`, `run_paddle.py`, `run_surya.py`, `run_chandra.py`: un corredor por motor. Todos reanudan: saltan las páginas que ya tienen `.txt`; `--no-resume` rehace todo (conviene borrar antes `out/<motor>/` para que `timing.jsonl` no acumule corridas).
- `measure.py`: métricas, `out/metrics.json`, `out/pages.csv`, la tabla en markdown por la salida estándar y el conjunto de oro en `gold/`.

Salida por motor: `out/<motor>/<docid>/<página>.txt` (una página por archivo, en orden de lectura), `timing.jsonl` (eventos `start`, `model_loaded`, `page`, `summary` con el pico de VRAM) y `vram.log`. Surya y Chandra dejan además `<página>.raw` (bloques con etiqueta y bbox, o el HTML de layout) y `vllm-server.log`.

## Entornos

Un venv por motor bajo `~/.venvs/`, fuera del repo. El servidor vLLM de Surya usa el vLLM instalado en el venv de Chandra (variable `OCR_VLLM_BIN` para cambiarlo).

```bash
uv=~/.local/bin/uv; cd ~/.venvs
# PaddleOCR 3.7 + Paddle GPU (CUDA 13.0), Python 3.13 del sistema
$uv venv -p /usr/bin/python3.13 ocr-paddle
$uv pip install -p ocr-paddle/bin/python --index-url https://www.paddlepaddle.org.cn/packages/stable/cu130/ --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match paddlepaddle-gpu==3.4.0 "paddleocr==3.7.*"
# Chandra OCR 2 + vLLM (servidor para Chandra y para Surya)
$uv venv -p 3.12 ocr-chandra
$uv pip install -p ocr-chandra/bin/python "chandra-ocr==0.2.0" vllm
# Surya OCR 2, solo cliente: con vllm en el mismo venv el resolvedor cae a vllm 0.1.3 y falla al compilar
$uv venv -p 3.12 ocr-surya
$uv pip install -p ocr-surya/bin/python "surya-ocr==0.22.*" "Pillow<11"
```

Tesseract 5.5.3 con `spa` es el del sistema. `measure.py` usa el Python del sistema, `rapidfuzz` si está (si no, `difflib`) y el binario `hunspell` con el diccionario `es_MX`.

El muestreador de flashinfer compila con `nvcc` y aquí no hay toolkit de CUDA; `common.start_vllm` pone `VLLM_USE_FLASHINFER_SAMPLER=0` y vLLM usa su propio muestreador.

## Cómo repetir

```bash
cd api/media && mkdir -p pdf-ocr-bench/pages
# rasterizar a 300 dpi: pdftoppm -r 300 -png <pdf> pdf-ocr-bench/pages/<docid>/p, y renombrar p-N.png a NNN.png
cd ../scripts/exploration/ocr_bench
python3 run_tesseract.py                          # 15 procesos, OMP_THREAD_LIMIT=1
~/.venvs/ocr-paddle/bin/python run_paddle.py      # PP-OCRv6 medium
~/.venvs/ocr-paddle/bin/python run_paddle.py --variant v5-latin
~/.venvs/ocr-surya/bin/python run_surya.py        # levanta y apaga vLLM en el puerto 8011
~/.venvs/ocr-chandra/bin/python run_chandra.py    # ídem en el 8012
python3 measure.py                                # o --gold doc/página,doc/página,…
```

Los motores de GPU van uno a la vez: cada servidor vLLM reserva el 85 % de la memoria. `--limit N` sirve para probar con las primeras N páginas.

## Decisiones del banco

- PP-OCRv6 no tiene reconocedor latino ni español (solo chino, inglés, coreano, japonés y algunos más), pero su diccionario sí trae acentos y ñ; se corre con los valores por omisión. La variante `paddle-v5latin` (PP-OCRv5 con `lang="es"`) está como comparación.
- Surya corre en modo página completa, una llamada al modelo por página. Chandra usa el prompt `ocr_layout` y su markdown con encabezados y pies incluidos, para transcribir la misma área que los demás.
- En Surya y Chandra los segundos por página son el tiempo del lote de un documento dividido entre sus páginas (`amortized: true`); las páginas por segundo de la tabla salen del tiempo total sin la carga del modelo.
- Chandra usa lo que su propio lanzador calcula para 24 GB (2048 tokens por lote, 16 secuencias, sin MTP); `--seqs` y `--batched-tokens` permiten probar otros valores.
- El pico de VRAM es el de toda la tarjeta menos la línea base al arrancar. En los motores con vLLM refleja la reserva de `--gpu-memory-utilization 0.85`, no lo que el modelo necesita.
- Una página cuenta como truncada si su texto es menos del 20 % del de Tesseract, y solo cuando Tesseract leyó al menos 200 caracteres. Un bucle es un n-grama de hasta 8 palabras repetido cuatro o más veces seguidas.
- Chandra devuelve los sellos y escudos como bloques de imagen con una descripción en inglés; el markdown de la página se arma sin ellos (`include_images=False`). `run_chandra.py --rebuild` rehace los `.txt` desde los `.raw` sin GPU; `--name` y `--doc` sirven para corridas de ajuste en otra carpeta de `out/`.
