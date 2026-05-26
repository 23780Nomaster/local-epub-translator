# EPUB Translator — GNOME Desktop Application

A native GNOME desktop application for offline EPUB translation using local AI models.

## Features

- **Native GNOME interface** — GTK4 + Libadwaita, follows GNOME HIG
- **File browser** — native file chooser dialog for EPUB selection
- **Auto-detection** — distinguishes text-based vs scanned/image-based EPUBs
- **Checkpoint/resume** — stop anytime, continue later without losing progress
- **Hardware monitoring** — real-time CPU and RAM usage display
- **ETA estimation** — estimated remaining time based on translation speed
- **Multiple languages** — Simplified Chinese, English, Japanese, Traditional Chinese

## Requirements

### System (install via dnf)
```
python3 >= 3.10
python3-gobject
gtk4
libadwaita
tesseract
python3-beautifulsoup4
python3-pillow
python3-requests
python3-tqdm
python3-flask
python3-psutil
```

### PyPI (install via pip)
```
pip install ebooklib pytesseract
```

### Translation backend
- [llama.cpp](https://github.com/ggerganov/llama.cpp) — install `llama-server` to `~/.local/llama-cpp/`
- [Hy-MT2-1.8B GGUF](https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF) — download to `~/models/`

## Install

### From RPM (Fedora)
```bash
git clone https://github.com/23780Nomaster/local-epub-translator.git
cd local-epub-translator/packaging/gnome
./build.sh --install
```

### Manual install
```bash
# Copy Python package
sudo mkdir -p /usr/lib/epub-translator
sudo cp -a src/epub_translator /usr/lib/epub-translator/

# Install wrapper script
sudo install -m 755 data/epub-translator-gnome /usr/bin/

# Install desktop integration
sudo install -m 644 data/com.voyager.epubtranslator.desktop /usr/share/applications/
sudo install -m 644 data/com.voyager.epubtranslator.metainfo.xml /usr/share/metainfo/
sudo install -m 644 data/icons/com.voyager.epubtranslator.svg /usr/share/icons/hicolor/scalable/apps/
```

### Run from source
```bash
python3 src/epub_translator/gnome_app.py
```

## Build RPM

```bash
./build.sh           # Build RPM only
./build.sh --install # Build and install
```

The RPM will be placed in `~/rpmbuild/RPMS/noarch/`.

## Project structure

```
epub-translator-gnome/
├── src/epub_translator/    # Python package
│   ├── __init__.py
│   ├── gnome_app.py        # GTK4 + Libadwaita application
│   ├── epub_handler.py     # EPUB parsing
│   ├── llm_backend.py      # llama-server management
│   ├── ocr_backend.py      # Tesseract OCR
│   ├── checkpoint.py       # Resume support
│   └── translate.py        # CLI entry point
├── data/                   # Desktop integration
│   ├── com.voyager.epubtranslator.desktop
│   ├── com.voyager.epubtranslator.metainfo.xml
│   ├── epub-translator-gnome   # Executable wrapper
│   └── icons/
├── rpm/
│   └── epub-translator.spec    # RPM spec file
├── build.sh                # Build script
├── requirements.txt        # Python dependencies
└── README.md
```

## License

MIT
