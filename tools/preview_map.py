"""Render a hillshaded map image straight from the tile synthesiser (development aid).

    python3 tools/preview_map.py OUT.png --index DB [--path /home/praetor] [--tiles 3] [--zoom 1.0]
"""
import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from branchfm.index import Index
from branchfm.tiles import N, Synth
from branchfm.world import World
from branchfm.service import filesystem


def zone_for(path):
    fs = filesystem(Path(path)) if Path(path).exists() else {'zone': 'native', 'writable': True}
    return fs['zone'] if fs.get('writable', True) else 'alpine'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--index', default=None, help='default: the real survey index')
    ap.add_argument('--path', default='/')
    ap.add_argument('--tiles', type=int, default=3)
    ap.add_argument('--zoom', type=float, default=1.0, help='>1 zooms into the place')
    a = ap.parse_args()
    from branchfm.atlas_api import Atlas
    atlas = Atlas('/', index=Index(a.index) if a.index else None, survey=False)
    world, synth = atlas.world, atlas.synth
    if a.path == '/':
        cx, cy, span = 0.5, 0.5, 1.0
        level_hint = None
    else:
        t = world.territory_for(a.path)
        parent = world.territory_for(str(Path(a.path).parent))
        entry = next(c for c in parent.children if c['path'] == a.path)
        bx0, by0, bx1, by1 = entry['bbox']
        cx, cy, span = (bx0+bx1)/2, (by0+by1)/2, max(bx1-bx0, by1-by0)*1.15
    span /= a.zoom
    level = max(0, int(math.floor(math.log2(a.tiles/span))))
    S = 0.5**level
    tx0 = int(math.floor((cx-span/2)/S)); ty0 = int(math.floor((cy-span/2)/S))
    tx1 = int(math.floor((cx+span/2)/S)); ty1 = int(math.floor((cy+span/2)/S))
    W, Hh = (tx1-tx0+1)*(N-1)+1, (ty1-ty0+1)*(N-1)+1
    height = np.zeros((Hh, W)); colour = np.zeros((Hh, W, 4))
    t0 = time.time()
    for ty in range(ty0, ty1+1):
        for tx in range(tx0, tx1+1):
            tile = synth.tile(level, tx, ty)
            oy, ox = (ty-ty0)*(N-1), (tx-tx0)*(N-1)
            height[oy:oy+N, ox:ox+N] = tile['height'] + tile['base']
            colour[oy:oy+N, ox:ox+N] = tile['colour']
    n_tiles = (tx1-tx0+1)*(ty1-ty0+1)
    print(f'level {level}, {n_tiles} tiles in {time.time()-t0:.1f}s ({(time.time()-t0)/n_tiles*1000:.0f} ms/tile)')
    px = S/(N-1)
    gy, gx = np.gradient(height, px)
    exag = 6.0
    nx, ny, nz = -gx*exag, -gy*exag, np.ones_like(gx)
    norm = np.sqrt(nx*nx+ny*ny+nz*nz)
    light = np.array([-0.55, -0.45, 0.70]); light /= np.linalg.norm(light)
    shade = np.clip((nx*light[0]+ny*light[1]+nz*light[2])/norm, 0, 1)
    water = colour[..., 3:4]/255
    lit = colour[..., :3]*(0.35+0.85*shade[..., None])*(1-water) + colour[..., :3]*(0.9+0.2*shade[..., None])*water
    Image.fromarray(np.clip(lit, 0, 255).astype(np.uint8)).save(a.out)
    print(a.out, lit.shape)


if __name__ == '__main__':
    main()
