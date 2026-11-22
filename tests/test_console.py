"""Russian and Hebrew must survive a legacy Windows code page."""

from __future__ import annotations

import io

from shotforge.console import force_utf8

PROJECT_NAME = "Улыбка Вавилоны"


def _stream(encoding: str) -> tuple[io.BytesIO, io.TextIOWrapper]:
    raw = io.BytesIO()
    return raw, io.TextIOWrapper(raw, encoding=encoding)


def test_a_cp1252_console_would_crash_on_a_cyrillic_name():
    _, stream = _stream("cp1252")
    try:
        stream.write(PROJECT_NAME)
        stream.flush()
    except UnicodeEncodeError:
        return
    raise AssertionError("expected cp1252 to refuse Cyrillic; the fixture is wrong")


def test_force_utf8_makes_that_write_work():
    raw, stream = _stream("cp1252")
    assert force_utf8(stream) is True
    stream.write(PROJECT_NAME)
    stream.flush()
    assert raw.getvalue().decode("utf-8") == PROJECT_NAME


def test_hebrew_survives_too():
    raw, stream = _stream("cp1252")
    force_utf8(stream)
    stream.write("דימה מקשיב מהצד")
    stream.flush()
    assert raw.getvalue().decode("utf-8") == "דימה מקשיב מהצד"


def test_a_utf8_stream_is_left_alone():
    _, stream = _stream("utf-8")
    assert force_utf8(stream) is False


def test_a_stream_that_cannot_be_reconfigured_is_not_an_error():
    class Plain:
        encoding = "cp1252"

    assert force_utf8(Plain()) is False
