from service.rtc_service.rtc_provider import _mask_rtc_config_for_logging


def test_mask_rtc_config_hides_credentials_without_mutating_input():
    config = {
        "urls": ["turn:example.com:3478?transport=udp"],
        "username": "turnuser",
        "credential": "secret-password",
    }

    masked = _mask_rtc_config_for_logging(config)

    assert masked["credential"] == "***masked***"
    assert config["credential"] == "secret-password"
