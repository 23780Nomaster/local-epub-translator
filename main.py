#!/usr/bin/env python3
"""EPUB Translator — desktop entry point.

Launches the tkinter window application.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app_window import main

if __name__ == "__main__":
    main()
