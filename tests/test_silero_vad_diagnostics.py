import numpy as np

from handlers.vad.silerovad.vad_handler_silero import _get_vad_clip_stats


def test_get_vad_clip_stats_reports_input_and_model_peaks():
    clip = np.array([-0.5, 0.25], dtype=np.float32)
    model_clip = clip.copy()

    stats = _get_vad_clip_stats(clip, model_clip)

    assert stats["input_peak"] == 0.5
    assert stats["model_peak"] == 0.5
    assert round(stats["input_rms"], 4) == 0.3953
