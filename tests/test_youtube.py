"""
Tests for YouTube URL parsing utility.
"""

from app.utils.youtube import extract_video_id


def test_standard_url():
    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_standard_url_no_www():
    assert extract_video_id("https://youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_short_url():
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_url_with_extra_params():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf&t=120"
    assert extract_video_id(url) == "dQw4w9WgXcQ"


def test_embed_url():
    assert extract_video_id("https://youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_v_url():
    assert extract_video_id("https://youtube.com/v/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_shorts_url():
    assert extract_video_id("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_mobile_url():
    assert extract_video_id("https://m.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_raw_video_id():
    assert extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_invalid_url():
    assert extract_video_id("not-a-url") is None


def test_empty_string():
    assert extract_video_id("") is None


def test_random_website():
    assert extract_video_id("https://google.com/search?q=hello") is None


def test_url_with_spaces():
    assert extract_video_id("  https://youtu.be/dQw4w9WgXcQ  ") == "dQw4w9WgXcQ"


def test_hyphenated_video_id():
    assert extract_video_id("https://www.youtube.com/watch?v=_g89_8Bb57g") == "_g89_8Bb57g"
