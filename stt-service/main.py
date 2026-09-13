"""Локальное распознавание речи: faster-whisper (int8, CPU), модель по умолчанию — small.

Полностью офлайн после первой загрузки весов. Отдельный контейнер, чтобы тяжёлая
ML-зависимость не влияла на API и масштабировалась независимо.
"""

import logging
import os
import tempfile

from fastapi import FastAPI, UploadFile
from faster_whisper import WhisperModel

logging.basicConfig(level="INFO")
log = logging.getLogger("stt")

MODEL_NAME = os.environ.get("STT_MODEL", "small")
app = FastAPI(title="Valerchik STT")
model: WhisperModel | None = None


@app.on_event("startup")
def load_model() -> None:
    global model
    log.info("Загружаю faster-whisper '%s' (int8, cpu)…", MODEL_NAME)
    model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
    log.info("Модель готова")


@app.get("/health")
def health() -> dict:
    return {"status": "ok" if model else "loading", "model": MODEL_NAME}


@app.post("/transcribe")
async def transcribe(file: UploadFile) -> dict:
    assert model is not None, "модель ещё загружается"
    audio = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        tmp.write(audio)
        path = tmp.name
    try:
        segments, info = model.transcribe(path, language="ru", vad_filter=True, beam_size=3)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return {"text": text, "duration": round(info.duration, 2), "language": info.language}
    finally:
        os.unlink(path)
