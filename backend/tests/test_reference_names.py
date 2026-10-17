from app.reference_names import parse_reference_name


def test_parses_chinese_bed_length_with_punctuation():
    assert parse_reference_name("（床 长度）") == ("bed", "length")
    assert parse_reference_name("床长") == ("bed", "length")


def test_parses_door_alias_and_english_name():
    assert parse_reference_name("门宽度") == ("door", "width")
    assert parse_reference_name("Bed Length") == ("bed", "length")


def test_rejects_unknown_reference_name():
    assert parse_reference_name("电视柜对角线") is None
