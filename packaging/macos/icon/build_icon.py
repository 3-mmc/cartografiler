"""Convert the supplied iDisk artwork to Cartografiler's ICNS container.

This conversion is licensed under the Design Science License; see LICENSE.txt.
"""
from pathlib import Path
from PIL import Image

root = Path(__file__).resolve().parent
image = Image.open(root/'iDisk.png').convert('RGBA')
image.resize((1024, 1024), Image.Resampling.LANCZOS).save(root/'Cartografiler.icns', format='ICNS')
