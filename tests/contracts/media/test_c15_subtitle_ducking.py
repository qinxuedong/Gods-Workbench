"""C15-AC2 词级字幕与音频闪避契约测试。"""

from gw.media.audio_ducking import build_ducking_envelope
from gw.media.subtitle_align import generate_webvtt


def test_c15_ac2_generates_word_timed_karaoke_webvtt():
    result = generate_webvtt(
        [
            {"word": "Hello", "start": 0.0, "end": 0.48},
            {"word": "world", "start": 0.5, "end": 1.18},
        ]
    )

    assert result == (
        "WEBVTT\n\n"
        "STYLE\n"
        "::cue(:past) { color: yellow; }\n"
        "::cue(:future) { color: white; }\n\n"
        "00:00.000 --> 00:01.180\n"
        "Hello<00:00.500> world\n"
    )


def test_c15_ac2_smoothly_ducks_bgm_during_dialogue_and_restores_afterward():
    dialogue = [0.0] * 40 + [0.2] * 80 + [0.0] * 80

    envelope = build_ducking_envelope(dialogue, sample_rate=100)
    gain_at = {point["at"]: point["gain_db"] for point in envelope}

    assert gain_at[0.3] == 0.0
    assert -12.0 < gain_at[0.5] < 0.0
    assert gain_at[0.8] == -12.0
    assert -12.0 < gain_at[1.3] < 0.0
    assert gain_at[1.9] == 0.0
