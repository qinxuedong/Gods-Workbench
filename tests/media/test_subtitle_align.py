import pytest
from gw.core.errors import CleanroomException
from gw.media.subtitle_align import generate_webvtt

def test_generate_webvtt_header_and_styling():
    words = [
        {"word": "Hello", "start": 0.0, "end": 0.5},
        {"word": "World", "start": 0.5, "end": 1.0}
    ]
    result = generate_webvtt(words)
    assert result.startswith("WEBVTT\n\nSTYLE\n::cue(:past) { color: yellow; }\n::cue(:future) { color: white; }\n\n")
    assert "00:00.000 --> 00:01.000\nHello<00:00.500> World\n" in result

def test_generate_webvtt_formatting_ms_rounding():
    words = [
        {"word": "A", "start": 0.0004, "end": 0.5006}, # start: 0, end: 501
    ]
    result = generate_webvtt(words)
    assert "00:00.000 --> 00:00.501\nA\n" in result

    words2 = [
        {"word": "A", "start": 0.0, "end": 0.5},
        {"word": "B", "start": 3600.0, "end": 3601.5}
    ]
    result2 = generate_webvtt(words2)
    assert "00:00.000 --> 01:00:01.500\n" in result2
    assert "A<01:00:00.000> B\n" in result2

def test_generate_webvtt_html_escaping():
    words = [
        {"word": "<html>", "start": 0.0, "end": 0.5},
        {"word": "&", "start": 0.5, "end": 1.0}
    ]
    result = generate_webvtt(words)
    assert "&lt;html&gt;<00:00.500> &amp;\n" in result

def test_generate_webvtt_invalid_input_raises_exception():
    with pytest.raises(CleanroomException) as exc:
        generate_webvtt([{"word": "A", "start": 0.0, "end": 0.5}], word_separator=" \n ")
    assert exc.value.code == "INVALID_SUBTITLE_SEPARATOR"

    with pytest.raises(CleanroomException) as exc:
        generate_webvtt([{"word": "A\nB", "start": 0.0, "end": 0.5}])
    assert exc.value.code == "INVALID_SUBTITLE_WORD"

    with pytest.raises(CleanroomException) as exc:
        generate_webvtt([{"word": "A", "start": 0.0, "end": 0.0001}]) # duration < 1ms rounds to 0 length cue
    assert exc.value.code == "INVALID_WEBVTT_TIMESTAMPS"

    with pytest.raises(CleanroomException) as exc:
        generate_webvtt([
            {"word": "A", "start": 0.0, "end": 0.0001},
            {"word": "B", "start": 0.0002, "end": 1.0} # start rounds to same ms as previous, meaning it's not strictly increasing in ms
        ])
    assert exc.value.code == "INVALID_WEBVTT_TIMESTAMPS"
