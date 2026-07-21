# Demo video tooling

This folder contains a deterministic recording harness for the product segment.
It replays `demo/canonical_run.json`, so the captured roles and drafts are from a
real run while the recording itself does not depend on live search latency.

## Product capture

Start the recording-only backend:

```bash
.venv/bin/uvicorn demo.video.demo_api:app --port 8000
```

Start the frontend:

```bash
cd frontend
npm run dev
```

Install the local capture dependency, then record:

```bash
cd demo/video
npm install
node capture_product.mjs
```

Capture the hosted app in its real dark theme without overwriting the fallback:

```bash
DEMO_APP_URL=https://agentic-job-hunt.vercel.app \
DEMO_OUTPUT_NAME=live-product \
DEMO_RESULT_TIMEOUT_MS=240000 \
DEMO_COLOR_SCHEME=dark \
node capture_product.mjs
```

Fetch and capture one real Phoenix trace:

```bash
.venv/bin/python demo/video/fetch_trace.py
cd demo/video
node capture_trace.mjs
```

Install and download the local neural TTS model:

```bash
pip install -r demo/video/requirements-tts.txt
mkdir -p demo/video/models
curl -L https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.fp16.onnx \
  -o demo/video/models/kokoro-v1.0.fp16.onnx
curl -L https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin \
  -o demo/video/models/voices-v1.0.bin
```

Generate premium local American-English narration, title cards, and the final cut:

```bash
.venv/bin/python demo/video/generate_narration.py
cd demo/video && node capture_cards.mjs
cd ../..
.venv/bin/python demo/video/render_final.py
```

Final deliverables are written outside the repository to:

```text
~/.codex/artifacts/job-hunt-signal/
```

Set `KOKORO_TTS_VOICE=bm_george` for a British male voice instead of the
default American male `am_michael`.

Turn the PNG sequence into a high-quality intermediate:

```bash
ffmpeg -framerate 6 -i demo/video/build/product/frame-%06d.png \
  -vf "scale=1920:1080,fps=30,format=yuv420p" -c:v libx264 -crf 14 -preset slow \
  demo/video/build/product.mp4
```
