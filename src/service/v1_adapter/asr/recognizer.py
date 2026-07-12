from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import List, Optional

import dashscope
from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult


class SpeechRecognitionError(RuntimeError):
    pass


class SpeechRecognitionTimeout(SpeechRecognitionError):
    pass


@dataclass(frozen=True)
class RecognitionConfig:
    api_key: str
    model_name: str = "fun-asr-realtime"
    sample_rate: int = 16000
    audio_format: str = "pcm"
    semantic_punctuation_enabled: bool = False
    language_hints: Optional[List[str]] = None
    base_websocket_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    timeout_seconds: float = 30.0


class _TranscriptCallback(RecognitionCallback):
    def __init__(self) -> None:
        super().__init__()
        self.sentences: List[str] = []
        self.error_message: Optional[str] = None
        self.completed = threading.Event()

    def on_event(self, result: RecognitionResult) -> None:
        sentence = result.get_sentence()
        if sentence.get("text") and RecognitionResult.is_sentence_end(sentence):
            self.sentences.append(sentence["text"])

    def on_complete(self) -> None:
        self.completed.set()

    def on_error(self, result: RecognitionResult) -> None:
        self.error_message = result.message
        self.completed.set()

    def on_close(self) -> None:
        self.completed.set()

    def full_text(self) -> str:
        return "".join(self.sentences).strip()


class BailianSpeechRecognizer:
    _CHUNK_SIZE_BYTES = 3200

    def __init__(self, config: RecognitionConfig):
        self.config = config

    def transcribe(self, audio_bytes: bytes) -> str:
        callback = _TranscriptCallback()
        recognition = self._create_recognition(callback)
        try:
            recognition.start()
            self._send_audio(recognition, audio_bytes)
            recognition.stop()
        except Exception as exc:
            raise SpeechRecognitionError(str(exc)) from exc

        if not callback.completed.wait(self.config.timeout_seconds):
            raise SpeechRecognitionTimeout("recognition timed out")
        if callback.error_message:
            raise SpeechRecognitionError(callback.error_message)
        return callback.full_text()

    def _create_recognition(self, callback: _TranscriptCallback) -> Recognition:
        dashscope.api_key = self.config.api_key
        dashscope.base_websocket_api_url = self.config.base_websocket_url
        kwargs = {
            "model": self.config.model_name,
            "format": self.config.audio_format,
            "sample_rate": self.config.sample_rate,
            "semantic_punctuation_enabled": self.config.semantic_punctuation_enabled,
            "callback": callback,
        }
        if self.config.language_hints:
            kwargs["language_hints"] = self.config.language_hints
        return Recognition(**kwargs)

    def _send_audio(self, recognition: Recognition, audio_bytes: bytes) -> None:
        for offset in range(0, len(audio_bytes), self._CHUNK_SIZE_BYTES):
            recognition.send_audio_frame(audio_bytes[offset:offset + self._CHUNK_SIZE_BYTES])
