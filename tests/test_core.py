import pytest
from core import parse_video_id, fmt_timestamp, to_plain_text, to_timestamped_text, Segment

VID = "dQw4w9WgXcQ"

@pytest.mark.parametrize("url", [
    f"https://www.youtube.com/watch?v={VID}",
    f"http://youtube.com/watch?v={VID}",
    f"https://m.youtube.com/watch?v={VID}",
    f"https://youtu.be/{VID}",
    f"https://youtu.be/{VID}?t=42",
    f"https://www.youtube.com/shorts/{VID}",
    f"https://www.youtube.com/embed/{VID}",
    f"https://www.youtube.com/live/{VID}",
    f"https://www.youtube.com/v/{VID}",
    f"https://www.youtube.com/watch?app=desktop&v={VID}&list=PLabc&index=3",
    f"https://www.youtube-nocookie.com/embed/{VID}",
    f"  https://youtu.be/{VID}  ",
    VID,
    f"https://www.youtube.com/watch?v={VID}&t=1h2m3s",
])
def test_parse_video_id_accepts(url):
    assert parse_video_id(url) == VID

@pytest.mark.parametrize("bad", [
    "", "   ", "not a url", "https://vimeo.com/12345",
    "https://www.youtube.com/watch?v=short", "https://example.com/watch?v=" + VID,
    "https://www.youtube.com/", "abc", "https://youtu.be/",
])
def test_parse_video_id_rejects(bad):
    with pytest.raises(ValueError):
        parse_video_id(bad)

@pytest.mark.parametrize("secs,expected", [
    (0, "00:00"), (5, "00:05"), (61, "01:01"), (599, "09:59"),
    (600, "10:00"), (3599, "59:59"), (3600, "1:00:00"),
    (3661, "1:01:01"), (7325.9, "2:02:05"), (12.34, "00:12"),
])
def test_fmt_timestamp(secs, expected):
    assert fmt_timestamp(secs) == expected

SEGS = [
    Segment(text="First line", start=0.0, duration=3.0),
    Segment(text="second   line\nwith break", start=12.5, duration=2.0),
    Segment(text="third", start=3700.0, duration=1.0),
]

def test_plain_text_joins_and_normalises_whitespace():
    out = to_plain_text(SEGS)
    assert out == "First line second line with break third"

def test_plain_text_empty():
    assert to_plain_text([]) == ""

def test_timestamped_text_has_one_line_per_segment():
    lines = to_timestamped_text(SEGS).splitlines()
    assert lines == [
        "[00:00] First line",
        "[00:12] second line with break",
        "[1:01:40] third",
    ]
