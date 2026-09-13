"""Голосовой ввод: аудио из браузера → локальный STT-микросервис (faster-whisper)."""

import asyncio

import httpx
from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import Response

from app.config import get_settings
from app.middleware import check_rate_limit
from app.schemas import SpeakRequest
from app.schemas.voice import Transcription
from app.services import speech
from app.uploads import read_limited

router = APIRouter(prefix="/api/voice", tags=["voice"])

# Реплика в чат — это секунды речи. Мегабайт с запасом покрывает даже webm
# посредственного качества, а десять мегабайт — это десять минут аудио, которые
# распознаются дольше, чем живёт запрос, и всё это время занимают STT целиком.
_MAX_AUDIO_BYTES = 1 * 1024 * 1024
# Один воркер faster-whisper на CPU: параллельные распознавания не ускоряют
# ответ, а выстраиваются в очередь и складываются в таймаут для всех.
_MAX_PARALLEL = 2
_slots = asyncio.Semaphore(_MAX_PARALLEL)
# Синтез считает на том же процессоре и стоит примерно столько же — отдельный
# счётчик, чтобы озвучка не выедала слоты у распознавания и наоборот.
_speak_slots = asyncio.Semaphore(_MAX_PARALLEL)


@router.post("/transcribe", response_model=Transcription)
async def transcribe(request: Request, file: UploadFile) -> Transcription:
    # Ручка публичная и дорогая: без лимита один клиент занимал STT бесконечно.
    await check_rate_limit(request, scope="voice")
    rt = request.app.state.rt
    if not rt.dyn.get("voice.enabled"):
        raise HTTPException(503, "Голосовой ввод отключён администратором")
    stt_url = get_settings().stt_url
    if not stt_url:
        raise HTTPException(503, "STT-сервис не настроен (запустите профиль voice в docker compose)")
    audio = await read_limited(
        file, _MAX_AUDIO_BYTES, "Запись слишком длинная — говорите короче (до 1 МБ аудио)"
    )
    if _slots.locked():
        raise HTTPException(429, "Распознавание занято, попробуйте через несколько секунд")
    async with _slots, httpx.AsyncClient(timeout=120.0) as client:
        return await _ask_stt(client, stt_url, file, audio)


@router.post(
    "/speak",
    response_class=Response,
    responses={200: {"content": {"audio/wav": {}}, "description": "Озвучка текста в WAV"}},
)
async def speak(request: Request, body: SpeakRequest) -> Response:
    """Текст ответа → озвучка.

    Лимиты те же и по тем же причинам, что у распознавания: ручка публичная,
    считает на процессоре и без ограничений занимается одним клиентом целиком.
    """
    await check_rate_limit(request, scope="voice")
    rt = request.app.state.rt
    if not rt.dyn.get("voice.tts_enabled"):
        raise HTTPException(503, "Озвучка отключена администратором")
    tts_url = get_settings().tts_url
    if not tts_url:
        raise HTTPException(503, "Синтез речи не настроен (запустите профиль voice)")

    text = speech.prepare(body.text, int(rt.dyn.get("voice.tts_max_chars")))
    if not text:
        raise HTTPException(422, "Нечего озвучивать")
    if _speak_slots.locked():
        raise HTTPException(429, "Синтез занят, попробуйте через несколько секунд")
    async with _speak_slots, httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(f"{tts_url.rstrip('/')}/synthesize", json={"text": text})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(502, "Сервис синтеза речи недоступен") from exc
    # Ответ уже прочитан целиком: он короткий (секунды речи), и стримить нечего,
    # зато с готовым телом браузер знает длину и может перематывать.
    return Response(resp.content, media_type="audio/wav", headers={"Cache-Control": "no-store"})


async def _ask_stt(client: httpx.AsyncClient, stt_url: str, file: UploadFile, audio: bytes) -> Transcription:
    try:
        resp = await client.post(
            f"{stt_url.rstrip('/')}/transcribe",
            files={"file": (file.filename or "audio.webm", audio, file.content_type or "audio/webm")},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, "STT-сервис недоступен, попробуйте текстом") from exc
    data = resp.json()
    # Только известные поля: STT — отдельный сервис, и новое поле в его ответе
    # не должно ронять распознавание.
    return Transcription(
        text=data.get("text", ""),
        duration=data.get("duration", 0.0),
        language=data.get("language"),
    )
