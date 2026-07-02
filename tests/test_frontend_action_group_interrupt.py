from pathlib import Path


COMPONENT = Path(
    "src/service/frontend_service/frontend/src/renderer/src/components/ActionGroup.vue"
)


def _component_source() -> str:
    return COMPONENT.read_text(encoding="utf-8")


def test_action_group_has_visible_interrupt_control():
    source = _component_source()

    assert "interrupt-action" in source
    assert "handleInterrupt" in source
    assert "HandStop" in source
    assert "打断当前回复" in source


def test_interrupt_control_uses_replying_state_for_enabled_style():
    source = _component_source()

    assert "replying" in source
    assert "canInterrupt" in source
    assert "{ active: canInterrupt, disabled: !canInterrupt }" in source
    assert ":aria-disabled=\"!canInterrupt\"" in source


def test_interrupt_control_dispatches_to_current_chat_mode():
    source = _component_source()

    assert "appStore.chatMode === 'ws'" in source
    assert "wsChatStore.interrupt()" in source
    assert "videoChatStore.interrupt()" in source
