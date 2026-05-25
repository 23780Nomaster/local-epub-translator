"""OCR backend using Tesseract — local, no GPU, no API key."""
import io
from PIL import Image
import pytesseract


class OCRBackend:
    def __init__(self, lang: str = "chi_sim+eng"):
        self.lang = lang

    def ocr(self, image_data: bytes, mime_type: str = "image/png") -> str:
        img = Image.open(io.BytesIO(image_data))
        text = pytesseract.image_to_string(img, lang=self.lang)
        return text.strip()
