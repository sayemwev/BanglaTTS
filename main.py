"""
Bangla TTS API
Microsoft Edge-এর নিউরাল ভয়েস দিয়ে টেক্সট থেকে MP3 অডিও তৈরি করে।
"""
import asyncio
import hmac
import os
import re
import time
from collections import defaultdict, deque
from typing import Optional

import edge_tts
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

# ---------- সেটিংস (Render-এর Environment থেকে বদলানো যায়) ----------
API_KEY = os.getenv("API_KEY", "").strip()            # খালি রাখলে key লাগবে না
MAX_CHARS = int(os.getenv("MAX_CHARS", "3000"))        # একবারে সর্বোচ্চ অক্ষর
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))  # প্রতি IP প্রতি মিনিটে
MAX_PARALLEL = int(os.getenv("MAX_PARALLEL", "3"))     # একসাথে কয়টি কাজ
DEFAULT_VOICE = os.getenv("DEFAULT_VOICE", "bn-BD-NabanitaNeural")

VOICES = [
    {"id": "bn-BD-NabanitaNeural", "name": "Nabanita", "gender": "Female", "country": "Bangladesh"},
    {"id": "bn-BD-PradeepNeural", "name": "Pradeep", "gender": "Male", "country": "Bangladesh"},
    {"id": "bn-IN-TanishaaNeural", "name": "Tanishaa", "gender": "Female", "country": "India"},
    {"id": "bn-IN-BashkarNeural", "name": "Bashkar", "gender": "Male", "country": "India"},
]

VOICE_PATTERN = re.compile(r"^[a-z]{2,3}-[A-Z]{2}-[A-Za-z0-9]+Neural$")

app = FastAPI(title="Bangla TTS API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

sem = asyncio.Semaphore(MAX_PARALLEL)
hits = defaultdict(deque)


def check_access(request: Request):
    """API key ও রেট লিমিট যাচাই।"""
    if API_KEY:
        key = request.headers.get("x-api-key") or request.query_params.get("key") or ""
        if not hmac.compare_digest(key.encode(), API_KEY.encode()):
            raise HTTPException(status_code=401, detail="API key ভুল বা দেওয়া হয়নি")

    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
    now = time.time()
    if len(hits) > 5000:
        hits.clear()
    q = hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_LIMIT_PER_MIN:
        raise HTTPException(status_code=429, detail="অনেক বেশি অনুরোধ, কিছুক্ষণ পর আবার চেষ্টা করুন")
    q.append(now)


async def make_audio(text: str, voice: str, rate: int, pitch: int) -> bytes:
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text খালি")
    if len(text) > MAX_CHARS:
        raise HTTPException(status_code=413, detail=f"লেখা অনেক বড়। সর্বোচ্চ {MAX_CHARS} অক্ষর, টুকরা করে পাঠান")
    if not VOICE_PATTERN.match(voice):
        raise HTTPException(status_code=400, detail="voice ঠিক নেই। /voices দেখুন")

    rate = max(-50, min(100, rate))
    pitch = max(-50, min(50, pitch))
    rate_s = f"{rate:+d}%"
    pitch_s = f"{pitch:+d}Hz"

    async with sem:
        for _ in range(3):
            try:
                comm = edge_tts.Communicate(text, voice, rate=rate_s, pitch=pitch_s)
                parts = []
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        parts.append(chunk["data"])
                audio = b"".join(parts)
                if audio:
                    return audio
            except Exception:
                await asyncio.sleep(0.5)
    raise HTTPException(status_code=502, detail="অডিও তৈরি করা যায়নি, একটু পরে আবার চেষ্টা করুন")


def audio_response(audio: bytes) -> Response:
    return Response(content=audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@app.get("/")
async def home():
    return {
        "status": "ok",
        "service": "Bangla TTS API",
        "use": "/tts?text=আপনার লেখা&voice=bn-BD-NabanitaNeural&rate=0&pitch=0&key=YOUR_KEY",
        "voices": "/voices",
        "max_chars": MAX_CHARS,
    }


@app.get("/voices")
async def voices():
    return VOICES


@app.get("/tts")
async def tts_get(
    request: Request,
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: int = 0,
    pitch: int = 0,
):
    check_access(request)
    return audio_response(await make_audio(text, voice, rate, pitch))


class TTSBody(BaseModel):
    text: str
    voice: Optional[str] = None
    rate: int = 0
    pitch: int = 0


@app.post("/tts")
async def tts_post(request: Request, body: TTSBody):
    check_access(request)
    return audio_response(await make_audio(body.text, body.voice or DEFAULT_VOICE, body.rate, body.pitch))
