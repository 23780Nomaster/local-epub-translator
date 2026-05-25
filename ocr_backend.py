"""OCR backend using Tesseract — local, no GPU, no API key."""
import io
import os
import sys
from PIL import Image
import pytesseract

_EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))

# Bundled tesseract layout:
#   tesseract/
#     tesseract.exe
#     tessdata/
#       chi_sim.traineddata
#       eng.traineddata


def _find_tesseract() -> str | None:
    """Find tesseract.exe bundled alongside the exe."""
    candidates = [
        os.path.join(_EXE_DIR, "tesseract", "tesseract.exe"),
        os.path.join(_EXE_DIR, "tesseract.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _find_tessdata() -> str | None:
    """Find tessdata directory bundled alongside the exe."""
    candidates = [
        os.path.join(_EXE_DIR, "tesseract", "tessdata"),
        os.path.join(_EXE_DIR, "tessdata"),
    ]
    for c in candidates:
        if os.path.isdir(c) and os.path.isfile(os.path.join(c, "eng.traineddata")):
            return c
    return None


class OCRBackend:
    def __init__(self, lang: str = "chi_sim+eng"):
        self.lang = lang
        tesseract_bin = _find_tesseract()
        if tesseract_bin:
            pytesseract.pytesseract.tesseract_cmd = tesseract_bin
            tessdata_dir = _find_tessdata()
            if tessdata_dir:
                os.environ["TESSDATA_PREFIX"] = tessdata_dir

    def ocr(self, image_data: bytes, mime_type: str = "image/png") -> str:
        img = Image.open(io.BytesIO(image_data))
        text = pytesseract.image_to_string(img, lang=self.lang)
        return text.strip()
