"""Two page elements drawn over each other, and the text that comes back.

Measured on the Bon Appetit pie, page 2 (2026-08-06). pdfplumber orders a
line's characters by x, which is correct until two INDEPENDENT elements
share a baseline -- there, the page's subscription ad and the recipe's
final instruction arrive shredded into each other:

    and 1/4 tsp. salt just to combine. Usings uab lasrcgreip stipoono na,n
    ddo lgloept asi xge FnReEroEu gsi aftms!ount of whipped cream in the

`Using a large spoon, dollop a generous amount` woven through
`subscription and get six FREE gifts!`. Neither `dollop` nor `spoon`
survives as a word, so nothing downstream can recover them: the import
rendered that step as `Pipe a mound of whipped cream` -- a different
technique, needing equipment the recipe never mentions -- and that was the
model doing its best with rubble.

The PDFs here are BUILT, not checked in. A one-page PDF using two base-14
fonts needs no embedded font data and no binary fixture, and it lets the
non-interleaved control cases be written as easily as the failing one --
which matters more, since the risk in this change is a false positive on
a page that was fine.
"""

import io

import pdfplumber

from app.services.recipe_service import extract_pdf_text

# 5.0pt per character at 10pt type: wide enough that no glyph's advance
# swallows the next one, tight enough that pdfplumber infers no spurious
# word breaks. The overlay is offset by half a step, which is what puts
# one element's characters BETWEEN the other's.
_STEP = 5.0
_HALF = 2.5


def _pdf(spans) -> bytes:
    """spans: [(text, "F1"|"F2", start_x)] laid out on one shared baseline."""
    parts = ["BT"]
    for text, font, x0 in spans:
        for i, ch in enumerate(text):
            if ch != " ":
                parts.append(f"/{font} 10 Tf 1 0 0 1 {x0 + i * _STEP:.2f} 700 Tm ({ch}) Tj")
    parts.append("ET")
    content = "\n".join(parts).encode("latin-1")

    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Resources<</Font<</F1 5 0 R/F2 6 0 R>>>>/Contents 4 0 R>>",
        b"<</Length %d>>\nstream\n" % len(content) + content + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Times-Roman>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1) + b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def _plain(pdf_bytes: bytes) -> str:
    """What extract_pdf_text did before this change."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


RECIPE = "Using a large spoon dollop a generous amount"
AD = "subscription and get six FREE gifts"


def test_the_shredding_is_real_before_it_is_repaired():
    """The control. If pdfplumber ever stops interleaving these, the rest
    of this file is testing nothing and should say so out loud."""
    raw = _plain(_pdf([(RECIPE, "F1", 60.0), (AD, "F2", 60.0 + _HALF)]))
    assert RECIPE not in raw
    assert "spoon" not in raw
    assert "Ussuibnsg" in raw.replace(" ", "")


def test_two_elements_written_through_each_other_come_back_whole():
    text = extract_pdf_text(_pdf([(RECIPE, "F1", 60.0), (AD, "F2", 60.0 + _HALF)]))
    assert text.splitlines()[0] == RECIPE


def test_the_overlay_is_kept_rather_than_deleted():
    """It is page furniture and almost certainly noise. `Almost certainly`
    is not a licence to drop text this module never saw the whole of."""
    text = extract_pdf_text(_pdf([(RECIPE, "F1", 60.0), (AD, "F2", 60.0 + _HALF)]))
    assert text.splitlines()[1] == AD


def test_a_page_with_one_font_is_byte_identical_to_the_old_path():
    pdf = _pdf([("Preheat oven to 325 degrees and bake the crust", "F1", 60.0)])
    assert extract_pdf_text(pdf) == _plain(pdf)


def test_a_font_change_that_does_not_interleave_is_left_alone():
    """A bold word inside a sentence, or a table row whose cells are set
    differently, changes font a handful of times in CONTIGUOUS runs. That
    is not two elements, and the measured band between the two is wide:
    2-5 alternations for every legitimate multi-font line on the two real
    PDFs, against 37 and 49 for the overlaid ones."""
    head = "Filling and Assembly"
    tail = "whisk the egg yolks and milk together"
    pdf = _pdf([(head, "F2", 60.0), (tail, "F1", 60.0 + len(head) * _STEP + _STEP)])
    assert extract_pdf_text(pdf) == _plain(pdf)
    assert tail in extract_pdf_text(pdf)
