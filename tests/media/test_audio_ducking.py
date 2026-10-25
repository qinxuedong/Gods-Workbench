import pytest
from gw.media.audio_ducking import build_ducking_envelope
from gw.core.errors import CleanroomException

def test_build_ducking_envelope_normal():
    # rate 1000, frame 10 (10ms)
    # attack step = 12 * 10 / (1000 * 0.2) = 120 / 200 = 0.6
    # release step = 12 * 10 / (1000 * 0.3) = 120 / 300 = 0.4
    samples = [0.02] * 10 + [0.0] * 10
    env = build_ducking_envelope(samples, 1000)
    assert len(env) == 2
    assert env[0]["at"] == 0.0
    assert env[0]["gain_db"] == -0.6
    assert env[1]["at"] == 0.01
    assert env[1]["gain_db"] == -0.2

def test_build_ducking_envelope_edge_cases():
    with pytest.raises(CleanroomException) as exc:
        build_ducking_envelope([], 1000)
    assert exc.value.status_code == 400
    assert exc.value.code == "EMPTY_AUDIO_SAMPLES"

    with pytest.raises(CleanroomException) as exc:
        build_ducking_envelope([0.0], -1)
    assert exc.value.code == "INVALID_SAMPLE_RATE"

    with pytest.raises(CleanroomException) as exc:
        build_ducking_envelope([0.0], 44100.5)
    assert exc.value.code == "INVALID_SAMPLE_RATE"

    # Overlapping ranges/generator
    def sample_gen():
        yield 0.5
        yield 0.5

    env = build_ducking_envelope(sample_gen(), 100)
    # rate 100, frame_size = max(1, int(round(100 * 0.01))) = 1
    # attack = 12 * 1 / (100 * 0.2) = 0.6
    assert len(env) == 2
    assert env[0]["gain_db"] == -0.6
    assert env[1]["gain_db"] == -1.2

def test_build_ducking_envelope_invalid_samples():
    with pytest.raises(CleanroomException) as exc:
        build_ducking_envelope(None, 44100)
    assert exc.value.code == "INVALID_AUDIO_SAMPLES"

    with pytest.raises(CleanroomException) as exc:
        build_ducking_envelope([True], 44100)
    assert exc.value.code == "INVALID_AUDIO_SAMPLES"

    with pytest.raises(CleanroomException) as exc:
        build_ducking_envelope([2.0], 44100)
    assert exc.value.code == "INVALID_AUDIO_SAMPLES"

    with pytest.raises(CleanroomException) as exc:
        build_ducking_envelope([-1.5], 44100)
    assert exc.value.code == "INVALID_AUDIO_SAMPLES"

    with pytest.raises(CleanroomException) as exc:
        build_ducking_envelope([float("inf")], 44100)
    assert exc.value.code == "INVALID_AUDIO_SAMPLES"
