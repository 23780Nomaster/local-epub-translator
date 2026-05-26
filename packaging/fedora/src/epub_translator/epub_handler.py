"""EPUB parsing, text extraction, and rebuilding with translated content."""
import os
import uuid
from ebooklib import epub
from bs4 import BeautifulSoup, NavigableString, Tag

TRANSLATABLE_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "td", "th", "figcaption", "dd", "dt", "summary"}
SKIP_TAGS = {"pre", "code", "style", "script", "svg", "math"}


def _fix_toc_uids(toc):
    for item in toc:
        if isinstance(item, epub.Link) and item.uid is None:
            item.uid = f"toc_{uuid.uuid4().hex[:8]}"
        if isinstance(item, tuple) and len(item) == 2:
            _fix_toc_uids(item[1])


class EpubBook:
    def __init__(self, path: str):
        self.path = os.path.expanduser(path)
        self.book = epub.read_epub(self.path)
        _fix_toc_uids(self.book.toc)
        self._original_soups = {}
        self._image_items = {
            item.file_name: item
            for item in self.book.get_items_of_type(1)  # ITEM_IMAGE = 1
        }

    SPINE_FILES = None

    def _get_spine_files(self):
        if self.SPINE_FILES is None:
            spine_ids = {item[0] for item in self.book.spine if item[0]}
            self.SPINE_FILES = set()
            for item in self.book.get_items():
                if item.id in spine_ids:
                    self.SPINE_FILES.add(item.file_name)
        return self.SPINE_FILES

    def _get_or_create_soup(self, item):
        """Return (item, soup) for a spine document, parsing and caching if needed."""
        if item.file_name not in self._original_soups:
            content = item.get_content()
            soup = BeautifulSoup(content, "xml")
            self._original_soups[item.file_name] = (item, soup)
        return self._original_soups[item.file_name]

    def _resolve_image(self, html_item, img_src: str):
        """Resolve an img src (relative to the HTML file) to the EPUB image item."""
        if img_src in self._image_items:
            return self._image_items[img_src]
        base_dir = os.path.dirname(html_item.file_name)
        resolved = os.path.normpath(os.path.join(base_dir, img_src))
        if resolved in self._image_items:
            return self._image_items[resolved]
        for name, item in self._image_items.items():
            if name.endswith(img_src) or img_src.endswith(name):
                return item
        return None

    def is_scanned(self) -> bool:
        """Heuristic: if the spine contains many images but very little text,
        the EPUB is likely a scanned document (page images with OCR-needed text)."""
        spine_files = self._get_spine_files()
        total_chars = 0
        image_count = 0

        for item in self.book.get_items_of_type(9):  # ITEM_DOCUMENT
            if not isinstance(item, epub.EpubHtml):
                continue
            if item.file_name not in spine_files:
                continue
            content = item.get_content()
            soup = BeautifulSoup(content, "xml")
            total_chars += len(soup.get_text(strip=True))
            image_count += len(soup.find_all("img"))

        return total_chars < 1000 and image_count >= 5

    def iter_text_blocks(self):
        """Yield (item, element, text, idx) for every translatable text block in spine content.
        idx is a zero-based counter scoped to each file, stable across runs on the same EPUB."""
        spine_files = self._get_spine_files()
        for item in self.book.get_items_of_type(9):  # ITEM_DOCUMENT = 9
            if not isinstance(item, epub.EpubHtml):
                continue
            if item.file_name not in spine_files:
                continue
            item, soup = self._get_or_create_soup(item)

            idx = 0
            for tag in soup.descendants:
                if not isinstance(tag, Tag):
                    continue
                if tag.name in SKIP_TAGS:
                    continue
                if tag.name not in TRANSLATABLE_TAGS:
                    continue
                if any(p.name in SKIP_TAGS for p in tag.parents if isinstance(p, Tag)):
                    continue
                if tag.find("a", href=True):
                    continue
                if tag.find("img") and not any(
                    isinstance(c, NavigableString) and c.strip()
                    for c in tag.children
                ):
                    continue
                text = tag.get_text(strip=True)
                if text:
                    yield (item, tag, text, idx)
                    idx += 1

    def iter_image_blocks(self):
        """Yield (item, tag, image_data, mime_type) for image-only blocks.
        Call after iter_text_blocks() so soups are already parsed and stored."""
        spine_files = self._get_spine_files()
        for file_name, (item, soup) in self._original_soups.items():
            if file_name not in spine_files:
                continue
            for tag in soup.descendants:
                if not isinstance(tag, Tag):
                    continue
                if tag.name in SKIP_TAGS:
                    continue
                if tag.name not in TRANSLATABLE_TAGS:
                    continue
                if any(p.name in SKIP_TAGS for p in tag.parents if isinstance(p, Tag)):
                    continue
                img = tag.find("img")
                if not img:
                    continue
                if any(
                    isinstance(c, NavigableString) and c.strip()
                    for c in tag.children
                ):
                    continue
                if img.get("src"):
                    image_item = self._resolve_image(item, img["src"])
                    if image_item:
                        mime = image_item.media_type or "image/png"
                        yield (item, tag, image_item.get_content(), mime)

    def iter_all_images(self, min_height: int = 0):
        """Yield (item, tag, image_data, mime_type, idx) for every <img> in spine content.
        idx is a zero-based counter scoped to each file, stable across runs on the same EPUB.
        Images shorter than min_height pixels are skipped (decorative bars, dividers)."""
        from PIL import Image
        import io

        spine_files = self._get_spine_files()
        for item in self.book.get_items_of_type(9):
            if not isinstance(item, epub.EpubHtml):
                continue
            if item.file_name not in spine_files:
                continue
            item, soup = self._get_or_create_soup(item)

            idx = 0
            for img_tag in soup.find_all("img"):
                src = img_tag.get("src")
                if not src:
                    continue
                image_item = self._resolve_image(item, src)
                if image_item:
                    if min_height > 0:
                        try:
                            with Image.open(io.BytesIO(image_item.get_content())) as pil_img:
                                if pil_img.height < min_height:
                                    continue
                        except Exception:
                            pass
                    mime = image_item.media_type or "image/png"
                    yield (item, img_tag, image_item.get_content(), mime, idx)
                    idx += 1

    def apply_translation(self, tag: Tag, translated: str):
        """Replace the text content of a tag with translated text."""
        tag.clear()
        tag.string = translated

    def apply_image_translation(self, tag: Tag, translated: str):
        """Append translated OCR text after <img>, preserving the image."""
        br = self.soup_for(tag).new_tag("br")
        tag.append(br)
        tag.append(translated)

    def soup_for(self, tag: Tag):
        """Return the BeautifulSoup object that owns the given tag."""
        current = tag
        while current.parent:
            current = current.parent
        return current

    def save(self, output_path: str):
        for file_name, (item, soup) in self._original_soups.items():
            item.set_content(str(soup).encode("utf-8"))

        output_path = os.path.expanduser(output_path)
        epub.write_epub(output_path, self.book)
