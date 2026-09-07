"""
A hostile filename cannot break a download, and an ordinary one is unchanged.

Four download routes used to interpolate a caller-controlled name straight into
`Content-Disposition`. Verified against a REAL uvicorn (not a TestClient, which
does not run h11's header validation), the two failure modes were:

* `ev"il` — uvicorn sends `filename="ev"il.zip"` verbatim; the embedded quote
  closes the quoted-string early and a conforming parser reads `ev`.
* a name containing a newline — uvicorn raises
  `RuntimeError: Invalid HTTP header value.` mid-send and the client gets an
  empty reply. The download is broken outright.

Neither is response splitting, and this file's job is to keep it that way: the
first two tests below are the property that matters — whatever the name, the
header is a single line with no control characters and exactly one quoted
string.

`test_a_plain_name_is_byte_identical_to_the_old_format` is the other half. The
helper is applied to every download route, so if it reshaped ordinary headers
this change would be a behaviour change for every user rather than a fix for a
hostile edge case.
"""
from __future__ import annotations

import pytest

from services.http_filenames import content_disposition

# Names the product accepts today. Component names are created through
# `POST /api/network/loads` and friends, which apply no character validation;
# project names go through rename, which validates only non-empty/unique.
HOSTILE = [
    'ev"il',
    "a\nb",
    "a\rb",
    "a\r\nSet-Cookie: x=1",
    "with\x00null",
    "tab\there",
    "../escape",
    "..\\escape",
    "back\\slash",
    "del\x7f",
    "..",
    ".",
    "...",
    "",
    "   ",
    "\x01\x02\x03",
    "Grüße.xlsx",
    "日本語.xlsx",
    "emoji🙂.zip",
    "a" * 500,
]


@pytest.mark.parametrize("name", HOSTILE)
def test_the_header_is_always_one_line_with_no_control_characters(name):
    """
    The property that makes the header safe to send at all. A control character
    here is what makes uvicorn raise and the download return an empty reply.
    """
    header = content_disposition(name)
    assert "\n" not in header and "\r" not in header, repr(header)
    assert not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in header), repr(header)


@pytest.mark.parametrize("name", HOSTILE)
def test_the_quoted_filename_is_exactly_one_quoted_string(name):
    """
    An embedded quote used to close the string early, so a parser read
    `filename="ev"il.zip"` as `ev`. The quoted part must contain no quote of
    its own — which, with the parameters this helper emits, means the header
    has exactly two `"` characters in total.
    """
    header = content_disposition(name)
    assert header.count('"') == 2, repr(header)
    quoted = header.split('"')[1]
    assert '"' not in quoted and "\\" not in quoted, repr(header)


@pytest.mark.parametrize(
    "name",
    ["report.xlsx", "load_profiles_template.xlsx", "My Project.pypsaproj.zip",
     "generator_G1_p_max_pu_template.xlsx", "a-b_c.1.csv"],
)
def test_a_plain_name_is_byte_identical_to_the_old_format(name):
    """
    The helper is applied to every download route, so an ordinary name must
    come out EXACTLY as the old f-string produced it — no `filename*`, same
    quoting. Otherwise this stops being a fix for a hostile edge case and
    becomes a behaviour change for every download in the product.
    """
    assert content_disposition(name) == f'attachment; filename="{name}"'


def test_the_real_name_survives_in_filename_star():
    """
    The fallback is lossy on purpose; `filename*` is where the true name lives,
    and every browser since IE9 prefers it. Losing the name entirely would be a
    worse bug than the one being fixed.
    """
    header = content_disposition("Grüße 100%.xlsx")
    assert "filename*=UTF-8''" in header
    encoded = header.split("filename*=UTF-8''", 1)[1]
    import urllib.parse

    assert urllib.parse.unquote(encoded, encoding="utf-8") == "Grüße 100%.xlsx"


def test_a_name_that_scrubs_away_still_produces_a_usable_filename():
    """`filename=""` is worse than a generic name — browsers fall back to the
    URL's last path segment, which for these routes is `template` or `bundle`."""
    for name in ("", "   ", "...", "\x01\x02"):
        header = content_disposition(name)
        assert header.split('"')[1], f"empty filename for {name!r}: {header!r}"


def test_inline_disposition_is_supported():
    """`routers/uploads.py` previews blobs in-tab rather than downloading."""
    assert content_disposition("x.png", disposition="inline").startswith("inline; ")
