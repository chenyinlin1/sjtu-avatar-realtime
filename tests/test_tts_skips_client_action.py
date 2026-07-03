from types import SimpleNamespace

from handlers.tts.bailian_tts.tts_handler_cosyvoice_bailian import HandlerTTS, TTSContext


class ActionOnlyBundle:
    metadata = {
        "client_action": {
            "type": "music.play",
            "url": "https://example.com/song.mp3",
        }
    }

    def get_main_data(self):
        return ""


def test_tts_skips_action_only_empty_text_without_creating_session():
    context = TTSContext("test-session")
    handler = HandlerTTS()
    data = SimpleNamespace(
        stream_id=SimpleNamespace(key="avatar-text-stream"),
        data=ActionOnlyBundle(),
        is_last_data=False,
    )

    context.handle_text_stream(data, handler)

    assert context.api_links == {}
