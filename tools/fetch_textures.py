"""Download the CC0 Poly Haven textures the terrain shader uses and bake them into detail maps.

Each material becomes one 512x512 PNG: RG = tangent-space normal (x, y), B = luminance detail
(high-passed, mean 0.5), A = 255. The shader tints the synthesised ground colour with B and
lights it with RG, so the textures add grain and relief without changing the map's colours.

Poly Haven assets are CC0 (https://polyhaven.com/license). Run again to rebuild:
    python3 tools/fetch_textures.py
"""
import io
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

# Layer order is the shader's material order.
MATERIALS = [
    ('meadow', 'rocky_terrain_02'),
    ('forest', 'forest_leaves_02'),
    ('field', 'farm_furrows'),
    ('town', 'aerial_asphalt_01'),
    ('wet', 'brown_mud_leaves_01'),
    ('snow', 'snow_field_aerial'),
    ('rock', 'aerial_rocks_02'),
    ('sand', 'aerial_beach_01'),
]
OUT = Path(__file__).resolve().parent.parent/'native'/'textures'
SIZE = 512


def get(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Cartografiler'}), timeout=60) as r:
        return r.read()


def fetch(url: str) -> Image.Image:
    return Image.open(io.BytesIO(get(url)))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/'.gdignore').touch()   # loaded at runtime from disk; no Godot import step
    credits = []
    for name, asset in MATERIALS:
        files = json.loads(get(f'https://api.polyhaven.com/files/{asset}'))
        diff = fetch(files['Diffuse']['1k']['jpg']['url']).convert('RGB').resize((SIZE, SIZE), Image.LANCZOS)
        nor = fetch(files['nor_gl']['1k']['jpg']['url']).convert('RGB').resize((SIZE, SIZE), Image.LANCZOS)
        lum = np.asarray(diff.convert('L'), dtype=np.float64)
        # High-pass: remove blotches wider than 1/6 of the texture, so repeats don't show.
        low = np.asarray(Image.fromarray(lum.astype(np.uint8)).filter(ImageFilter.GaussianBlur(SIZE/12)), dtype=np.float64)
        detail = lum-low
        detail = 0.5+detail/(4*detail.std()+1e-9)
        n = np.asarray(nor, dtype=np.float64)
        out = np.zeros((SIZE, SIZE, 4), dtype=np.uint8)
        out[..., 0] = n[..., 0]
        out[..., 1] = n[..., 1]
        out[..., 2] = np.clip(detail*255, 0, 255)
        out[..., 3] = 255
        Image.fromarray(out, 'RGBA').save(OUT/f'{name}.png', optimize=True)
        credits.append(f'- `{name}.png`: "{asset}" by Poly Haven, https://polyhaven.com/a/{asset} (CC0)')
        print(name, asset, file=sys.stderr)
    (OUT/'LICENSE.md').write_text('# Terrain detail textures\n\nDerived from CC0 textures (public domain, https://polyhaven.com/license), '
                                  'baked by `tools/fetch_textures.py` into normal + luminance-detail maps.\n\n'+'\n'.join(credits)+'\n')


if __name__ == '__main__':
    main()
