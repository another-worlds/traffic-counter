"""Region input validation — SVG drag coords arrive as floats."""
from app.schemas import TimestampRegionIn


def test_region_in_coerces_float_coordinates():
    region = TimestampRegionIn(
        x=1968.4,
        y=1314.7,
        w=592.1,
        h=126.9,
        video_width=2560.0,
        video_height=1440.0,
    )
    assert region.x == 1968
    assert region.y == 1315
    assert region.w == 592
    assert region.h == 127
    assert region.video_width == 2560
    assert region.video_height == 1440