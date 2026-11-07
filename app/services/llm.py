"""
AI provider layer.

Everything that talks to a model goes through this module, so the rest of the
app never touches an SDK directly. It provides:

* streaming chat with Gemini "thought summaries" (shown in the UI as the
  live status line and the collapsible *Thoughts* panel),
* structured JSON generation validated against Pydantic schemas,
* real multimodal video understanding (YouTube URLs and uploaded files go to
  Gemini, which watches the frames and listens to the audio),
* multi-speaker text-to-speech for Audio / Video Overviews,
* Google Search grounding for "Search the web for new sources".

Model names change over time, so each capability has an ordered list of
candidate models (see config). The first model the API accepts is remembered
and reused. Errors are mapped onto short, user-safe messages; keys are never
logged or echoed.

OpenAI is supported as a text-only fallback (chat, notes, quizzes, …) when no
Gemini key is configured. Video understanding, TTS and web search need Gemini.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import time
import wave
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

NOT_CONFIGURED_MESSAGE = (
    "AI is not configured yet. Add GEMINI_API_KEY=your_key to the .env file in the "
    "project folder (get a key at aistudio.google.com/apikey), then restart the server."
)


class AIError(Exception):
    """A failure that is safe to show to the user as-is."""

    def __init__(self, message: str, code: str = "error", *, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable


@dataclass
class StreamEvent:
    kind: str  # "thought" | "text"
    text: str


@dataclass
class MediaRef:
    """A piece of media for the model: a YouTube URL or an uploaded file URI."""

    uri: str
    mime_type: str = ""
    start_sec: Optional[float] = None
    end_sec: Optional[float] = None
    fps: Optional[float] = None


@dataclass
class AudioClip:
    pcm: bytes          # 16-bit little-endian mono PCM
    sample_rate: int = 24000

    @property
    def seconds(self) -> float:
        return len(self.pcm) / 2 / self.sample_rate if self.sample_rate else 0.0


def _clean_provider_message(text: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    # Never echo anything key-shaped back.
    text = re.sub(r"(AIza|AQ\.)[A-Za-z0-9_\-\.]{8,}", "***", text)
    text = re.sub(r"key=[^&\s]+", "key=***", text)
    return text[:limit]


def _extract_json(text: str) -> Any:
    """Parse a JSON value from model text, tolerating code fences and preambles."""
    if not text:
        raise ValueError("empty response")
    raw = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.S)
    if fence:
        raw = fence.group(1)
    try:
        return json.loads(raw)
    except ValueError:
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = raw.find(open_ch)
        end = raw.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except ValueError:
                continue
    raise ValueError("no JSON object found")


def pcm_from_audio_bytes(data: bytes, mime_type: str = "") -> AudioClip:
    """TTS models return either a complete WAV file or raw 16-bit PCM."""
    if data[:4] == b"RIFF":
        with wave.open(io.BytesIO(data), "rb") as wav:
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
            if wav.getsampwidth() != 2:
                raise AIError("The speech model returned an unsupported audio format.", "audio")
            if wav.getnchannels() == 2:
                # Down-mix to mono by taking the left channel.
                frames = b"".join(frames[i : i + 2] for i in range(0, len(frames), 4))
            return AudioClip(frames, rate)
    rate = 24000
    match = re.search(r"rate=(\d+)", mime_type or "")
    if match:
        rate = int(match.group(1))
    return AudioClip(data, rate)


def wav_bytes(clips: list[AudioClip], gap_seconds: float = 0.25) -> tuple[bytes, list[float]]:
    """Concatenate clips into one WAV. Returns (wav, start offsets in seconds)."""
    if not clips:
        raise AIError("No audio was produced.", "audio")
    rate = clips[0].sample_rate
    silence = b"\x00\x00" * int(rate * gap_seconds)
    offsets: list[float] = []
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        position = 0.0
        for i, clip in enumerate(clips):
            offsets.append(position)
            pcm = clip.pcm
            if clip.sample_rate != rate:
                pcm = _resample(pcm, clip.sample_rate, rate)
            out.writeframes(pcm)
            position += len(pcm) / 2 / rate
            if i < len(clips) - 1 and silence:
                out.writeframes(silence)
                position += gap_seconds
    return buf.getvalue(), offsets


def _resample(pcm: bytes, src: int, dst: int) -> bytes:
    """Nearest-neighbour resample — only used if TTS calls disagree on rate."""
    import array

    samples = array.array("h")
    samples.frombytes(pcm)
    if not samples or src == dst:
        return pcm
    ratio = src / dst
    out = array.array("h", (samples[min(len(samples) - 1, int(i * ratio))] for i in range(int(len(samples) / ratio))))
    return out.tobytes()


# ═════════════════════════════════════════════════════════════════
# Provider interface
# ═════════════════════════════════════════════════════════════════


class Provider:
    name = "none"
    supports_media = False
    supports_tts = False
    supports_search = False
    supports_thoughts = False

    async def stream(
        self, *, system: str, contents: list[dict], tier: str = "default", thinking: bool = True,
        max_tokens: int = 12000,
    ) -> AsyncIterator[StreamEvent]:  # pragma: no cover - interface
        raise NotImplementedError
        yield  # noqa

    async def text(
        self, *, system: str, prompt: str, tier: str = "default", max_tokens: int = 16000,
        timeout: float | None = None,
    ) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    async def json(
        self, *, system: str, prompt: str, schema: Type[T], tier: str = "default",
        media: list[MediaRef] | None = None, max_tokens: int = 32000,
        timeout: float | None = None, low_media_resolution: bool = False,
    ) -> T:  # pragma: no cover - interface
        raise NotImplementedError

    async def upload_media(self, path: str, mime_type: str, display_name: str) -> MediaRef:
        raise AIError("Analysing uploaded media needs a Gemini API key.", "unsupported")

    async def tts(self, turns: list[tuple[str, str]], voices: dict[str, str], *, style: str = "") -> AudioClip:
        raise AIError("Audio generation needs a Gemini API key (text-to-speech).", "unsupported")

    async def web_search(self, query: str, *, youtube_only: bool = False, deep: bool = False) -> dict:
        raise AIError("Web search needs a Gemini API key (Google Search grounding).", "unsupported")

    async def ping(self) -> dict:
        reply = await self.text(system="Reply with the single word OK.", prompt="ping", tier="lite", max_tokens=64)
        return {"ok": bool(reply.strip()), "model": getattr(self, "last_model", "")}


# ═════════════════════════════════════════════════════════════════
# Gemini
# ═════════════════════════════════════════════════════════════════


class GeminiProvider(Provider):
    name = "gemini"
    supports_media = True
    supports_tts = True
    supports_search = True
    supports_thoughts = True

    def __init__(self, api_key: str) -> None:
        from google import genai
        from google.genai import errors, types

        self.types = types
        self.errors = errors
        http_options = types.HttpOptions(timeout=int(settings.llm_timeout * 1000))
        base_url = os.getenv("GEMINI_BASE_URL", "").strip()
        if base_url:  # advanced: an API gateway / proxy in front of Gemini
            http_options.base_url = base_url
        self.client = genai.Client(api_key=api_key, http_options=http_options)
        self._working: dict[str, str] = {}
        self._dead_models: set[str] = set()
        self._no_thinking: set[str] = set()
        self.last_model = ""

    # ── model selection ──────────────────────────────────────────

    def _candidates(self, tier: str) -> list[str]:
        table = {
            "default": settings.gemini_models,
            "pro": settings.gemini_pro_models + settings.gemini_models,
            "lite": settings.gemini_lite_models,
            "tts": settings.gemini_tts_models,
        }
        ordered: list[str] = []
        preferred = self._working.get(tier)
        if preferred:
            ordered.append(preferred)
        for model in table.get(tier, settings.gemini_models):
            if model not in ordered and model not in self._dead_models:
                ordered.append(model)
        if not ordered:  # every candidate was marked dead; try them again
            self._dead_models.clear()
            ordered = list(dict.fromkeys(table.get(tier, settings.gemini_models)))
        return ordered

    def _is_model_error(self, exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        msg = str(getattr(exc, "message", "") or exc).lower()
        if code == 404:
            return True
        return "model" in msg and any(
            s in msg for s in ("not found", "not supported", "is not available", "unknown model",
                               "does not exist", "no longer available", "deprecated")
        )

    def _is_thinking_error(self, exc: Exception) -> bool:
        msg = str(getattr(exc, "message", "") or exc).lower()
        return getattr(exc, "code", None) == 400 and ("thinking" in msg or "thought" in msg)

    def _map_error(self, exc: Exception) -> AIError:
        if isinstance(exc, AIError):
            return exc
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timeout" in type(exc).__name__.lower():
            return AIError("The AI took too long to respond. Please try again.", "timeout", retryable=True)
        code = getattr(exc, "code", None)
        status = str(getattr(exc, "status", "") or "")
        message = _clean_provider_message(getattr(exc, "message", "") or str(exc))
        low = message.lower()
        # The machine-readable reason (e.g. ACCESS_TOKEN_TYPE_UNSUPPORTED) lives in the error details.
        detail = (str(getattr(exc, "details", "") or "") + " " + str(exc)).lower()
        if "access_token_type_unsupported" in detail:
            return AIError(
                "Gemini didn't accept this API key (ACCESS_TOKEN_TYPE_UNSUPPORTED). Copy the complete key from "
                "aistudio.google.com/apikey into GEMINI_API_KEY in .env, without quotes or spaces. New keys that "
                "start with 'AQ.' are supported; if it still fails, create a new key and restart the server.",
                "auth")
        if "api key not valid" in low or "api_key_invalid" in low or "invalid api key" in low:
            return AIError(
                "Gemini rejected the API key (GEMINI_API_KEY in .env). Create a new key at "
                "aistudio.google.com/apikey, update .env and restart the server.", "auth")
        if code in (401, 403) or status in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
            return AIError(
                f"Gemini refused the request ({status or code}). Check that GEMINI_API_KEY is valid "
                f"and allowed to use the Gemini API. Details: {message}", "auth")
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            return AIError(
                "Gemini's rate limit or quota was reached. Wait a minute and try again "
                "(free-tier keys have per-minute and daily limits).", "quota", retryable=True)
        if code in (500, 502, 503, 504) or status in ("UNAVAILABLE", "INTERNAL", "DEADLINE_EXCEEDED"):
            return AIError("Gemini is temporarily unavailable. Please try again shortly.", "unavailable",
                           retryable=True)
        if code == 400 or status == "INVALID_ARGUMENT":
            return AIError(f"The AI request was rejected: {message}", "bad_request")
        if code == 404:
            return AIError(f"The configured Gemini model was not found: {message}", "not_found")
        transport = {cls.__name__ for cls in type(exc).__mro__} & {
            "TransportError", "NetworkError", "ProxyError", "ConnectError", "RemoteProtocolError"}
        if transport or isinstance(exc, (ConnectionError, OSError)) or "connect" in type(exc).__name__.lower():
            hint = " A proxy or firewall blocked the request." if "ProxyError" in transport else ""
            return AIError("Couldn't reach the Gemini API. Check your internet connection." + hint, "network",
                           retryable=True)
        logger.warning("unexpected AI error", extra={"error_type": type(exc).__name__})
        return AIError(f"The AI request failed: {message or type(exc).__name__}", "error")

    async def _call(self, tier: str, fn, *, retries: int = 2):
        """Run `fn(model)` over the candidate models with retries for transient errors."""
        last_exc: Exception | None = None
        for model in self._candidates(tier):
            attempt = 0
            while True:
                try:
                    result = await fn(model)
                    self._working[tier] = model
                    self.last_model = model
                    return result
                except AIError:
                    raise
                except Exception as exc:  # noqa: BLE001 - mapped below
                    last_exc = exc
                    if self._is_model_error(exc):
                        logger.info("model unavailable, trying next", extra={"model": model})
                        self._dead_models.add(model)
                        break
                    mapped = self._map_error(exc)
                    if mapped.retryable and attempt < retries:
                        attempt += 1
                        await asyncio.sleep(2 * attempt + (attempt - 1) * 2)
                        continue
                    raise mapped from exc
        if last_exc is not None:
            raise AIError(
                "None of the configured Gemini models are available to this API key. "
                "Set GEMINI_MODEL in .env to a model your key can use.", "not_found") from last_exc
        raise AIError("No Gemini model is configured.", "not_found")

    # ── content helpers ──────────────────────────────────────────

    def _media_part(self, ref: MediaRef):
        t = self.types
        meta = None
        if ref.start_sec is not None or ref.end_sec is not None or ref.fps:
            meta = t.VideoMetadata(
                start_offset=f"{int(ref.start_sec)}s" if ref.start_sec is not None else None,
                end_offset=f"{int(ref.end_sec)}s" if ref.end_sec is not None else None,
                fps=ref.fps,
            )
        file_data = t.FileData(file_uri=ref.uri, mime_type=ref.mime_type or None)
        return t.Part(file_data=file_data, video_metadata=meta)

    def _contents(self, contents: list[dict]):
        t = self.types
        out = []
        for turn in contents:
            role = "model" if turn.get("role") in ("model", "assistant") else "user"
            parts = [self._media_part(m) for m in turn.get("media", [])]
            if turn.get("text"):
                parts.append(t.Part(text=turn["text"]))
            if parts:
                out.append(t.Content(role=role, parts=parts))
        return out

    def _config(self, *, system: str, max_tokens: int, timeout: float | None = None, thinking: bool = False,
                low_media_resolution: bool = False, **extra):
        t = self.types
        if timeout:
            extra["http_options"] = t.HttpOptions(timeout=int(timeout * 1000))
        if thinking:
            extra["thinking_config"] = t.ThinkingConfig(include_thoughts=True)
        if low_media_resolution:
            extra["media_resolution"] = t.MediaResolution.MEDIA_RESOLUTION_LOW
        return t.GenerateContentConfig(
            system_instruction=system or None,
            max_output_tokens=max_tokens,
            # No tools are ever passed for function calling; keep the SDK from
            # running its automatic function-calling loop.
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
            **extra,
        )

    @staticmethod
    def _check_blocked(response) -> None:
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            raise AIError("The AI declined this request (safety filter). Try rephrasing it.", "blocked")

    # ── streaming chat ───────────────────────────────────────────

    async def stream(self, *, system, contents, tier="default", thinking=True, max_tokens=12000):
        t = self.types
        gcontents = self._contents(contents)
        emitted = False
        last_exc: Exception | None = None

        for model in self._candidates(tier):
            use_thinking = thinking and model not in self._no_thinking
            attempt = 0
            while True:
                cfg = self._config(system=system, max_tokens=max_tokens, thinking=use_thinking)
                try:
                    iterator = await self.client.aio.models.generate_content_stream(
                        model=model, contents=gcontents, config=cfg
                    )
                    finish = None
                    async for chunk in iterator:
                        self._check_blocked(chunk)
                        for cand in chunk.candidates or []:
                            if cand.finish_reason:
                                finish = str(cand.finish_reason)
                            content = cand.content
                            for part in (content.parts if content and content.parts else []):
                                if not part.text:
                                    continue
                                emitted = True
                                yield StreamEvent("thought" if part.thought else "text", part.text)
                    if finish and "SAFETY" in finish and not emitted:
                        raise AIError("The AI declined to answer this (safety filter).", "blocked")
                    self._working[tier] = model
                    self.last_model = model
                    return
                except AIError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if emitted:
                        raise self._map_error(exc) from exc
                    if use_thinking and self._is_thinking_error(exc):
                        self._no_thinking.add(model)
                        use_thinking = False
                        continue
                    if self._is_model_error(exc):
                        self._dead_models.add(model)
                        break
                    mapped = self._map_error(exc)
                    if mapped.retryable and attempt < 2:
                        attempt += 1
                        await asyncio.sleep(2 * attempt)
                        continue
                    raise mapped from exc
        raise AIError(
            "None of the configured Gemini models are available to this API key. "
            "Set GEMINI_MODEL in .env to a model your key can use.", "not_found") from last_exc

    # ── one-shot text ────────────────────────────────────────────

    async def text(self, *, system, prompt, tier="default", max_tokens=16000, timeout=None):
        contents = self._contents([{"role": "user", "text": prompt}])

        async def run(model):
            cfg = self._config(system=system, max_tokens=max_tokens, timeout=timeout)
            response = await self.client.aio.models.generate_content(model=model, contents=contents, config=cfg)
            self._check_blocked(response)
            return response.text or ""

        result = await self._call(tier, run)
        return result[: settings.max_llm_response_chars]

    # ── structured JSON ──────────────────────────────────────────

    async def json(self, *, system, prompt, schema, tier="default", media=None, max_tokens=32000,
                   timeout=None, low_media_resolution=False):
        t = self.types
        contents = self._contents([{"role": "user", "text": prompt, "media": media or []}])

        async def run(model):
            cfg = self._config(
                system=system,
                max_tokens=max_tokens,
                timeout=timeout,
                low_media_resolution=low_media_resolution,
                response_mime_type="application/json",
                response_schema=schema,
            )
            response = await self.client.aio.models.generate_content(model=model, contents=contents, config=cfg)
            self._check_blocked(response)
            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, schema):
                return parsed
            raw = response.text or ""
            try:
                return schema.model_validate(_extract_json(raw))
            except (ValueError, ValidationError) as exc:
                finish = ""
                if response.candidates:
                    finish = str(response.candidates[0].finish_reason or "")
                if "MAX_TOKENS" in finish:
                    raise AIError("The AI response was too long and got cut off. Try a narrower request.",
                                  "too_long") from exc
                raise AIError("The AI returned an unexpected format. Please try again.", "format",
                              retryable=True) from exc

        try:
            return await self._call(tier, run, retries=1)
        except AIError as err:
            if err.code == "format":
                return await self._call(tier, run, retries=0)
            raise

    # ── media upload (Files API) ─────────────────────────────────

    async def upload_media(self, path, mime_type, display_name):
        t = self.types
        try:
            uploaded = await self.client.aio.files.upload(
                file=path, config=t.UploadFileConfig(mime_type=mime_type, display_name=display_name[:120])
            )
            deadline = time.monotonic() + 900
            while uploaded.state is not None and str(uploaded.state).endswith("PROCESSING"):
                if time.monotonic() > deadline:
                    raise AIError("Gemini took too long to process the uploaded file.", "timeout")
                await asyncio.sleep(4)
                uploaded = await self.client.aio.files.get(name=uploaded.name)
            if uploaded.state is not None and str(uploaded.state).endswith("FAILED"):
                raise AIError("Gemini could not process this media file (it may be corrupt or unsupported).",
                              "media")
            return MediaRef(uri=uploaded.uri, mime_type=uploaded.mime_type or mime_type)
        except AIError:
            raise
        except ValueError as exc:  # e.g. Vertex mode
            raise AIError(f"Uploading media is not supported with this Gemini setup: {exc}", "unsupported") from exc
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc) from exc

    # ── text to speech ───────────────────────────────────────────

    async def tts(self, turns, voices, *, style=""):
        t = self.types
        speakers = list(dict.fromkeys(s for s, _ in turns))
        multi = len(speakers) > 1

        def speech_config():
            if multi:
                return t.SpeechConfig(
                    multi_speaker_voice_config=t.MultiSpeakerVoiceConfig(
                        speaker_voice_configs=[
                            t.SpeakerVoiceConfig(
                                speaker=s,
                                voice_config=t.VoiceConfig(
                                    prebuilt_voice_config=t.PrebuiltVoiceConfig(voice_name=voices.get(s, "Kore"))
                                ),
                            )
                            for s in speakers[:2]
                        ]
                    )
                )
            return t.SpeechConfig(
                voice_config=t.VoiceConfig(
                    prebuilt_voice_config=t.PrebuiltVoiceConfig(voice_name=voices.get(speakers[0], "Kore"))
                )
            )

        def modern_contents():
            parts = []
            for speaker, text in turns:
                meta = None
                if multi or style:
                    meta = t.SpeechMetadata(speaker=speaker if multi else None, style=style or None)
                parts.append(t.Part(text=text, speech_metadata=meta))
            return [t.Content(role="user", parts=parts)]

        def legacy_contents():
            lines = "\n".join(f"{s}: {txt}" if multi else txt for s, txt in turns)
            lead = f"Read this aloud{(' in a ' + style + ' tone') if style else ''}"
            lead += " as a conversation between " + " and ".join(speakers[:2]) + ":\n" if multi else ":\n"
            return [t.Content(role="user", parts=[t.Part(text=lead + lines)])]

        async def run(model):
            cfg = t.GenerateContentConfig(
                response_modalities=["AUDIO"], speech_config=speech_config(),
                http_options=t.HttpOptions(timeout=int(max(settings.llm_timeout, 300) * 1000)),
                automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
            )
            last_exc = None
            for builder in (modern_contents, legacy_contents):
                try:
                    response = await self.client.aio.models.generate_content(
                        model=model, contents=builder(), config=cfg
                    )
                except Exception as exc:  # noqa: BLE001
                    if getattr(exc, "code", None) == 400 and builder is modern_contents:
                        last_exc = exc
                        continue
                    raise
                self._check_blocked(response)
                pcm = b""
                rate = 24000
                for cand in response.candidates or []:
                    for part in (cand.content.parts if cand.content and cand.content.parts else []):
                        blob = part.inline_data
                        if blob and blob.data:
                            clip = pcm_from_audio_bytes(blob.data, blob.mime_type or "")
                            pcm += clip.pcm
                            rate = clip.sample_rate
                if pcm:
                    return AudioClip(pcm, rate)
                raise AIError("The speech model returned no audio.", "audio", retryable=True)
            raise last_exc  # type: ignore[misc]

        return await self._call("tts", run)

    # ── web search (Google Search grounding) ─────────────────────

    async def web_search(self, query, *, youtube_only=False, deep=False):
        t = self.types
        what = "YouTube videos (lectures, tutorials, explainers)" if youtube_only else "web pages, articles, documentation and tutorials"
        prompt = (
            f"Search the web and find the best {what} for a student who wants to learn about:\n"
            f"\"{query}\"\n\n"
            f"Prefer authoritative, educational, beginner-friendly sources. "
            f"{'Look broadly and compare many sources. ' if deep else ''}"
            "Write a short overview (2-4 sentences) of what these sources cover."
        )
        if youtube_only:
            prompt += " Only include results from youtube.com."

        async def run(model):
            cfg = t.GenerateContentConfig(
                tools=[t.Tool(google_search=t.GoogleSearch())], max_output_tokens=2048,
                automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
            )
            response = await self.client.aio.models.generate_content(model=model, contents=prompt, config=cfg)
            self._check_blocked(response)
            results = []
            seen = set()
            for cand in response.candidates or []:
                gm = getattr(cand, "grounding_metadata", None)
                for ch in (getattr(gm, "grounding_chunks", None) or []):
                    web = getattr(ch, "web", None)
                    if not web or not web.uri or web.uri in seen:
                        continue
                    seen.add(web.uri)
                    results.append({
                        "title": (web.title or web.domain or "Web result")[:200],
                        "url": web.uri,
                        "domain": (getattr(web, "domain", None) or web.title or "")[:120],
                    })
            return {"summary": (response.text or "").strip(), "results": results[: (16 if deep else 10)]}

        return await self._call("default", run)


# ═════════════════════════════════════════════════════════════════
# OpenAI (text-only fallback)
# ═════════════════════════════════════════════════════════════════


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, api_key: str) -> None:
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=api_key, timeout=settings.llm_timeout, max_retries=2)
        self.last_model = settings.openai_model

    def _messages(self, system: str, contents: list[dict]) -> list[dict]:
        msgs = [{"role": "system", "content": system}] if system else []
        for turn in contents:
            if turn.get("media"):
                raise AIError("Video and audio analysis needs a Gemini API key.", "unsupported")
            role = "assistant" if turn.get("role") in ("model", "assistant") else "user"
            msgs.append({"role": role, "content": turn.get("text", "")})
        return msgs

    def _map(self, exc: Exception) -> AIError:
        name = type(exc).__name__
        if "Authentication" in name:
            return AIError("OpenAI rejected the API key (OPENAI_API_KEY in .env).", "auth")
        if "RateLimit" in name:
            return AIError("OpenAI's rate limit or quota was reached. Try again shortly.", "quota", retryable=True)
        if "Timeout" in name:
            return AIError("The AI took too long to respond. Please try again.", "timeout", retryable=True)
        return AIError(f"The AI request failed: {_clean_provider_message(str(exc))}", "error")

    async def stream(self, *, system, contents, tier="default", thinking=True, max_tokens=12000):
        try:
            response = await self.client.chat.completions.create(
                model=settings.openai_model, messages=self._messages(system, contents),
                stream=True, max_tokens=min(max_tokens, 16000),
            )
            async for chunk in response:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield StreamEvent("text", delta)
        except AIError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._map(exc) from exc

    async def text(self, *, system, prompt, tier="default", max_tokens=16000, timeout=None):
        try:
            response = await self.client.chat.completions.create(
                model=settings.openai_model, messages=self._messages(system, [{"role": "user", "text": prompt}]),
                max_tokens=min(max_tokens, 16000),
            )
            return response.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001
            raise self._map(exc) from exc

    async def json(self, *, system, prompt, schema, tier="default", media=None, max_tokens=32000,
                   timeout=None, low_media_resolution=False):
        if media:
            raise AIError("Video and audio analysis needs a Gemini API key.", "unsupported")
        schema_hint = json.dumps(schema.model_json_schema())[:6000]
        full = f"{prompt}\n\nReturn ONLY a JSON object that matches this JSON Schema:\n{schema_hint}"
        try:
            response = await self.client.chat.completions.create(
                model=settings.openai_model,
                messages=self._messages(system, [{"role": "user", "text": full}]),
                response_format={"type": "json_object"},
                max_tokens=min(max_tokens, 16000),
            )
            return schema.model_validate(_extract_json(response.choices[0].message.content or ""))
        except (ValueError, ValidationError) as exc:
            raise AIError("The AI returned an unexpected format. Please try again.", "format") from exc
        except AIError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._map(exc) from exc


# ═════════════════════════════════════════════════════════════════
# Accessors
# ═════════════════════════════════════════════════════════════════

_provider: Provider | None = None
_provider_override: Provider | None = None
_provider_key: str | None = None


def _clean_key(raw: str | None) -> str:
    """Tolerate keys pasted with surrounding quotes or whitespace."""
    return (raw or "").strip().strip("\"'").strip()


def get_provider() -> Provider | None:
    """The configured provider, or None if no key is set."""
    global _provider, _provider_key
    if _provider_override is not None:
        return _provider_override
    gemini_key = _clean_key(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    openai_key = _clean_key(os.getenv("OPENAI_API_KEY"))
    key_id = f"g:{bool(gemini_key)}:{len(gemini_key or '')}|o:{bool(openai_key)}"
    if _provider is not None and _provider_key == key_id:
        return _provider
    _provider_key = key_id
    if gemini_key:
        _provider = GeminiProvider(gemini_key)
    elif openai_key:
        _provider = OpenAIProvider(openai_key)
    else:
        _provider = None
    return _provider


def require_provider() -> Provider:
    provider = get_provider()
    if provider is None:
        raise AIError(NOT_CONFIGURED_MESSAGE, "not_configured")
    return provider


def set_provider_override(provider: Provider | None) -> None:
    """Test hook: route all AI calls to a stand-in provider."""
    global _provider_override
    _provider_override = provider


def capabilities() -> dict:
    provider = get_provider()
    if provider is None:
        return {"configured": False, "provider": None, "media": False, "tts": False, "search": False,
                "thoughts": False, "message": NOT_CONFIGURED_MESSAGE}
    return {
        "configured": True,
        "provider": provider.name,
        "media": provider.supports_media,
        "tts": provider.supports_tts,
        "search": provider.supports_search,
        "thoughts": provider.supports_thoughts,
        "model": getattr(provider, "last_model", ""),
    }
