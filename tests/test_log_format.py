from pathlib import Path
import sys
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import flet as ft
from app.gui.log_format import html_to_spans, COLOR_MAP


def test_plain_text_becomes_single_span():
    spans = html_to_spans("hello world")
    assert len(spans) == 1
    assert spans[0].text == "hello world"
    assert spans[0].style is None


def test_red_font_tag():
    spans = html_to_spans("<font color='red'>error</font>")
    assert len(spans) == 1
    assert spans[0].text == "error"
    assert spans[0].style.color == COLOR_MAP["red"]


def test_green_font_tag():
    spans = html_to_spans("<font color='green'>ok</font>")
    assert spans[0].style.color == COLOR_MAP["green"]


def test_gray_font_tag():
    spans = html_to_spans("<font color='gray'>info</font>")
    assert spans[0].style.color == COLOR_MAP["gray"]


def test_p_wrapper_stripped():
    spans = html_to_spans("<p><font color='red'>msg</font></p>")
    assert len(spans) == 1
    assert spans[0].text == "msg"


def test_mixed_content():
    spans = html_to_spans("prefix <font color='red'>error</font> suffix")
    texts = [s.text for s in spans]
    assert "prefix " in texts
    assert "error" in texts
    assert " suffix" in texts


def test_unknown_color_falls_back_to_default():
    spans = html_to_spans("<font color='purple'>text</font>")
    assert spans[0].style is None


def test_empty_string():
    spans = html_to_spans("")
    assert spans == []


def test_double_quote_attribute():
    spans = html_to_spans('<font color="green">ok</font>')
    assert spans[0].style.color == COLOR_MAP["green"]


def test_hex_color_passed_through():
    spans = html_to_spans("<font color='#ff8800'>hex</font>")
    assert spans[0].text == "hex"
    assert spans[0].style.color == "#ff8800"


def test_hex_color_uppercase_lowercased():
    spans = html_to_spans("<font color='#ABCDEF'>x</font>")
    assert spans[0].style.color == "#abcdef"
