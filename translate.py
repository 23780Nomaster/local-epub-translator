#!/usr/bin/env python3
"""EPUB translator CLI — translate EPUB books using local Tesseract OCR and Hy-MT2.

Usage:
    translate-epub <input.epub> [--target-lang English] [--output output.epub]

Scanned EPUBs (image-heavy, little text) are auto-detected and processed
via local Tesseract OCR before translation.
"""
import argparse
import os
import sys
import signal
from tqdm import tqdm

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from epub_handler import EpubBook
from llm_backend import TranslationServer, Translator
from ocr_backend import OCRBackend
from checkpoint import CheckpointManager


def main():
    parser = argparse.ArgumentParser(description="Translate EPUB books using local Tesseract OCR and Hy-MT2")
    parser.add_argument("input", help="Path to input EPUB file")
    parser.add_argument("--target-lang", default="English", help="Target language (default: English)")
    parser.add_argument("--output", "-o", default=None, help="Output EPUB path (default: <input>.translated.epub)")
    parser.add_argument("--model-path", default="~/models/Hy-MT2-1.8B-Q8_0.gguf",
                        help="Path to GGUF model file")
    args = parser.parse_args()

    input_path = os.path.expanduser(args.input)
    if not os.path.exists(input_path):
        print(f"Error: file not found: {input_path}")
        sys.exit(1)

    model_path = os.path.expanduser(args.model_path)
    if not os.path.exists(model_path):
        print(f"Error: model not found: {model_path}")
        print("Download it first: curl -L -o ~/models/Hy-MT2-1.8B-Q8_0.gguf \\")
        print("  https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF/resolve/main/Hy-MT2-1.8B-Q8_0.gguf")
        sys.exit(1)

    if args.output:
        output_path = os.path.expanduser(args.output)
    else:
        base = os.path.splitext(os.path.basename(input_path))[0]
        output_path = os.path.join(os.path.dirname(input_path) or ".", f"{base}.translated.epub")

    # Load the book first to detect mode
    print(f"Loading EPUB: {input_path}")
    book = EpubBook(input_path)

    scanned = book.is_scanned()
    mode = "scanned" if scanned else "text"

    # Try to resume from checkpoint
    checkpoint = CheckpointManager.load(input_path, output_path, mode, args.target_lang)

    if checkpoint:
        n = len(checkpoint.completed)
        if n > 0:
            print(f"Resuming from checkpoint ({n} block(s) completed already).")
    else:
        checkpoint = CheckpointManager(input_path, output_path, mode, args.target_lang)

    if scanned:
        print("Detected: scanned document (image-heavy, little text). Will OCR all pages.")
    else:
        print("Detected: text document. Will translate text directly.")

    # Initialize backends
    ocr = OCRBackend() if scanned else None
    print(f"Starting translation server (model: {os.path.basename(model_path)})...")
    server = TranslationServer(model_path)
    translator = Translator(server, target_lang=args.target_lang)

    def cleanup():
        server.stop()

    def handle_interrupt(sig, frame):
        print("\nInterrupted. Saving checkpoint for resume...")
        checkpoint.save()
        server.stop()
        sys.exit(1)

    signal.signal(signal.SIGINT, handle_interrupt)
    signal.signal(signal.SIGTERM, handle_interrupt)

    try:
        server.start()

        if scanned:
            all_images = list(book.iter_all_images(min_height=200))
            total = len(all_images)
            completed_count = sum(1 for it, _, _, _, idx in all_images
                                  if checkpoint.is_completed(it.file_name, idx))
            pending = total - completed_count
            print(f"OCR + translating {total} page images ({pending} remaining)...")
            with tqdm(total=total, unit="page", desc="OCR+Translate", initial=completed_count) as pbar:
                for item, img_tag, image_data, mime_type, idx in all_images:
                    file_name = item.file_name
                    if checkpoint.is_completed(file_name, idx):
                        translated = checkpoint.get_translated(file_name, idx)
                        book.apply_image_translation(img_tag, translated)
                        pbar.update(1)
                        continue

                    try:
                        ocr_text = ocr.ocr(image_data, mime_type)
                        if ocr_text.strip():
                            translated = translator.translate(ocr_text)
                            book.apply_image_translation(img_tag, translated)
                            checkpoint.mark_completed(file_name, idx, translated)
                    except Exception as e:
                        pbar.write(f"Error on page (skipping): {e}")
                    pbar.update(1)
        else:
            blocks = list(book.iter_text_blocks())
            total = len(blocks)
            completed_count = sum(1 for it, _, _, idx in blocks
                                  if checkpoint.is_completed(it.file_name, idx))
            pending = total - completed_count
            print(f"Found {total} text blocks ({pending} remaining)")

            if total == 0:
                print("No translatable text found in EPUB.")
                sys.exit(0)

            print(f"Translating to {args.target_lang}...")
            with tqdm(total=total, unit="block", desc="Translating", initial=completed_count) as pbar:
                for item, tag, text, idx in blocks:
                    file_name = item.file_name
                    if checkpoint.is_completed(file_name, idx):
                        translated = checkpoint.get_translated(file_name, idx)
                        book.apply_translation(tag, translated)
                        pbar.update(1)
                        continue

                    for attempt in range(3):
                        try:
                            translated = translator.translate(text)
                            book.apply_translation(tag, translated)
                            checkpoint.mark_completed(file_name, idx, translated)
                            break
                        except Exception as e:
                            if attempt < 2 and ("Connection" in str(e) or "Remote" in str(e)):
                                pbar.write("Server connection lost, restarting...")
                                server.stop()
                                server = TranslationServer(model_path)
                                server.start()
                                translator.server = server
                            elif attempt == 2:
                                pbar.write(f"Error translating block (skipping): {e}")
                            else:
                                raise
                    pbar.update(1)

        print(f"Saving to: {output_path}")
        book.save(output_path)
        checkpoint.delete()
        print("Done.")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
