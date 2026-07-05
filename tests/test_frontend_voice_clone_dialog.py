from pathlib import Path


COMPONENT = Path(
    "src/service/frontend_service/frontend/src/renderer/src/components/VoiceCloneControl.vue"
)
ACTION_GROUP = Path(
    "src/service/frontend_service/frontend/src/renderer/src/components/ActionGroup.vue"
)
APP_STORE = Path("src/service/frontend_service/frontend/src/renderer/src/store/app.ts")
APIS = Path("src/service/frontend_service/frontend/src/renderer/src/apis/index.ts")
WEBRTC_STORE = Path("src/service/frontend_service/frontend/src/renderer/src/store/webrtc.ts")
EVENT_TYPES = Path("src/service/frontend_service/frontend/src/renderer/src/interface/eventType.ts")


def test_voice_clone_dialog_guides_record_preview_and_submit_flow():
    source = COMPONENT.read_text(encoding="utf-8")

    assert "音色克隆" in source
    assert "朗读文案" in source
    assert "MediaRecorder" in source
    assert "<audio v-if=\"audioUrl\"" in source
    assert "重录" in source
    assert "使用这个音色" in source
    assert "恢复默认" in source
    assert "uploadVoiceClone" in source


def test_action_group_mounts_voice_clone_control_from_init_config():
    action_source = ACTION_GROUP.read_text(encoding="utf-8")
    store_source = APP_STORE.read_text(encoding="utf-8")
    api_source = APIS.read_text(encoding="utf-8")

    assert "VoiceCloneControl" in action_source
    assert ":upload-route=\"voiceCloneUploadRoute\"" in action_source
    assert ":sample-text=\"voiceCloneSampleText\"" in action_source
    assert "voiceCloneEnabled" in store_source
    assert "config.voice_clone?.upload_route" in store_source
    assert "uploadVoiceClone" in api_source
    assert "resetVoiceClone" in api_source



def test_web_persona_selection_drives_clone_controls_and_webrtc_device_info():
    action_source = ACTION_GROUP.read_text(encoding="utf-8")
    store_source = APP_STORE.read_text(encoding="utf-8")
    api_source = APIS.read_text(encoding="utf-8")
    webrtc_source = WEBRTC_STORE.read_text(encoding="utf-8")
    event_source = EVENT_TYPES.read_text(encoding="utf-8")

    assert "webPersonaItems" in action_source
    assert "selectWebPersona" in action_source
    assert ':persona-id="selectedPersonaId"' in action_source
    assert '@updated="refreshPersonas"' in action_source
    assert "face_upload_route_template" in store_source
    assert "voice_upload_route_template" in store_source
    assert "currentDeviceInfoPayload" in store_source
    assert "fillPersonaRoute" in api_source
    assert "DeviceInfo = 'DeviceInfo'" in event_source
    assert "DeviceInfoAck = 'DeviceInfoAck'" in event_source
    assert "sendDeviceInfo()" in webrtc_source
    assert "name: WsProtocol.DeviceInfo" in webrtc_source
