from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "src/service/frontend_service/frontend/src/renderer/src"


def read_frontend_file(relative_path: str) -> str:
    return (FRONTEND / relative_path).read_text(encoding="utf-8")


def compact(text: str) -> str:
    return " ".join(text.split())


def test_web_permission_prompt_auto_accesses_after_init_config():
    app_vue = read_frontend_file("App.vue")
    app_compact = compact(app_vue)

    assert "import { onMounted, ref } from 'vue'" in app_vue
    assert "const appReady = ref(false)" in app_vue
    assert "await appState.init()" in app_vue
    assert "appReady.value = true" in app_vue
    assert (
        '<WebcamPermission v-if="appReady && !mediaState.webcamAccessed" auto-access />'
        in app_compact
    )


def test_audio_only_permission_prompt_uses_mic_specific_copy():
    permission_vue = read_frontend_file("components/WebcamPermission.vue")

    assert "isAudioOnly.value ? '点击允许访问麦克风'" in permission_vue
    assert "点击允许访问麦克风" in permission_vue


def test_required_mic_permission_failure_does_not_enter_silent_session():
    media_ts = read_frontend_file("store/media.ts")

    assert "requestRequiredAudioPermission" in media_ts
    assert "stopMediaStream(permissionStream)" in media_ts
    assert "未获取到麦克风权限" in media_ts
    assert "if (audioEnabled && !(await requestRequiredAudioPermission()))" in media_ts
    assert "return" in media_ts.split("if (audioEnabled && !(await requestRequiredAudioPermission()))", 1)[1].split("}", 1)[0]
