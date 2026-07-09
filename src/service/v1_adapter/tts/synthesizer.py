from __future__ import annotations

import io
import threading
import wave
from dataclasses import dataclass
from typing import Optional


SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH_BYTES = 2


class SpeechSynthesisError(Exception):
    pass


@dataclass(frozen=True)
class SpeechAudio:
    pcm_bytes: bytes
    wav_bytes: bytes
    sample_rate: int = SAMPLE_RATE
    channels: int = CHANNELS
    sample_width_bytes: int = SAMPLE_WIDTH_BYTES

    @property
    def duration_ms(self) -> int:
        bytes_per_second = self.sample_rate * self.channels * self.sample_width_bytes
        return int(len(self.pcm_bytes) * 1000 / bytes_per_second) if bytes_per_second else 0


class BailianSpeechSynthesizer:
    def __init__(self, *, api_key: Optional[str] = None, timeout_seconds: float = 30.0):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def synthesize(self, *, text: str, model: str, voice: str, instruction: Optional[str] = None) -> SpeechAudio:
        SpeechSynthesizer, AudioFormat, dashscope = self._load_dashscope()
        if self.api_key:
            dashscope.api_key = self.api_key

        callback = self._create_callback()
        kwargs = {
            "model": model,
            "voice": voice,
            "callback": callback,
            "format": AudioFormat.PCM_24000HZ_MONO_16BIT,
        }
        if instruction:
            kwargs["instruction"] = instruction

        synthesizer = SpeechSynthesizer(**kwargs)
        try:
            synthesizer.streaming_call(text)
            synthesizer.streaming_complete()
            callback.wait(self.timeout_seconds)
        except SpeechSynthesisError:
            self._cancel(synthesizer)
            raise
        except Exception as exc:
            self._cancel(synthesizer)
            raise SpeechSynthesisError(str(exc)) from exc

        pcm_bytes = callback.pcm_bytes
        if not pcm_bytes:
            raise SpeechSynthesisError("empty audio returned by TTS upstream")
        return SpeechAudio(pcm_bytes=pcm_bytes, wav_bytes=pcm_to_wav(pcm_bytes))

    @staticmethod
    def _load_dashscope():
        try:
            import dashscope
            from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer
        except Exception as exc:
            raise SpeechSynthesisError("dashscope TTS SDK is not available") from exc
        return SpeechSynthesizer, AudioFormat, dashscope

    @staticmethod
    def _cancel(synthesizer) -> None:
        try:
            synthesizer.streaming_cancel()
        except Exception:
            pass

    @staticmethod
    def _create_callback():
        from dashscope.audio.tts_v2 import ResultCallback

        class CollectingCallback(ResultCallback):
            def __init__(self):
                super().__init__()
                self._event = threading.Event()
                self._chunks = []
                self._error = None

            @property
            def pcm_bytes(self) -> bytes:
                return b"".join(self._chunks)

            def wait(self, timeout_seconds: float) -> None:
                if not self._event.wait(timeout_seconds):
                    raise SpeechSynthesisError("TTS upstream timeout")
                if self._error:
                    raise SpeechSynthesisError(self._error)

            def on_data(self, data: bytes) -> None:
                self._chunks.append(data)

            def on_complete(self) -> None:
                self._event.set()

            def on_error(self, message) -> None:
                self._error = str(message)
                self._event.set()

        return CollectingCallback()


def pcm_to_wav(pcm_bytes: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(CHANNELS)
        wav_file.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return buffer.getvalue()
