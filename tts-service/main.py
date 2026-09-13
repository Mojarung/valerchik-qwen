"""Локальный синтез речи: Piper (ONNX на CPU, русский голос).

Отдельный контейнер по тем же причинам, что и STT: ML-зависимость и веса модели
не должны попадать в образ API и масштабируются независимо. Полностью офлайн
после первой загрузки голоса.

Piper выбран вместо Silero осознанно: он работает на onnxruntime (десятки
мегабайт), тогда как Silero тянет torch — почти гигабайт ради того же результата
на том же процессоре.
"""

import io
import logging
import os
import subprocess
import sys
import wave
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from piper import PiperVoice
from pydantic import BaseModel, Field

logging.basicConfig(level="INFO")
log = logging.getLogger("tts")

VOICE_NAME = os.environ.get("TTS_VOICE", "ru_RU-dmitri-medium")
VOICE_DIR = Path(os.environ.get("TTS_VOICE_DIR", "/data/voices"))

app = FastAPI(title="Valerchik TTS")
voice: PiperVoice | None = None


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    # Скорость речи: 1.0 — как записан голос, больше — медленнее
    length_scale: float | None = Field(default=None, ge=0.5, le=2.0)


@app.on_event("startup")
def load_voice() -> None:
    """Голос качаем при первом старте в том, а не кладём в образ.

    Иначе каждая пересборка образа тащила бы веса заново, а сменить голос через
    переменную окружения было бы нельзя — только пересборкой.
    """
    global voice
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    model = VOICE_DIR / f"{VOICE_NAME}.onnx"
    if not model.exists():
        log.info("Скачиваю голос %s…", VOICE_NAME)
        subprocess.run(  # имя голоса задаёт администратор, не пользователь
            [sys.executable, "-m", "piper.download_voices", VOICE_NAME, "--data-dir", str(VOICE_DIR)],
            check=True,
        )
    log.info("Загружаю голос %s…", VOICE_NAME)
    voice = PiperVoice.load(str(model))
    log.info("Голос готов")


@app.get("/health")
def health() -> dict:
    return {"status": "ok" if voice else "loading", "voice": VOICE_NAME}


@app.post("/synthesize")
def synthesize(body: SpeakRequest) -> Response:
    """Текст → WAV.

    Обработчик намеренно синхронный (def, а не async def): синтез загружает
    процессор целиком, и в асинхронном обработчике он заблокировал бы весь цикл
    событий вместе с проверками здоровья. FastAPI уводит такие обработчики в
    пул потоков.
    """
    if voice is None:
        raise HTTPException(503, "Голос ещё загружается")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        if body.length_scale is None:
            voice.synthesize_wav(body.text, wav_file)
        else:
            from piper import SynthesisConfig

            voice.synthesize_wav(
                body.text, wav_file, syn_config=SynthesisConfig(length_scale=body.length_scale)
            )
    return Response(buffer.getvalue(), media_type="audio/wav")
