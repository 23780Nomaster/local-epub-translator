Name:           epub-translator
Version:        0.2.0
Release:        1%{?dist}
Summary:        Local offline EPUB translator with native GNOME interface
License:        MIT
URL:            https://github.com/23780Nomaster/local-epub-translator
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

Requires:       python3 >= 3.10
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       tesseract
Requires:       python3dist(beautifulsoup4)
Requires:       python3dist(pillow)
Requires:       python3dist(requests)
Requires:       python3dist(tqdm)
Requires:       python3dist(flask)
Requires:       python3dist(psutil)

# Tesseract OCR language data — installed via dnf automatically
Requires:       tesseract-langpack-chi_sim
Requires:       tesseract-langpack-eng
Recommends:     tesseract-langpack-jpn
Recommends:     tesseract-langpack-chi_tra

# PyPI-only deps (not in Fedora repos)
# Install manually: pip install ebooklib pytesseract

%description
A fully offline EPUB translator that runs entirely on your local machine.
Supports both text-based EPUBs (direct translation) and scanned/image-based
EPUBs (OCR + translation) using Tesseract and local LLM models.

Integrated components:
 - Tesseract OCR with chi_sim + eng language data (installed automatically)
 - Setup script for one-command model + llama.cpp download

Features:
 - Native GNOME desktop interface (GTK4 + Libadwaita)
 - Direct text translation and OCR-based image translation
 - Checkpoint/resume: stop anytime and continue later without losing progress
 - Real-time hardware monitoring (CPU, RAM usage)
 - Supports Simplified Chinese, English, Japanese, Traditional Chinese

After install, run:  epub-translator-setup
This downloads llama-server and the Hy-MT2 translation model automatically.

PyPI dependencies (not in Fedora repos):
  pip install ebooklib pytesseract


%prep
rm -rf %{_builddir}/%{name}-%{version}
tar xzf %{_sourcedir}/%{name}-%{version}.tar.gz -C %{_builddir}


%install
cd %{_builddir}/%{name}-%{version}

# Python package
mkdir -p %{buildroot}/usr/lib/epub-translator/epub_translator
cp -a src/epub_translator/*.py %{buildroot}/usr/lib/epub-translator/epub_translator/

# Wrapper script
mkdir -p %{buildroot}%{_bindir}
install -m 755 data/epub-translator-gnome %{buildroot}%{_bindir}/epub-translator-gnome

# Setup script
install -m 755 data/epub-translator-setup %{buildroot}%{_bindir}/epub-translator-setup

# Model download script
install -m 755 data/epub-translator-download-model %{buildroot}%{_bindir}/epub-translator-download-model

# Desktop entry
mkdir -p %{buildroot}%{_datadir}/applications
install -m 644 data/com.voyager.epubtranslator.desktop \
    %{buildroot}%{_datadir}/applications/

# AppStream metainfo
mkdir -p %{buildroot}%{_datadir}/metainfo
install -m 644 data/com.voyager.epubtranslator.metainfo.xml \
    %{buildroot}%{_datadir}/metainfo/

# Icon
mkdir -p %{buildroot}%{_datadir}/icons/hicolor/scalable/apps
install -m 644 data/icons/com.voyager.epubtranslator.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/

# Documentation
mkdir -p %{buildroot}%{_docdir}/epub-translator
install -m 644 requirements.txt %{buildroot}%{_docdir}/epub-translator/


%check
python3 -c "
import py_compile, os, sys
ok = True
for f in ['epub_handler.py', 'llm_backend.py', 'ocr_backend.py',
          'checkpoint.py', 'translate.py', 'gnome_app.py']:
    path = os.path.join('%{buildroot}', 'usr/lib/epub-translator/epub_translator', f)
    try:
        py_compile.compile(path, doraise=True)
        print(f'  OK  {f}')
    except py_compile.PyCompileError as e:
        print(f'  FAIL {f}: {e}')
        ok = False
sys.exit(0 if ok else 1)
"
rm -rf %{buildroot}/usr/lib/epub-translator/epub_translator/__pycache__


%post
echo ""
echo "============================================"
echo "  EPUB Translator installed!"
echo "============================================"
echo ""
echo "  Next step — run the setup script to download"
echo "  the translation model and llama-server:"
echo ""
echo "    epub-translator-setup"
echo ""
echo "  Or download the model only:"
echo "    epub-translator-download-model"
echo ""
echo "  After setup, launch:  epub-translator-gnome"
echo "============================================"
echo ""


%files
%{_bindir}/epub-translator-gnome
%{_bindir}/epub-translator-setup
%{_bindir}/epub-translator-download-model
%dir /usr/lib/epub-translator
%dir /usr/lib/epub-translator/epub_translator
/usr/lib/epub-translator/epub_translator/__init__.py
/usr/lib/epub-translator/epub_translator/checkpoint.py
/usr/lib/epub-translator/epub_translator/epub_handler.py
/usr/lib/epub-translator/epub_translator/gnome_app.py
/usr/lib/epub-translator/epub_translator/llm_backend.py
/usr/lib/epub-translator/epub_translator/ocr_backend.py
/usr/lib/epub-translator/epub_translator/translate.py
%{_datadir}/applications/com.voyager.epubtranslator.desktop
%{_datadir}/metainfo/com.voyager.epubtranslator.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/com.voyager.epubtranslator.svg
%dir %{_docdir}/epub-translator
%{_docdir}/epub-translator/requirements.txt


%changelog
* Mon May 25 2026 Voyager <voyager@example.com> - 0.2.0-1
- Initial GNOME desktop application release
- Integrated Tesseract OCR langpacks (chi_sim, eng)
- Built-in setup script for model + llama-server download
- GTK4 + Libadwaita native interface
- RPM packaging for Fedora
