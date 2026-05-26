#!/bin/bash
# Build RPM package for epub-translator GNOME app.
# Prerequisites: sudo dnf install rpm-build
# Usage: ./build.sh [--install]

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
NAME="epub-translator"
VERSION=$(grep '^Version:' rpm/epub-translator.spec | awk '{print $2}')
RPMBUILD_DIR="$HOME/rpmbuild"

# Ensure rpmbuild is available
if ! command -v rpmbuild &>/dev/null; then
    echo "==> Installing rpm-build..."
    sudo dnf install -y rpm-build
fi

echo "==> Setting up rpmbuild directories..."
mkdir -p "$RPMBUILD_DIR"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

# Clean any __pycache__ from source
find "$PROJECT_DIR" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Create clean source tarball
echo "==> Creating source tarball..."
cd "$PROJECT_DIR"
tar czf "$RPMBUILD_DIR/SOURCES/${NAME}-${VERSION}.tar.gz" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='dist' \
    --exclude='.git' \
    --transform="s|^|${NAME}-${VERSION}/|" \
    src/ data/ requirements.txt

# Copy spec
cp "$PROJECT_DIR/rpm/epub-translator.spec" "$RPMBUILD_DIR/SPECS/"

echo "==> Building RPM..."
rpmbuild -bb "$RPMBUILD_DIR/SPECS/epub-translator.spec" \
    --define "_topdir $RPMBUILD_DIR"

# Copy to dist/
mkdir -p "$PROJECT_DIR/dist"
RPM_FILE=$(find "$RPMBUILD_DIR/RPMS" -name "${NAME}-${VERSION}*.rpm" | sort -V | tail -1)
cp "$RPM_FILE" "$PROJECT_DIR/dist/"
echo ""
echo "==> RPM built: $PROJECT_DIR/dist/$(basename "$RPM_FILE")"
echo ""

if [[ "${1:-}" == "--install" ]]; then
    echo "==> Installing..."
    sudo dnf install -y "$PROJECT_DIR/dist/$(basename "$RPM_FILE")"
    echo "==> Installed. You can now run: epub-translator-gnome"
fi

echo "==> Dependencies to install manually (not in Fedora repos):"
echo "    pip install ebooklib pytesseract"
echo ""
echo "==> You also need:"
echo "    1. llama.cpp (llama-server) at ~/.local/llama-cpp/"
echo "    2. A GGUF translation model at ~/models/"
echo "       curl -L -o ~/models/Hy-MT2-1.8B-Q8_0.gguf \\"
echo "         https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF/resolve/main/Hy-MT2-1.8B-Q8_0.gguf"
