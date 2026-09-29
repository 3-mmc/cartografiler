"""Terrain synthesis: seamless height and colour tiles of the world at any zoom.

A tile is 257x257 samples on an edge-inclusive grid, so neighbours share their edges
exactly. Every sample descends the territory hierarchy from the root, collecting relief
from each level it passes through:

  - continents rise from the sea with shelving coasts (continental levels);
  - sibling territories are separated by ridgelines (drainage divides);
  - each level is raised a little above its parent's home basin, so water runs downhill
    toward the parent everywhere;
  - rivers carve their valleys; files stand as landmarks in their home district.

Detail fades in as a place grows on screen (no popping from one level to the next), and
colour follows content: a region full of photographs is forested from orbit, and its
individual woods appear as you approach. Unsurveyed places are drawn as terra incognita.

Heights are float64 world units while synthesising and are sent relative to the tile's
minimum (float32 keeps precision at any zoom); the client adds the base back.
"""
from __future__ import annotations

import json
import math
import os
import struct
import time
from functools import lru_cache

import numpy as np

from .index import NO_CRAWL

from .world import BUILD_DIRS, VENDORED, FIRST_CHILD, climate, HOME, OUT, SEA, GridNoise, World, _bilinear, coast_distance, content_shares, fbm, label_at, lattice, stable_hash, value_noise

N = 257
DAY = 86400.0

# Muted, satellite-like palettes per climate (the mount), MSI-restrained.
CLIMATE = {
    'native':    {'meadow': (122, 128, 94),  'dry': (146, 142, 112), 'forest': (72, 88, 64),  'field': (150, 146, 106), 'shore': (190, 182, 152)},
    'windows':   {'meadow': (108, 122, 84),  'dry': (134, 136, 100), 'forest': (60, 82, 56),  'field': (146, 144, 98),  'shore': (192, 184, 150)},
    'network':   {'meadow': (110, 120, 100), 'dry': (126, 130, 110), 'forest': (66, 82, 70),  'field': (132, 136, 110), 'shore': (170, 170, 150)},
    # Virtual filesystems (/proc, /sys) are the kernel's live state, remade every moment: a
    # volcanic wasteland of ash and cinder cones.
    'ephemeral': {'meadow': (92, 84, 80), 'dry': (106, 96, 88), 'forest': (70, 64, 62), 'field': (98, 88, 80), 'shore': (120, 110, 100)},
    'alpine':    {'meadow': (136, 138, 128), 'dry': (152, 152, 144), 'forest': (84, 96, 84),  'field': (146, 146, 132), 'shore': (178, 176, 168)},
}
# Rock by age: basalt, weathered basalt, sandstone, granite, all muted.
ROCK_BY_AGE = [(7, (74, 72, 70)), (180, (104, 98, 90)), (1095, (156, 126, 100)), (1e9, (178, 172, 164))]
SEA_DEEP = np.array((24, 58, 72), dtype=np.float64)
SEA_SHALLOW = np.array((58, 112, 122), dtype=np.float64)
RIVER = np.array((46, 86, 98), dtype=np.float64)
SNOW = np.array((236, 238, 236), dtype=np.float64)
PARCHMENT = np.array((206, 194, 162), dtype=np.float64)

# Land cover from content: what a place holds decides what grows there.
COVER_OF = {'images': 'forest', 'video': 'canyon', 'tables': 'field', 'code': 'town', 'databases': 'town',
            'weights': 'town', 'industry': 'town',
            'pdf': 'meadow', 'documents': 'meadow', 'audio': 'wet', 'archives': 'ice', 'binaries': 'rock',
            'disks': 'rock', 'other': 'dry', 'folders': 'dry'}
COVERS = ('meadow', 'forest', 'field', 'town', 'wet', 'ice', 'rock', 'dry', 'canyon')
# Detail materials the client textures with (native/textures, in this order). Covers map
# one-to-one: ice is snow, dry scrub and beaches are sand.
MATERIALS = ('meadow', 'forest', 'field', 'town', 'wet', 'snow', 'rock', 'sand')
MEADOW, FOREST, FIELD, TOWN, WET, SNOWM, ROCK, SAND = range(8)
COVER_MAT = np.zeros((len(COVERS), len(MATERIALS)))
for _i in range(8):
    COVER_MAT[_i, _i] = 1.0
COVER_MAT[COVERS.index('canyon'), SAND] = 0.6
COVER_MAT[COVERS.index('canyon'), ROCK] = 0.4


ROCK_LIMITS = np.array([lim for lim, _ in ROCK_BY_AGE[:-1]])
ROCK_LUT = np.array([c for _, c in ROCK_BY_AGE], dtype=np.float64)
KIND_NAMES = ('pdf', 'images', 'video', 'audio', 'tables', 'code', 'databases', 'archives', 'binaries', 'disks', 'documents',
              'other', 'industry', 'weights')
KIND_CODE = {k: i for i, k in enumerate(KIND_NAMES)}
SYMBOL_KINDS = ('video', 'pdf', 'images', 'archives', 'audio', 'code')
# 3D instances the client draws (native/models.gd builds the meshes, in this order).
MODEL_NAMES = ('broadleaf', 'conifer', 'house', 'boulder', 'palm', 'cactus', 'shrub', 'oak', 'flat_house', 'factory',
               'warehouse', 'silo', 'power_station', 'ruin', 'town_hall', 'keep', 'steam', 'obelisk', 'arch', 'block', 'tower',
               'wall_tower', 'aqueduct')
(BROADLEAF, CONIFER, HOUSE, BOULDER, PALM, CACTUS, SHRUB, OAK, FLAT, FACTORY, WAREHOUSE, SILO, POWER, RUIN, HALL,
 KEEP, STEAM, OBELISK, ARCH, BLOCK, TOWER, WALL_TOWER, AQUEDUCT) = range(len(MODEL_NAMES))
CITY_MIN = 25                   # a town of at least this many files grows a city centre
# Rows of self._lots: x, y, size, yaw, r, g, b, model, height factor.
ACROSS_BANK = 0.55              # a riverside town spreads along the water, not into it
BRIDGE_MIN = 18                 # buildings a town astride a river needs before it bridges it
MAX_SPANS = 24                  # an arcade stays a structure, not a wall across the map
LOT_COLUMNS = 9
SP = 3                          # instance grid spacing, in tile samples
ROLE_CODE = {'': 0, 'house': 1, 'depot': 2, 'factory': 3, 'silo': 4, 'power': 5, 'hall': 6, 'arch': 7, 'oak': 8, 'shrub': 9}
# Architecture is the language: a Python town has terracotta gables, a JavaScript town white
# flat roofs, C and C++ slate, Rust rust-red, Go blue flat roofs, Java green, shell timber.
LANG_STYLE = {}
for _exts, _model, _roof in (
        (('.py', '.pyi', '.ipynb'), 'house', (168, 92, 64)),
        (('.js', '.ts', '.tsx', '.jsx', '.mjs', '.cjs', '.vue', '.svelte'), 'flat_house', (206, 198, 180)),
        (('.c', '.h', '.cpp', '.hpp', '.cc', '.cxx', '.hh'), 'house', (96, 104, 116)),
        (('.rs',), 'house', (140, 72, 52)),
        (('.go',), 'flat_house', (120, 150, 170)),
        (('.java', '.kt', '.scala', '.groovy'), 'house', (80, 110, 84)),
        (('.rb', '.erb'), 'house', (150, 50, 56)),
        (('.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat', '.cmd'), 'house', (118, 90, 62)),
        (('.html', '.css', '.scss', '.sass', '.less'), 'flat_house', (214, 178, 120)),
        (('.json', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.xml'), 'flat_house', (170, 170, 160)),
        (('.gd', '.cs', '.fs'), 'house', (110, 86, 140)),
        (('.lua', '.php', '.swift', '.lisp', '.clj', '.el', '.hs', '.ml'), 'house', (146, 120, 90))):
    for _e in _exts:
        LANG_STYLE[_e] = (MODEL_NAMES.index(_model), _roof)
ZONE_CODE = {'native': 0, 'windows': 1, 'network': 2, 'ephemeral': 3, 'alpine': 4}
AUTUMN = np.array([(176, 108, 48), (196, 152, 60), (152, 70, 44)], dtype=np.float64)
VOLCANO = 100                   # symbol code for an erupting folder (not a file kind)
MAX_INSTANCES = 12000
INSTANCE = np.dtype([('u', '<f4'), ('v', '<f4'), ('h', '<f4'), ('s', '<f4'), ('kind', 'u1'), ('yaw', 'u1'),
                     ('rgb', 'u1', 3), ('pad', 'u1', 3)])
# Muted crops (wheat, young green, dark green, ploughed, stubble) and roofs (terracotta,
# slate, limestone, weathered); database towns are slate-blue.
CROPS = np.array([(176, 160, 92), (128, 142, 74), (96, 116, 62), (132, 106, 78), (164, 152, 112)], dtype=np.float64)
ROOFS = np.array([(152, 98, 78), (124, 122, 120), (170, 158, 140), (112, 104, 96)], dtype=np.float64)
# City centres: stone, brick, glass and concrete.
CITY_TINTS = np.array([(172, 164, 150), (150, 110, 88), (118, 136, 152), (190, 184, 172), (136, 128, 120)], dtype=np.float64)
ROOFS_DB = np.array([(104, 114, 128), (124, 130, 140), (92, 100, 112), (146, 150, 156)], dtype=np.float64)


def rock_colour(age_days):
    for limit, colour in ROCK_BY_AGE:
        if age_days < limit:
            return np.array(colour, dtype=np.float64)
    return np.array(ROCK_BY_AGE[-1][1], dtype=np.float64)


def _octave(freq: float) -> float:
    # Quantised so nearby places share one noise field per tile.
    return 2.0**round(math.log2(freq))


def smoothstep(a, b, x):
    t = np.clip((x-a)/(b-a), 0, 1)
    return t*t*(3-2*t)


def cover_fractions(kinds: dict, kind_bytes: dict | None = None) -> np.ndarray:
    # Count (log-compressed, so diversity shows) weighted by share of the bytes.
    f = np.zeros(len(COVERS))
    for kind, share in content_shares(kinds, kind_bytes).items():
        f[COVERS.index(COVER_OF.get(kind, 'dry'))] += share
    if f.sum() == 0:
        f[COVERS.index('dry')] = 1
    return f/f.sum()


class Synth:
    def __init__(self, world: World, zone_for=None):
        self.world = world
        self.zone = lru_cache(maxsize=65536)(zone_for or (lambda p: 'native'))

    def tile(self, level: int, tx: int, ty: int, now: float | None = None) -> dict:
        now = now or time.time()
        S = 0.5**level
        x0, y0 = tx*S, ty*S
        px = S/(N-1)
        g = np.arange(N)*px
        X, Y = np.meshgrid(x0+g, y0+g)
        xs, ys = X.ravel(), Y.ravel()
        M = xs.size
        self.G = GridNoise(x0, y0, px, N)

        H = np.zeros(M)                 # float64 world units
        ridge = np.zeros(M)             # 0..1 how much a pixel is ridgeline (for rock colour)
        coast = np.zeros(M)             # 0..1 closeness to a shore (beaches)
        border_line = np.zeros(M)
        water = np.zeros(M)             # 0 land .. 1 open water
        depth = np.zeros(M)             # water depth for colour
        leaf_kind = np.zeros(M, dtype=np.int8)   # 0 land-child, 1 home, 2 sea, 3 unsurveyed
        leaf_ref = np.zeros(M, dtype=np.int64)   # index into leaves list
        leaves = []                     # (territory, entry or None)
        fade_in = np.ones(M)
        leaf_bd = np.zeros(M)           # distance to the leaf's own border (home districts)
        symbol = np.zeros((M, 2))       # library landforms: strength, kind code

        clock = [time.perf_counter()]
        timing = {}
        def lap(name):
            now_ = time.perf_counter()
            timing[name] = round((now_-clock[0])*1000)
            clock[0] = now_
        self.timing = timing
        root = self.world.root()
        visited = {root.node_id: root}
        descended = {}                  # territory id -> set of child labels descended into
        current = np.full(M, root.node_id, dtype=np.int64)
        active = np.arange(M)
        side_of = {root.node_id: 0.6}

        for _ in range(48):
            if active.size == 0:
                break
            next_ids = []
            next_idx = []
            for tid in np.unique(current[active]):
                T = visited[int(tid)]
                idx = active[current[active] == tid]
                lab, bd = label_at(T, xs[idx], ys[idx], px, self.G, idx)
                side_T = side_of[T.node_id]
                fade = fade_in[idx]
                # Per-label lookup tables, so the arithmetic below runs on all pixels at once.
                labs, inv = np.unique(lab, return_inverse=True)
                n_l = len(labs)
                entries = [T.child_by_label.get(int(l)) for l in labs]
                is_sea = np.array([(l == SEA) or (l == OUT and T.continental) for l in labs])
                is_home = np.array([(l == HOME) or (l == OUT and not T.continental) or (l >= FIRST_CHILD and entries[i] is None)
                                    for i, l in enumerate(labs)])
                side_l = np.array([e['side'] if e is not None else 0.0 for e in entries])
                seed_l = np.array([stable_hash(e['path']) % 8 if e is not None else 0 for e in entries])
                kind_l = np.where(is_sea, 0, np.where(is_home, 1, np.where(side_l < 5*px, 2, 3)))
                pk = kind_l[inv]
                # Sea: a flat surface; depth only shades the water.
                m = pk == 0
                if m.any():
                    k = idx[m]
                    depth[k] = smoothstep(0, 0.08*side_T, np.minimum(bd[m], np.maximum(-coast_distance(T, xs[k], ys[k], px, self.G, k), 0)) if T.continental else bd[m])
                    water[k] = 1.0
                    leaf_kind[k] = 2
                    leaves.append((T, None))
                    leaf_ref[k] = len(leaves)-1
                # Home district: the directory's own ground; its files stand here.
                m = pk == 1
                if m.any():
                    k = idx[m]
                    leaf_kind[k] = 1
                    leaf_bd[k] = bd[m]
                    leaves.append((T, None))
                    leaf_ref[k] = len(leaves)-1
                # Places under ~5 px merge into one leaf coloured by the parent's whole subtree.
                m = pk == 2
                if m.any():
                    k = idx[m]
                    leaf_kind[k] = 0
                    leaf_bd[k] = bd[m]
                    leaves.append((T, self._aggregate(T, side_T)))
                    leaf_ref[k] = len(leaves)-1
                # Relief of every child, tiny ones included. Anything that differs between the
                # two sides of a border must vanish at the border (b = 0), or it becomes a cliff;
                # ridge heights therefore scale with the parent's typical province, not each side.
                typical = side_T/math.sqrt(len(T.children)+1)
                relief = (pk == 1) | (pk == 2) | (pk == 3)   # all land in this territory
                if relief.any():
                    k = idx[relief]
                    b = bd[relief]
                    f = fade[relief]
                    sc = np.maximum(side_l[inv[relief]], px)
                    if T.continental:
                        # Continents rise out of the sea with shelving coasts and rolling uplands.
                        # Measured from the sea: provinces of one disk share land, and their mutual
                        # borders are ridges, not coasts.
                        bs = np.maximum(coast_distance(T, xs[k], ys[k], px, self.G, k), 0)
                        shore = smoothstep(0, 0.09*typical*2, bs)
                        hills = self.G.smooth_fbm(_octave(1.1/typical), 5, 300)[k]
                        H[k] += typical*(0.02*shore + 0.016*shore*(hills+0.25))*f
                        coast[k] = np.maximum(coast[k], (1-smoothstep(0, 0.024*typical, bs))*f)
                        # Ridges follow every border but fade out toward the sea: a boolean
                        # "which border is nearest" test flipped between estimates and left steps.
                        inland = np.ones(k.size, dtype=bool)
                        coastfade = smoothstep(0, 0.07*typical, bs)
                    else:
                        # Provinces rise a little above the parent's basin (the home district is
                        # the basin); divides come and go.
                        child = pk[relief] != 1
                        H[k[child]] += (0.004*sc*smoothstep(0, 0.25*sc, b))[child]*f[child]
                        if child.any():
                            # Rolling uplands inside each province, vanishing at its border.
                            kc = k[child]
                            scc = sc[child]
                            hills = self._grouped_fbm(kc, scc, 2.5, 4, 610, np.zeros(kc.size, dtype=np.int64))
                            H[kc] += 0.014*scc*(hills+0.35)*smoothstep(0, 0.3*scc, b[child])*f[child]
                        inland = np.ones(k.size, dtype=bool)
                        show = smoothstep(300, 900, side_T/px)*(1-smoothstep(3000, 9000, side_T/px))
                        self._volcanoes(T, k, b, sc, pk[relief], inv[relief], entries, f, xs, ys, px, H, symbol)
                        if T.uniform:
                            # A library is one landscape: no divides between its members, and
                            # each member stands as one landform of the library's kind.
                            inland = np.zeros(k.size, dtype=bool)
                            show = 0
                            self._library_symbols(T, k, b, sc, pk[relief], seed_l[inv[relief]], inv[relief], entries, f, xs, ys, px, H, symbol, typical)
                        if show > 0:
                            dots = (self.G.value(0.25/px, 77)[k] > 0.45)
                            border_line[k] = np.maximum(border_line[k], np.clip(1.0-b/px, 0, 1)*show*dots*0.6)
                    if inland.any():
                        ki = k[inland]
                        gate = np.clip((1.0-np.abs(self.G.smooth_fbm(_octave(2.0/typical), 4, 400)[ki]))*2.0-1.1, 0, 1)
                        if T.river_dist is not None:
                            # Ridges part where the main rivers run: water gaps, not rivers on crests.
                            u = (xs[ki]-T.x0)/T.cell-0.5
                            v = (ys[ki]-T.y0)/T.cell-0.5
                            gate *= smoothstep(1.0, 3.5, _bilinear(T.river_dist, u, v, T.n))
                        rr = np.exp(-(b[inland]/(0.07*typical))**2)*gate
                        if T.continental:
                            rr *= coastfade[inland]
                        H[ki] += 0.03*typical*rr*f[inland]
                        ridge[ki] = np.maximum(ridge[ki], rr*f[inland]*0.8)
                m = pk == 3
                if not m.any():
                    continue
                k = idx[m]
                f = fade[m]
                b_child = bd[m]
                # Descend into places big enough to show their insides; the rest are leaves.
                leaf_lut = np.full(n_l, -1, dtype=np.int64)
                sub_inv = inv[m]
                for li in np.flatnonzero(kind_l == 3):
                    entry = entries[li]
                    inside = self.world.child(T, entry) if side_l[li] > 24*px else None
                    sel = sub_inv == li
                    if inside is not None:
                        descended.setdefault(T.node_id, set()).add(int(labs[li]))
                        visited[inside.node_id] = inside
                        side_of[inside.node_id] = side_l[li]
                        kk = k[sel]
                        # Detail inside a place fades in as it grows on screen, and fades out
                        # at its own border, so no deeper level can raise a step at an edge.
                        # Library members keep their single landform longer before their own
                        # fields take over (60-160 px rather than 24-90 px).
                        lo, hi = (60, 160) if T.uniform else (24, 90)
                        fade_in[kk] = f[sel]*smoothstep(lo*px, hi*px, side_l[li])*smoothstep(0, 0.12*side_l[li], b_child[sel])
                        next_ids.append(np.full(kk.size, inside.node_id, dtype=np.int64))
                        next_idx.append(kk)
                    else:
                        leaves.append((T, self._aggregate(T, side_T, entry) if T.uniform else entry))
                        leaf_lut[li] = len(leaves)-1
                leafy = leaf_lut[sub_inv] >= 0
                if leafy.any():
                    kk = k[leafy]
                    leaf_ref[kk] = leaf_lut[sub_inv[leafy]]
                    # Virtual filesystems (/proc, /sys…) are never crawled on purpose: they are
                    # desert, not terra incognita.
                    surveyed = np.array([bool(e and (e['scanned'] or e['files'] or e['dirs'] or e['path'] in NO_CRAWL)) for e in entries])
                    leaf_kind[kk] = np.where(surveyed[sub_inv[leafy]], 0, 3)
                    leaf_bd[kk] = b_child[leafy]
            if next_idx:
                active = np.concatenate(next_idx)
                current[active] = np.concatenate(next_ids)
            else:
                active = np.array([], dtype=np.int64)
        lap('descent')
        # Fractal detail, self-similar in world units, from 64 tiles down to 8 samples; finer
        # relief is drawn by the GPU at screen resolution (terrain.gdshader).
        kmin = max(0, int(math.floor(math.log2(1/(64*S)))))
        kmax = int(math.floor(math.log2(1/(8*px))))
        # Land-only, and faded out toward the coast: this term is large at coarse octaves
        # (a tenth of a tile), and cut off at the shoreline it stood as a sea wall.
        land = (water < 0.5)*smoothstep(0, 0.02, np.maximum(coast_distance(root, xs, ys, px, self.G, np.arange(M)), 0))
        detail = np.zeros(M)
        for k in range(kmin, kmax+1):
            f = 2.0**k
            fade = 1.0 if k < kmax else 0.5
            detail += (self.G.smooth(f, 7000+k)*2-1)*(0.0022/f)*fade
        H += detail*land

        lap('detail')
        mat = np.zeros((M, len(MATERIALS)))
        # Geysers and their steam are drawn from a 15-minute window, so a tile that has any
        # must not outlive the session on disk: the shape signatures do not notice activity.
        self.transient = False
        self._planned = np.zeros(M, dtype=bool)   # pixels whose houses come from their lots
        self._lots = []                           # rows: LOT_COLUMNS
        self._cities = []                         # (x, y, radius) of city centres drawn here
        self._city_done = set()                   # patches whose lots are already drawn
        self._species = np.full(M, -1, dtype=np.int64)   # tree species set by the files there
        self._leaf_info(leaves, now)
        self._children_landforms(visited, descended, x0, y0, S, px, now)
        colour = self._colour(xs, ys, px, H, ridge, water, depth, leaf_kind, leaf_ref, leaves, now, coast, mat, leaf_bd)
        lap('colour')
        self._symbol_colour(symbol, colour, mat, H, px)
        rain = self._rain(leaf_ref, leaves, M)
        fog = (leaf_kind == 3).astype(np.float64)
        lap('rain')
        self._fields(xs, ys, px, visited, leaf_kind, leaf_ref, leaves, leaf_bd, fade_in, H, water, colour, mat, now)
        lap('fields')
        self._waters(xs, ys, px, S, x0, y0, visited, descended, H, water, depth, colour, mat)
        lap('rivers')
        colour = self._contours(H, water, colour, px)
        lap('contours')
        colour[:, :3] = colour[:, :3]*(1-0.35*border_line[:, None]) + np.array([70, 60, 45])*0.35*border_line[:, None]

        base = float(H.min())
        mat *= (water < 0.5)[:, None]
        instances = self._instances(tx, ty, x0, y0, S, px, H, base, water, colour, mat, leaf_kind, leaf_ref)
        lap('instances')
        mat = np.clip(mat*255, 0, 255).astype(np.uint8)
        return {'level': level, 'x': tx, 'y': ty, 'base': base, 'instances': instances, 'transient': self.transient,
                'height': (H-base).astype(np.float32).reshape(N, N),
                'colour': np.clip(colour, 0, 255).astype(np.uint8).reshape(N, N, 4),
                'aux': np.stack([np.clip(rain*255, 0, 255), np.clip(fog*255, 0, 255), np.clip(depth*255, 0, 255), np.clip(ridge*255, 0, 255)], axis=1).astype(np.uint8).reshape(N, N, 4),
                'mat_a': mat[:, :4].reshape(N, N, 4).copy(), 'mat_b': mat[:, 4:].reshape(N, N, 4).copy()}

    def _instances(self, tx, ty, x0, y0, S, px, H, base, water, colour, mat, leaf_kind, leaf_ref):
        """3D landmarks for the client to instance: trees where the ground is forest, houses
        on town ground (from the actual lots where a town of files is drawn), boulders on bare
        rock. Positions are tile-relative; sizes are map symbols (a few samples), like Civ's
        trees, so a forest reads as a forest at every zoom."""
        sp = SP
        g = np.arange(0, N-1, sp)
        I, J = np.meshgrid(g, g)
        I, J = I.ravel().astype(np.int64), J.ravel().astype(np.int64)
        gx, gy = tx*(N-1)+I, ty*(N-1)+J
        r = [lattice(gx, gy, 9301+q) for q in range(5)]
        fx = np.clip(I+r[0]*sp, 0, N-1.001)
        fy = np.clip(J+r[1]*sp, 0, N-1.001)
        k = np.rint(fy).astype(np.int64)*N+np.rint(fx).astype(np.int64)
        ok = (water[k] < 0.3) & (leaf_kind[k] <= 1)
        forest, town, rock, snow = mat[k, FOREST], mat[k, TOWN], mat[k, ROCK], mat[k, SNOWM]
        kind = np.full(k.size, -1, dtype=np.int64)
        # Only where a cover clearly dominates, so models gather in clumps (a wood, a village)
        # instead of sprinkling every mixed patch.
        tree = ok & (r[2] < np.clip((forest-0.35)*1.8, 0, 0.97))
        # The trees are the climate, i.e. the disk: broadleaf woods on Linux, jungle and palms
        # on Windows drives, conifers where you cannot write, cacti on virtual filesystems.
        zone = self._leaf_zone[leaf_ref[k]]
        pick = r[3]
        species = np.select([snow > 0.2, zone == ZONE_CODE['alpine'],
                             (zone == ZONE_CODE['windows']) & (pick < 0.4), zone == ZONE_CODE['windows'],
                             (zone == ZONE_CODE['network']) & (pick < 0.35),
                             (zone == ZONE_CODE['ephemeral']) & (pick < 0.3), zone == ZONE_CODE['ephemeral'],
                             pick < 0.22],
                            [CONIFER, CONIFER, PALM, BROADLEAF, SHRUB, STEAM, BOULDER, CONIFER], BROADLEAF)
        species = np.where(self._species[k] >= 0, self._species[k], species)
        kind[tree] = species[tree]
        # Cinder and fumaroles across the wasteland, whatever grows there otherwise.
        desert = ok & ~tree & (zone == ZONE_CODE['ephemeral']) & (r[4] < 0.07)
        kind[desert] = np.where(r[2][desert] < 0.35, STEAM, BOULDER)
        # Settlements cluster into villages with open land between (Civ's towns are places,
        # not a blanket): a village field, world-anchored, decides where houses may stand.
        village = self.G.smooth_fbm(1/(px*40), 3, 8801)[k]
        house = ok & ~tree & ~desert & (r[4] < np.clip((town-0.5)*1.2, 0, 0.55)*smoothstep(0.1, 0.35, village)) & ~self._planned[k]
        # Villages far off take the role of the place they stand in: warehouses among
        # vendored dependencies, factories in build output.
        lref = leaf_ref[k]
        kind[house] = np.where(self._leaf_vendored[lref][house], WAREHOUSE, np.where(self._leaf_build[lref][house], FACTORY, HOUSE))
        boulder = ok & ~tree & ~house & (r[4] < np.clip((rock-0.5)*0.25, 0, 0.12))
        kind[boulder] = BOULDER
        keep = kind >= 0
        building = (kind == HOUSE) | (kind == WAREHOUSE) | (kind == FACTORY)
        size = np.where(building, 1.25, np.where(kind == BOULDER, 0.7, np.where(kind == OAK, 1.7, np.where(kind == SHRUB, 0.8,
                        np.where(kind == PALM, 1.5, 1.15+0.5*r[3])))))*sp*px
        tint = colour[k, :3]*np.where(kind[:, None] == BOULDER, 0.95, 1.08)
        roofs = ROOFS[(r[3]*len(ROOFS)).astype(np.int64) % len(ROOFS)]
        tint = np.where(kind[:, None] == HOUSE, roofs, tint)
        tint = np.where(kind[:, None] == WAREHOUSE, np.array((132, 140, 148)), tint)
        tint = np.where(kind[:, None] == FACTORY, np.array((148, 92, 70)), tint)
        tint = np.where(kind[:, None] == CACTUS, np.array((92, 128, 78)), tint)
        ash = (zone == ZONE_CODE['ephemeral'])[:, None]
        tint = np.where(ash & (kind[:, None] == BOULDER), np.array((52, 48, 48)), tint)
        tint = np.where(ash & (kind[:, None] == STEAM), np.array((150, 144, 140)), tint)
        tint = np.where(kind[:, None] == PALM, tint*0.4+np.array((118, 150, 66))*0.6, tint)   # palms: lighter fronds
        u, v, hh = fx[keep]/(N-1), fy[keep]/(N-1), H[k[keep]]
        kinds, sizes, yaws, tints = kind[keep], size[keep], r[4][keep]*math.tau, tint[keep]
        heights = np.ones(len(kinds))
        if self._cities:
            # No scattered village houses inside a city centre.
            cx_ = x0+u*S
            cy_ = y0+v*S
            scatter = (kinds == HOUSE) | (kinds == WAREHOUSE) | (kinds == FACTORY)
            for (ccx, ccy, cr) in self._cities:
                scatter &= np.hypot(cx_-ccx, cy_-ccy) > cr
            drop = ((kinds == HOUSE) | (kinds == WAREHOUSE) | (kinds == FACTORY)) & ~scatter
            u, v, hh, kinds, sizes, yaws, tints, heights = u[~drop], v[~drop], hh[~drop], kinds[~drop], sizes[~drop], yaws[~drop], tints[~drop], heights[~drop]
        if self._lots:
            lots = np.vstack(self._lots)
            li = ((lots[:, 0]-x0)/px).astype(np.int64)
            lj = ((lots[:, 1]-y0)/px).astype(np.int64)
            inside = (li >= 0) & (li < N) & (lj >= 0) & (lj < N)
            inside[inside] &= water[lj[inside]*N+li[inside]] < 0.3   # no houses in the river
            lots, li, lj = lots[inside], li[inside], lj[inside]
            u = np.concatenate([u, (lots[:, 0]-x0)/S])
            v = np.concatenate([v, (lots[:, 1]-y0)/S])
            hh = np.concatenate([hh, H[lj*N+li]])
            kinds = np.concatenate([kinds, lots[:, 7].astype(np.int64)])
            heights = np.concatenate([np.ones(len(kind[keep])), lots[:, 8]])
            # Buildings are map symbols too: sizes were capped where each lot was made.
            sizes = np.concatenate([sizes, lots[:, 2]])
            yaws = np.concatenate([yaws, -lots[:, 3]])
            tints = np.vstack([tints, lots[:, 4:7]])
        for m in self._monuments():
            # The largest file on each disk stands as a monument, visible at every zoom.
            if x0 <= m['x'] < x0+S and y0 <= m['y'] < y0+S:
                i = min(int((m['x']-x0)/px), N-1)
                j = min(int((m['y']-y0)/px), N-1)
                u = np.append(u, (m['x']-x0)/S)
                v = np.append(v, (m['y']-y0)/S)
                hh = np.append(hh, H[j*N+i])
                kinds = np.append(kinds, OBELISK)
                heights = np.append(heights, 1.0)
                # Scaled with its disk, within symbol limits: small from orbit, never a tower block.
                sizes = np.append(sizes, float(np.clip(0.012*m.get('disk_side', 0.2), 2.5*sp*px, 5.0*sp*px)))
                yaws = np.append(yaws, 0.0)
                tints = np.vstack([tints, np.array((212, 184, 110))])
        order = np.argsort(-sizes, kind='stable')[:MAX_INSTANCES]
        out = np.zeros(len(order), dtype=INSTANCE)
        out['u'], out['v'] = u[order], v[order]
        out['h'] = (hh[order]-base)/S
        out['s'] = sizes[order]/S
        out['kind'] = kinds[order]
        out['yaw'] = (np.mod(yaws[order], math.tau)/math.tau*255).astype(np.uint8)
        out['rgb'] = np.clip(tints[order], 0, 255).astype(np.uint8)
        out['pad'][:, 0] = np.clip(np.rint(heights[order]*32), 32, 255).astype(np.uint8)   # height factor x32
        return out

    def _leaf_info(self, leaves, now):
        """Per-leaf facts for the landforms: climate zone, dependency or build ground, an
        empty folder (a salt flat), a folder the survey could not open (fenced)."""
        L = len(leaves)
        self._leaf_zone = np.zeros(L, dtype=np.int64)
        self._leaf_vendored = np.zeros(L, dtype=bool)
        self._leaf_build = np.zeros(L, dtype=bool)
        self._leaf_empty = np.zeros(L, dtype=bool)
        self._leaf_fenced = np.zeros(L, dtype=bool)
        for i, (T, entry) in enumerate(leaves):
            if entry is not None and T.path == '/':
                zone = self.zone(entry['path'])
            else:
                zone = climate(T.zone, T.writable)
            self._leaf_zone[i] = ZONE_CODE.get(zone, 0)
            path = (entry['path'] if entry is not None else T.path)+'/'
            self._leaf_vendored[i] = any(v in path for v in VENDORED)
            self._leaf_build[i] = any(b in path for b in BUILD_DIRS)
            if entry is not None and entry.get('label') is not None:
                self._leaf_empty[i] = bool(entry['scanned']) and not entry['files'] and not entry['dirs'] and not entry.get('error')
                self._leaf_fenced[i] = bool(entry.get('error'))
            elif entry is None and T.path != '/':
                # A place drawn as its own territory: empty when it holds nothing at all.
                self._leaf_empty[i] = bool(T.node.get('scanned')) and not T.children and not T.places.get('n')
                self._leaf_fenced[i] = bool(T.node.get('error'))

    def _children_landforms(self, visited, descended, x0, y0, S, px, now):
        """Geysers where files changed in the last 15 minutes, smoke over erupting folders:
        drawn for places too small to show their own fields."""
        for T in visited.values():
            inside = descended.get(T.node_id, set())
            for c in T.children:
                if c['label'] in inside or c['side'] < 4*px:
                    continue
                x, y = c['centroid']
                if not (x0 <= x < x0+S and y0 <= y < y0+S):
                    continue
                if now-(c['newest'] or 0) < 900:
                    self.transient = True
                    self._lots.append(np.array([[x, y, 1.3*SP*px, 0.0, 236, 240, 238, STEAM, 1.0]]))
                if _volcanic(c):
                    self._lots.append(np.array([[x, y, 1.8*SP*px, 0.0, 84, 80, 78, STEAM, 1.0]]))
                # A folder mostly of code, seen from afar, is one city: Civ-like, tall at its
                # centre. Vendored dependencies are a warehouse district instead.
                code = content_shares(c['kinds'], c.get('kind_bytes')).get('code', 0)+content_shares(c['kinds'], c.get('kind_bytes')).get('databases', 0)
                if code >= 0.4 and (c['files'] or 0) >= CITY_MIN and c['side'] >= 6*SP*px:
                    vendored = any(v in c['path']+'/' for v in VENDORED)
                    rows = self._city(x, y, c['side'], c['files'], 'depot' if vendored else 'code', c['path'], px,
                                      self._river_axis(T, x, y))
                    if rows:
                        self._lots.append(np.array(rows, dtype=np.float64))

    def _aqueduct(self, x, y, rf, px, T=None, pl=None):
        """A folder's links are one aqueduct: an unbroken arcade, not a scatter of ruins.

        It is built where a Roman one would be, and so has a source and a destination rather
        than two arbitrary ends: it starts at the water (the folder's river, or its outlet)
        and runs to the settlement it serves (the town of source files). With neither, it
        falls back to the line the links themselves lie along. Spans abut, one pier shared
        between neighbours, so the deck reads as one channel."""
        tint = (196, 180, 150)
        size = float(np.clip(float(np.median(rf))*1.4, 3*px, 7*px))
        cx, cy = float(x.mean()), float(y.mean())
        src = dst = None
        if T is not None:
            axis = self._river_axis(T, cx, cy)
            if axis is not None:
                _, toward, dist = axis
                src = (cx+toward[0]*dist, cy+toward[1]*dist)      # the bank it draws from
        if pl is not None:
            towns = [q for q in pl.get('patches', []) if q['kind'] in ('code', 'databases')]
            if towns:
                q = min(towns, key=lambda q: (q['x']-cx)**2+(q['y']-cy)**2)
                dst = (q['x'], q['y'])                            # the place it supplies
        if src is None or dst is None or math.dist(src, dst) < 2*size:
            # No water or no town to serve: lay it along the links' own scatter instead.
            if x.size <= 2:
                return [[float(x[i]), float(y[i]), size, 0.0, *tint, AQUEDUCT, 1.0] for i in range(x.size)]
            u, v = x-cx, y-cy
            ang = 0.5*math.atan2(2*float((u*v).sum()), float((u*u).sum()-(v*v).sum()))
            d = u*math.cos(ang)+v*math.sin(ang)
            src = (cx+math.cos(ang)*float(d.min()), cy+math.sin(ang)*float(d.min()))
            dst = (cx+math.cos(ang)*float(d.max()), cy+math.sin(ang)*float(d.max()))
        run = math.dist(src, dst)
        ang = math.atan2(dst[1]-src[1], dst[0]-src[0])
        # One span per span-width, so the piers meet and the decks join. A long run widens its
        # spans rather than stopping short: an aqueduct that does not arrive is a ruin.
        n = int(np.clip(round(run/size), 1, MAX_SPANS))
        size = float(np.clip(run/n, 3*px, 12*px))
        ux, uy = math.cos(ang), math.sin(ang)
        return [[src[0]+ux*size*(k+0.5), src[1]+uy*size*(k+0.5), size, ang, *tint, AQUEDUCT, 1.0]
                for k in range(n)]

    def _file_landforms(self, T, pl, roles, x0, y0, S, px, now):
        """Single landmarks at a file's site: a town hall for a project manifest, a power
        station for model weights, an arch for a link, a geyser for a file changed in the last
        15 minutes, and a keep at the heart of a git repository."""
        n = pl['n']
        x, y, rf = pl['x'], pl['y'], pl.get('rf', pl['r'])
        inside = (x >= x0) & (x < x0+S) & (y >= y0) & (y < y0+S) & (rf/px >= 2.0)
        rows = []
        # (role, model, smallest and largest size in samples, tint)
        for role, model, lo, hi, tint in ((ROLE_CODE['hall'], HALL, 3, 7, (150, 96, 70)),
                                          (ROLE_CODE['power'], POWER, 4, 10, (208, 204, 196))):
            for i in np.flatnonzero(inside & (roles == role)):
                rows.append([x[i], y[i], float(np.clip(rf[i]*1.2, lo*px, hi*px)), (i*2.4) % math.tau, *tint, model, 1.0])
        links = np.flatnonzero(inside & (roles == ROLE_CODE['arch']))
        if links.size:
            rows += self._aqueduct(x[links], y[links], rf[links], px, T, pl)
        recent = np.flatnonzero(inside & (now-pl['mtime'] < 900))
        self.transient = self.transient or recent.size > 0
        for i in recent:
            rows.append([x[i]+rf[i]*0.25, y[i], 1.3*SP*px, 0.0, 236, 240, 238, STEAM, 1.0])
        if T.repo:
            town = [q for q in pl.get('patches', []) if q['kind'] == 'code']
            if town and x0 <= town[0]['x'] < x0+S and y0 <= town[0]['y'] < y0+S and town[0]['side']/px > 6:
                rows.append([town[0]['x'], town[0]['y'], float(np.clip(0.3*town[0]['side'], 5*SP*px, 9*SP*px)), 0.4, 176, 60, 52, KEEP, 1.0])
        rows.extend(self._city_centres(T, pl, x0, y0, S, px))
        if rows:
            self._lots.append(np.array(rows, dtype=np.float64))

    @staticmethod
    def _skyline(pl, fi, wx, wy, jitter):
        """Height factor of buildings in a town: 1 at the edge, rising toward the centre of a
        big town (more files, taller centre), like Civ's cities."""
        hf = np.ones(len(fi))
        patches = pl.get('patches', [])
        for j, f in enumerate(fi):
            q = patches[int(pl['patch'][f])] if patches else None
            if q is None or q['n'] < CITY_MIN:
                continue
            rel = math.hypot(wx[j]-q['x'], wy[j]-q['y'])/(0.42*q['side'])
            top = 1.0+1.1*min(3.0, math.log10(q['n']))
            hf[j] = 1.0+(top-1.0)*max(0.0, 1.0-rel)**1.6*(0.55+0.45*float(jitter[j]))
        return hf

    def _city_centres(self, T, pl, x0, y0, S, px):
        """Zoomed out, a big town of files is a Civ-like city: a tight cluster of buildings,
        low at the edge and tallest at the centre, standing for the whole patch."""
        rows = []
        for pi, q in enumerate(pl.get('patches', [])):
            if q['kind'] not in ('code', 'databases', 'industry') or q['n'] < CITY_MIN:
                continue
            if (T.node_id, pi) in self._city_done or not (x0 <= q['x'] < x0+S and y0 <= q['y'] < y0+S):
                continue
            rows.extend(self._city(q['x'], q['y'], q['side'], q['n'], q['kind'], T.path+str(pi), px,
                                   self._river_axis(T, q['x'], q['y'])))
        return rows

    def _river_axis(self, T, x, y):
        """Where the nearest river runs past a point, from the drainage's own distance field.

        Returns (angle along the bank, unit vector toward the water, distance in world units),
        or None where no river is near. The same raster already parts the ridges at _ridges."""
        if T.river_dist is None:
            return None
        u = (x-T.x0)/T.cell-0.5
        v = (y-T.y0)/T.cell-0.5
        if not (1 <= u < T.n-2 and 1 <= v < T.n-2):
            return None
        # The distance field grows away from the water, so its gradient points inland and the
        # river itself runs across that: perpendicular to the gradient.
        gx = float(_bilinear(T.river_dist, u+1, v, T.n)-_bilinear(T.river_dist, u-1, v, T.n))
        gy = float(_bilinear(T.river_dist, u, v+1, T.n)-_bilinear(T.river_dist, u, v-1, T.n))
        g = math.hypot(gx, gy)
        if g < 1e-6:
            return None
        return math.atan2(gx, -gy), (-gx/g, -gy/g), float(_bilinear(T.river_dist, u, v, T.n))*T.cell

    def _city(self, x, y, side, n, kind, key, px, axis=None):
        """A Civ-like city: a tight cluster of buildings on a golden-angle spiral, tallest at
        the centre (more files, taller), low houses at its edge.

        Given a river axis the town sits on the bank and grows along it rather than as a disc:
        settlements follow water, and a ring of houses dropped on a patch centroid never read
        as one."""
        b = 1.25*SP*px                                            # one building, as a map symbol
        if side < 3*b:
            return []
        count = int(np.clip(3*math.sqrt(n), 7, 70))
        R = min(0.4*side, b*0.62*math.sqrt(count))
        count = min(count, int((R/b)**2/0.4)+1)
        h = stable_hash(key+'city')
        ang = (h % 628)/100
        al = ac = 0.0
        straddles = False
        if axis is not None:
            bank, toward, dist = axis
            al, ac = math.cos(bank), math.sin(bank)
            # A river already inside the town's reach is built across, not backed away from:
            # the town sits astride it and the water divides it into two banks. Otherwise it
            # walks to the water and stops its own edge short.
            straddles = dist < R
            step = dist if straddles else max(0.0, min(dist-R*0.4, 0.6*side))
            x, y = x+toward[0]*step, y+toward[1]*step
        top = 1.0+1.1*min(3.0, math.log10(max(n, 1)))
        if kind == 'depot':
            top = min(top, 1.8)                                   # warehouses stay low
        golden = math.pi*(3-math.sqrt(5))
        rows = []
        for i in range(count):
            rr = R*math.sqrt((i+0.5)/count)
            a = i*golden+ang
            ox, oy = rr*math.cos(a), rr*math.sin(a)
            if axis is not None:
                along, across = ox*al+oy*ac, (-ox*ac+oy*al)*ACROSS_BANK
                ox, oy = along*al-across*ac, along*ac+across*al
            cx, cy = x+ox, y+oy
            jit = ((h >> (i % 40)) & 0xFF)/255
            hf = 1.0+(top-1.0)*(1-rr/R)**1.6*(0.55+0.45*jit)
            if kind == 'depot':
                model = BLOCK if hf > 1.5 else WAREHOUSE
                tint = np.array((132, 140, 148)) if model == WAREHOUSE else CITY_TINTS[(h >> (i % 23)) % len(CITY_TINTS)]
            else:
                model = TOWER if hf > 2.4 else BLOCK if hf > 1.4 else (FLAT if kind != 'databases' else SILO)
                tint = CITY_TINTS[(h >> (i % 23)) % len(CITY_TINTS)] if hf > 1.4 else ROOFS[(h >> (i % 17)) % len(ROOFS)]
            rows.append([cx, cy, b*(0.9+0.2*jit), ang, *tint, model, hf])
        if straddles and count >= BRIDGE_MIN and (h >> 7) % 3:
            # Astride the water, a town crosses it: one arch laid across the flow, the same
            # masonry as an aqueduct's spans. Not every town — a crossing stays an event.
            rows.append([x, y, float(np.clip(0.22*R, 2.5*px, 7*px)), bank+math.pi/2,
                         196, 180, 150, AQUEDUCT, 1.0])
        self._cities.append((x, y, R+b))
        return rows

    @staticmethod
    def _lot_models(fi, roles, style_model, style_roof, age_file):
        """Which building stands on each lot: the file's role, then its language's
        architecture; code untouched for three years stands in ruins."""
        role = roles[fi]
        model = style_model[fi].copy()
        tint = style_roof[fi].copy()
        for code, m, t in ((ROLE_CODE['depot'], WAREHOUSE, (132, 140, 148)), (ROLE_CODE['factory'], FACTORY, (148, 92, 70)),
                           (ROLE_CODE['silo'], SILO, (206, 200, 188))):
            sel = role == code
            model[sel] = m
            tint[sel] = t
        old = (age_file[fi] > 1095) & ((model == HOUSE) | (model == FLAT) | (model == WAREHOUSE) | (model == FACTORY))
        model[old] = RUIN
        tint[old] = (150, 142, 128)
        return model, tint

    def _monuments(self):
        """The largest file on each disk (worked out by the Atlas in the background)."""
        now = time.monotonic()
        if getattr(self, '_monument_time', -1e9) < now-60:
            self._monument_time = now
            self._monument_list = []
            index = getattr(self.world, 'index', None)
            try:
                row = index.db().execute("SELECT value FROM meta WHERE key='monuments'").fetchone() if index else None
                self._monument_list = json.loads(row[0]).get('places', []) if row else []
            except Exception:
                self._monument_list = []
        return self._monument_list

    def _volcanoes(self, T, k, b, sc, kinds_px, inv, entries, f, xs, ys, px, H, symbol):
        """A folder most of whose files changed this week erupts: a volcano at its heart,
        fading out as its own fields take over up close."""
        flags = np.array([bool(e is not None and _volcanic(e)) for e in entries])
        if not flags.any():
            return
        sel = (kinds_px >= 2) & flags[inv]
        if not sel.any():
            return
        kk = k[sel]
        cx = np.array([e['centroid'][0] if e is not None else 0.0 for e in entries])[inv[sel]]
        cy = np.array([e['centroid'][1] if e is not None else 0.0 for e in entries])[inv[sel]]
        side = sc[sel]
        d = np.hypot(xs[kk]-cx, ys[kk]-cy)/(0.5*side)
        cone = np.clip(1-d/0.6, 0, 1)
        crater = smoothstep(0.14, 0.05, d)
        w = smoothstep(0, 0.15*side, b[sel])*(1-smoothstep(60*px, 160*px, side))*f[sel]
        H[kk] += side*0.1*(cone**1.3-0.35*crater)*w
        strength = np.where(cone > 0, np.clip(cone*3, 0, 1)*w+crater*w, 0)
        better = strength > symbol[kk, 0]
        symbol[kk[better], 0] = strength[better]
        symbol[kk[better], 1] = VOLCANO

    def _grouped_fbm(self, k, side, scale, octaves, base_seed, seed_class):
        """Smooth noise with wavelength proportional to each place's size, from shared tile fields."""
        freq_key = np.round(np.log2(scale/side)).astype(np.int64)
        out = np.empty(k.size)
        for fk in np.unique(freq_key):
            for sc in np.unique(seed_class[freq_key == fk]):
                sel = (freq_key == fk) & (seed_class == sc)
                out[sel] = self.G.smooth_fbm(2.0**float(fk), octaves, base_seed)[k[sel]]
        return out

    @staticmethod
    def _aggregate(T, side_T, entry=None):
        node = T.node
        if entry is not None:
            # A library member drawn in the library's colours, but keeping its own age and activity.
            base = Synth._aggregate(T, side_T)
            base.update(path=entry['path'], newest=entry['newest'] or base['newest'], day=entry['day'], side=entry['side'],
                        scanned=entry['scanned'], files=entry['files'], dirs=entry['dirs'])
            return base
        return {'path': T.path, 'kinds': json.loads(node['kinds']) if node.get('kinds') else {},
                'kind_bytes': json.loads(node['kind_bytes']) if node.get('kind_bytes') else {}, 'newest': node.get('newest') or 0,
                'mtime': node.get('mtime') or 0, 'side': side_T*0.25, 'day': node.get('day') or 0, 'scanned': True,
                'files': node.get('files') or 0, 'dirs': node.get('dirs') or 0}

    def _library_symbols(self, T, k, b, sc, kinds_px, seeds, inv, entries, f, xs, ys, px, H, symbol, typical):
        """A library is one massif, not a field of bumps: the whole library rises as a plateau
        or range from its edge, and each member is carved from it. Films are mesas of a
        tableland cut by canyons along their borders; PDF collections are the peaks of one
        range, meeting at high saddles. Terms that differ between members vanish at their
        borders; the shared plateau depends only on the library's edge."""
        if T.uniform not in SYMBOL_KINDS or T.library_dist is None:
            return
        code = KIND_CODE.get(T.uniform, -1)
        member = np.array([e is not None and e['label'] in T.members for e in entries])
        child = kinds_px >= 2
        if not child.any():
            return
        summit = member[inv][child]
        kk = k[child]
        side = sc[child]
        bb = b[child]
        u = (xs[kk]-T.x0)/T.cell-0.5
        v = (ys[kk]-T.y0)/T.cell-0.5
        edge = _bilinear(T.library_dist, u, v, T.n)*T.cell
        # The range scales with the library as a whole, not with one member: a library of 400
        # members is a great range, not 400 hills.
        side_T = T.node.get('side', T.size)
        scale = math.sqrt(typical*side_T)
        plateau = smoothstep(0, 0.08*side_T, edge)
        ridged = 1-np.abs(self.G.smooth_fbm(_octave(2.5/scale), 5, 719)[kk])
        fT = f[child]
        # Member detail hands over to the member's own fields up close (shared by all members,
        # so the fade itself raises no step at a border).
        detail = f[child]*(1-smoothstep(60*px, 160*px, typical))
        kind = T.uniform
        if kind == 'video':
            base = 0.016*side_T*plateau
            mesa = smoothstep(0.02*side, 0.09*side, bb)          # flat tops, canyon walls at borders
            H[kk] += (base+0.04*scale*mesa*plateau*detail*summit)*fT
            w = np.maximum(plateau*0.7, mesa*plateau)
        elif kind == 'pdf':
            # One range: crests from ridged noise across the whole library, each member a
            # summit on it (heights vanish at member borders only for the summit term).
            crest = ridged**2
            base = 0.035*side_T*plateau*(0.3+0.7*crest)
            peak = smoothstep(0, 0.45*side, bb)**1.4*(0.5+0.5*crest)
            H[kk] += (base+0.05*side*peak*plateau*detail*summit)*fT
            w = plateau
        else:
            rise = {'images': 0.012, 'archives': 0.03, 'audio': -0.004, 'code': 0.006}.get(kind, 0.0)
            H[kk] += rise*scale*plateau*fT
            w = plateau*0.8
        # Colour follows the plateau alone (it already vanishes at the library's edge), so the
        # range reads as rock even while its heights are still fading in.
        symbol[kk, 0] = np.maximum(symbol[kk, 0], w)
        symbol[kk, 1] = code

    def _symbol_colour(self, symbol, colour, mat, H, px):
        on = symbol[:, 0] > 0.01
        if not on.any():
            return
        k = np.flatnonzero(on)
        w = np.clip(symbol[k, 0]*1.4, 0, 1)
        for c in np.unique(symbol[k, 1]).astype(int):
            sel = symbol[k, 1] == c
            kk, ww = k[sel], w[sel]
            kind = KIND_NAMES[c] if c < len(KIND_NAMES) else 'volcano'
            if kind == 'volcano':
                # Dark basalt flanks; the crater glows (w above 1 marks the crater).
                crater = np.clip(symbol[kk, 0]-1.0, 0, 1)
                col = np.array((58, 54, 52))*(1-crater[:, None])+np.array((230, 96, 40))*crater[:, None]
                m = np.eye(8)[ROCK]
                ww = np.clip(ww, 0, 1)
            elif kind == 'video':
                band = np.sin(H[kk]/(px*4)*math.pi)*0.5+0.5
                col = np.array((164, 98, 66))*(1-band[:, None])+np.array((206, 166, 118))*band[:, None]
                m = np.eye(8)[ROCK]*0.6+np.eye(8)[SAND]*0.4
            elif kind == 'images':
                col = np.array((50, 72, 48))*np.ones((kk.size, 1))
                m = np.eye(8)[FOREST]
            elif kind == 'pdf':
                col = np.array((118, 110, 100))*np.ones((kk.size, 1))
                m = np.eye(8)[ROCK]
            elif kind == 'archives':
                col = np.array((222, 230, 236))*np.ones((kk.size, 1))
                m = np.eye(8)[SNOWM]
            elif kind == 'audio':
                col = np.array((92, 110, 96))*np.ones((kk.size, 1))
                m = np.eye(8)[WET]
            else:
                col = np.array((128, 120, 112))*np.ones((kk.size, 1))
                m = np.eye(8)[TOWN]
            colour[kk, :3] = colour[kk, :3]*(1-ww[:, None])+col*ww[:, None]
            mat[kk] = mat[kk]*(1-ww[:, None])+m[None, :]*ww[:, None]

    # ---------------------------------------------------------------- colour

    def _colour(self, xs, ys, px, H, ridge, water, depth, leaf_kind, leaf_ref, leaves, now, coast, mat, leaf_bd):
        """Colour for every pixel at once, from per-leaf lookup tables."""
        M = xs.size
        L = len(leaves)
        cover_rgb = np.zeros((L, len(COVERS), 3))
        fractions = np.zeros((L, len(COVERS)))
        rock = np.zeros((L, 3))
        snow_amount = np.zeros(L)
        shore = np.zeros((L, 3))
        freq_key = np.zeros(L, dtype=np.int64)
        for i, (T, entry) in enumerate(leaves):
            if entry is not None and T.path == '/':
                zone = self.zone(entry['path'])
            else:
                zone = climate(T.zone, T.writable)
            palette = CLIMATE.get(zone, CLIMATE['native'])
            if entry is not None:
                kinds, newest, side = entry['kinds'], entry['newest'] or entry['mtime'], entry['side']
                kind_bytes = entry.get('kind_bytes')
            else:
                places = T.places
                kinds = {'documents': 1}   # a home district is lowland meadow under its landmarks
                kind_bytes = {}
                for kd, size in zip(places.get('kinds', []), places.get('size', [])):
                    kinds[kd] = kinds.get(kd, 0)+1
                    kind_bytes[kd] = kind_bytes.get(kd, 0)+float(size)
                newest = float(places['mtime'].max()) if places.get('n') else (T.node.get('newest') or 0)
                side = T.node.get('side', T.size)
            fractions[i] = cover_fractions(kinds, kind_bytes)
            for ci, name in enumerate(COVERS):
                cover_rgb[i, ci] = self._cover_rgb(name, palette)
            age = (now-newest)/DAY if newest else 400
            rock[i] = rock_colour(age)
            snow_amount[i] = min(1.0, (age-730)/1500+0.4) if age > 730 else 0.0
            shore[i] = palette['shore']
            freq_key[i] = int(round(math.log2(1/(max(side, 1e-300)*0.13))))
        macro = self.G.smooth_fbm(1/(px*90), 4, 5)
        grain = self.G.fbm(1/(px*5), 3, 11)
        ref = leaf_ref
        # Land: covers form patches (a forest, a field system), each cover's share deciding
        # how much ground it wins. The patch noise is world-absolute per cover, so a forest on
        # one side of a border carries on across it when the neighbour grows forest too.
        land = (leaf_kind == 0) | (leaf_kind == 1)
        rgb = np.zeros((M, 3))
        if land.any():
            li = np.flatnonzero(land)
            fk = freq_key[ref[li]]
            acc = np.zeros((li.size, 3))
            total = np.zeros(li.size)
            wm = np.zeros((li.size, len(COVERS)))
            for key in np.unique(fk):
                sel = fk == key
                pix = li[sel]
                lrefs = ref[pix]
                f1 = 2.0**float(key)
                for ci in range(len(COVERS)):
                    share = fractions[lrefs, ci]
                    if not share.any():
                        continue
                    # fbm, not raw value noise: sharpened value noise shows its square lattice.
                    nz = self.G.smooth_fbm(f1, 4, 500+ci*31)[pix]
                    w = share*np.exp(9.0*nz)
                    acc[sel] += w[:, None]*cover_rgb[lrefs, ci]
                    total[sel] += w
                    wm[sel, ci] = w
            base = acc/np.maximum(total, 1e-9)[:, None]
            m_land = (wm/np.maximum(total, 1e-9)[:, None]) @ COVER_MAT
            base *= (0.93+0.12*macro[li])[:, None]*(0.96+0.08*grain[li])[:, None]
            sand = np.clip(coast[li]*1.2, 0, 1)[:, None]
            base = base*(1-sand)+shore[ref[li]]*sand
            r = np.clip(ridge[li]*1.1, 0, 0.7)[:, None]
            base = base*(1-r)+rock[ref[li]]*r*(0.9+0.2*grain[li][:, None])
            sa = snow_amount[ref[li]]
            snow = np.clip((ridge[li]-0.2)*2.5+grain[li]*0.25, 0, 1)*sa   # snowbound: untouched > 2 years
            base = base*(1-snow[:, None])+SNOW[None, :]*snow[:, None]
            m_land = m_land*(1-sand)+np.eye(8)[SAND][None, :]*sand
            m_land = m_land*(1-r)+np.eye(8)[ROCK][None, :]*r
            m_land = m_land*(1-snow[:, None])+np.eye(8)[SNOWM][None, :]*snow[:, None]
            # An empty folder is a salt flat: a dry lakebed, white and cracked.
            e = self._leaf_empty[ref[li]]
            if e.any():
                le = li[e]
                cracks = np.abs(self.G.smooth(1/(px*18), 6060)[le]-0.5) < 0.035
                base[e] = np.array((232, 226, 212))*(0.97+0.04*grain[le])[:, None]*np.where(cracks, 0.86, 1.0)[:, None]
                m_land[e] = np.eye(8)[SAND]
            # A folder the survey could not open is fenced off: posts along its border,
            # the ground inside greyed and hatched.
            fz = self._leaf_fenced[ref[li]]
            if fz.any():
                lf = li[fz]
                hatch = (np.sin((xs[lf]-ys[lf])/(px*4))*0.5+0.5) > 0.8
                post = (leaf_bd[lf] < 1.6*px) & (self.G.value(0.35/px, 6161)[lf] > 0.45)
                g = base[fz].mean(axis=1, keepdims=True)
                grey = base[fz]*0.35+g*0.65
                grey = grey*np.where(hatch, 0.84, 1.0)[:, None]
                base[fz] = np.where(post[:, None], np.array((70, 58, 46)), grey)
            rgb[li] = base
            mat[li] = m_land
        sea = leaf_kind == 2
        if sea.any():
            d = depth[sea][:, None]
            rgb[sea] = (SEA_SHALLOW*(1-d)**1.5+SEA_DEEP*(1-(1-d)**1.5))*(1+0.05*macro[sea])[:, None]
        unknown = leaf_kind == 3
        if unknown.any():
            # Terra incognita: parchment with survey hatching.
            hatch = np.sin((xs[unknown]+ys[unknown])/(px*5))*0.5+0.5
            rgb[unknown] = PARCHMENT[None, :]*(0.94+0.06*hatch[:, None])
        out = np.zeros((M, 4))
        out[:, :3] = rgb
        out[:, 3] = water*255
        return out

    @staticmethod
    def _cover_rgb(name, palette):
        return {'meadow': palette['meadow'], 'forest': palette['forest'], 'field': palette['field'],
                'town': (138, 130, 118), 'wet': (92, 106, 90), 'ice': (206, 214, 216), 'rock': (118, 114, 106),
                'dry': palette['dry'], 'canyon': (172, 124, 90)}[name]

    def _rain(self, leaf_ref, leaves, M):
        rain = np.zeros(M)
        for ref in np.unique(leaf_ref):
            T, entry = leaves[int(ref)]
            day = entry['day'] if entry else sum(1 for m in T.places.get('mtime', []) if time.time()-m < DAY)
            if day:
                rain[leaf_ref == ref] = min(1.0, math.log1p(day)/math.log(300))
        return rain

    # ---------------------------------------------------------------- water

    @staticmethod
    def _window(bbox, x0, y0, px):
        i0 = int(max(0, math.floor((bbox[0]-x0)/px)))
        i1 = int(min(N-1, math.ceil((bbox[2]-x0)/px)))
        j0 = int(max(0, math.floor((bbox[1]-y0)/px)))
        j1 = int(min(N-1, math.ceil((bbox[3]-y0)/px)))
        return i0, i1, j0, j1

    def _waters(self, xs, ys, px, S, x0, y0, visited, descended, H, water, depth, colour, mat):
        """Lakes and deltas first (they change the ground), then the river network over them."""
        for T in visited.values():
            for lake in T.lakes:
                if 'delta' in lake:
                    if T.continental:
                        self._delta(lake, T, px, x0, y0, H, water, colour, mat)
                else:
                    self._lake(lake, T, px, x0, y0, H, water, depth, colour)
        for T in visited.values():
            inside = descended.get(T.node_id, set())
            for r in T.rivers:
                if r['kind'] == 'upper' and r['child'] in inside:
                    continue   # replaced by that place's own river network
                # Small upper courses stay off the map until they are a few pixels wide:
                # hundreds of one-pixel threads read as scratches, not rivers.
                if r['width'] < px*(2.0 if r['kind'] == 'upper' else 1.2) or r['kind'] == 'delta' and not T.continental:
                    continue
                self._river(r, T, px, S, x0, y0, H, water, colour)
            if T.falls and T.outlet is not None:
                self._falls(T, px, x0, y0, H, water, colour)

    @staticmethod
    def symbol_width(w, px):
        """Rivers are drawn at true width until 8 px, then grow sublinearly with zoom, as on a
        map: a great river stays a river when you zoom into a town on its bank, not a sea."""
        return np.where(w > 6*px, 6*px*(np.maximum(w, 1e-300)/(6*px))**0.28, w)

    def _river(self, r, T, px, S, x0, y0, H, water, colour):
        wmax = float(self.symbol_width(r['width'], px))
        reach = wmax*3
        bx0, by0, bx1, by1 = r['bbox']
        if bx1+reach < x0 or by1+reach < y0 or bx0-reach > x0+S or by0-reach > y0+S:
            return
        p = r['pts']
        w = self.symbol_width(r['w'], px)
        a, b = p[:-1], p[1:]
        wa, wb = w[:-1], w[1:]
        # Meanders at every zoom: the lookup is displaced by noise scaled to the drawn width.
        amp = wmax*1.1
        fq = _octave(1.0/(9*wmax))
        box = (min(bx0, bx1)-reach-amp, min(by0, by1)-reach-amp, max(bx0, bx1)+reach+amp, max(by0, by1)+reach+amp)
        i0, i1, j0, j1 = self._window(box, x0, y0, px)
        if i0 > i1 or j0 > j1:
            return
        ww, hh = i1-i0+1, j1-j0+1
        best = np.full((hh, ww), np.inf)      # signed distance to the bank (negative in water)
        bw = np.zeros((hh, ww))
        wx = (self.G.smooth(fq, 9100).reshape(N, N)[j0:j1+1, i0:i1+1]*2-1)*amp
        wy = (self.G.smooth(fq, 9101).reshape(N, N)[j0:j1+1, i0:i1+1]*2-1)*amp
        for s_ in range(len(a)):
            sw = max(wa[s_], wb[s_])
            rr = sw*3+amp
            si0, si1, sj0, sj1 = self._window((min(a[s_, 0], b[s_, 0])-rr, min(a[s_, 1], b[s_, 1])-rr,
                                              max(a[s_, 0], b[s_, 0])+rr, max(a[s_, 1], b[s_, 1])+rr), x0, y0, px)
            si0, si1, sj0, sj1 = max(si0, i0), min(si1, i1), max(sj0, j0), min(sj1, j1)
            if si0 > si1 or sj0 > sj1:
                continue
            li0, li1, lj0, lj1 = si0-i0, si1-i0+1, sj0-j0, sj1-j0+1
            qx = x0+np.arange(si0, si1+1)[None, :]*px+wx[lj0:lj1, li0:li1]
            qy = y0+np.arange(sj0, sj1+1)[:, None]*px+wy[lj0:lj1, li0:li1]
            dx, dy = b[s_, 0]-a[s_, 0], b[s_, 1]-a[s_, 1]
            L2 = max(dx*dx+dy*dy, 1e-300)
            t = np.clip(((qx-a[s_, 0])*dx+(qy-a[s_, 1])*dy)/L2, 0, 1)
            d = np.hypot(qx-(a[s_, 0]+t*dx), qy-(a[s_, 1]+t*dy))
            wt = wa[s_]+(wb[s_]-wa[s_])*t
            sd = d-wt/2
            cur = best[lj0:lj1, li0:li1]
            better = sd < cur
            cur[better] = sd[better]
            bw[lj0:lj1, li0:li1][better] = wt[better]
        near = np.isfinite(best) & (best < bw*2.5)
        if not near.any():
            return
        jj, ii = np.nonzero(near)
        flat = (jj+j0)*N+(ii+i0)
        sd, wt = best[near], bw[near]
        # An upper course belongs to its child's territory (until that place draws its own
        # network), delta channels to the sea.
        lab, _ = label_at(T, x0+(flat % N)*px, y0+(flat // N)*px, px)
        if r['kind'] == 'upper':
            own = lab == r['child']
        elif r['kind'] == 'gorge':
            own = (lab != SEA) & (lab != OUT)
        elif r['kind'] == 'delta':
            own = (lab == SEA) | (lab == OUT) | (lab == HOME)
        else:
            # Streams run in the valleys between provinces, so they cross borders; they only
            # stay out of the sea.
            own = (lab != SEA) & ((lab != OUT) | (not T.continental))
        if not own.any():
            return
        flat, sd, wt = flat[own], sd[own], wt[own]
        valley = np.clip(1-(sd+wt/2)/(wt*2.5), 0, 1)
        # Rivers fade in as they widen, so small networks never etch the map like cracks.
        show = smoothstep(1.2, 3.5, wt/px)
        if r['kind'] == 'gorge':
            # A slot canyon: a chain of folders each holding one folder. Deep and narrow,
            # with banded sandstone walls.
            gorge = np.clip(1-(sd+wt/2)/(wt*4.0), 0, 1)
            H[flat] -= np.minimum(wt*0.9, px*80)*gorge**1.5*show
            wall = np.clip(gorge*1.6, 0, 1)*(1-np.clip(-sd/px+0.5, 0, 1))*show
            band = np.sin(H[flat]/(px*3))*0.5+0.5
            rock = np.array((178, 112, 72))*(1-band[:, None])+np.array((206, 150, 104))*band[:, None]
            colour[flat, :3] = colour[flat, :3]*(1-wall[:, None])+rock*wall[:, None]
        elif r['kind'] != 'delta':
            H[flat] -= np.minimum(wt*0.18, px*40)*valley**2*show
        wet = np.clip(-sd/px+0.5, 0, 1)*show
        colour[flat, :3] = colour[flat, :3]*(1-wet[:, None])+RIVER[None, :]*wet[:, None]
        bank = np.clip(1-(sd+wt/2)/(wt*1.5), 0, 1)*(1-wet)*show
        colour[flat, :3] *= (1-0.12*bank)[:, None]
        water[flat] = np.maximum(water[flat], wet)
        colour[flat, 3] = np.maximum(colour[flat, 3], wet*255)

    def _lake(self, lake, T, px, x0, y0, H, water, depth, colour):
        R = lake['r']
        if R < px*1.5:
            return
        i0, i1, j0, j1 = self._window((lake['x']-R*1.5, lake['y']-R*1.5, lake['x']+R*1.5, lake['y']+R*1.5), x0, y0, px)
        if i0 > i1 or j0 > j1:
            return
        jj, ii = np.mgrid[j0:j1+1, i0:i1+1]
        flat = (jj*N+ii).ravel()
        qx, qy = x0+ii.ravel()*px, y0+jj.ravel()*px
        # Irregular shores: the lookup is warped, then the outline roughened at finer scales.
        wxl = fbm(qx, qy, 1.5/R, 3, lake['seed'] & 0xFFFF)*R*0.45
        wyl = fbm(qx, qy, 1.5/R, 3, (lake['seed'] >> 16) & 0xFFFF)*R*0.45
        shape = fbm(qx, qy, 5.0/R, 5, (lake['seed'] >> 8) & 0xFFFF)
        d = np.hypot(qx+wxl-lake['x'], qy+wyl-lake['y'])/R+0.25*shape
        lab, _ = label_at(T, qx, qy, px)
        own = lab == HOME
        inside = np.clip((1-d)*R/px+0.5, 0, 1)*own
        shore = np.clip(1-np.abs(d-1)*6, 0, 1)*own*(1-inside)
        H[flat] -= np.minimum(R*0.01, px*4)*smoothstep(0, 0.5, 1-d)*own
        deep = smoothstep(0, 0.6, 1-d)
        col = RIVER*(1-deep[:, None]*0.2)+SEA_DEEP*deep[:, None]*0.2
        colour[flat, :3] = colour[flat, :3]*(1-inside[:, None])+col*inside[:, None]
        colour[flat, :3] = colour[flat, :3]*(1-0.3*shore[:, None])+np.array((184, 176, 140))*0.3*shore[:, None]
        water[flat] = np.maximum(water[flat], inside)
        depth[flat] = np.maximum(depth[flat], deep*inside*0.6)
        colour[flat, 3] = np.maximum(colour[flat, 3], inside*255)

    def _delta(self, lake, T, px, x0, y0, H, water, colour, mat):
        # A delta: the river drops its load where it meets the sea, building a lobed fan of
        # marsh and sand threaded by distributaries, with a turbid plume offshore.
        L = lake['r']
        if L < px*3:
            return
        ax, ay = lake['x'], lake['y']
        dx, dy = lake['delta']
        i0, i1, j0, j1 = self._window((ax-L*2, ay-L*2, ax+L*2, ay+L*2), x0, y0, px)
        if i0 > i1 or j0 > j1:
            return
        jj, ii = np.mgrid[j0:j1+1, i0:i1+1]
        flat = (jj*N+ii).ravel()
        qx, qy = x0+ii.ravel()*px, y0+jj.ravel()*px
        vx, vy = qx-ax, qy-ay
        dist = np.hypot(vx, vy)
        rel = dist/L
        cosang = (vx*dx+vy*dy)/np.maximum(dist, 1e-300)
        n1 = fbm(qx, qy, 4.0/L, 4, lake['seed'] & 0xFFFF)
        lab, _ = label_at(T, qx, qy, px)
        sea = (lab == SEA) | (lab == OUT)
        reach = (0.62+0.3*n1)*(0.3+0.7*np.clip(cosang, 0, 1)**0.8)
        land = np.clip((reach-rel)*L/px+0.5, 0, 1)*sea
        plume = np.clip(1-rel/1.9, 0, 1)*np.clip(cosang+0.3, 0, 1)*sea*(1-land)
        colour[flat, :3] = colour[flat, :3]*(1-0.45*plume[:, None])+np.array((104, 116, 96))*0.45*plume[:, None]
        marsh = np.clip(0.5+n1*1.5, 0, 1)[:, None]
        ground = np.array((168, 160, 118))*(1-marsh)+np.array((110, 120, 84))*marsh
        colour[flat, :3] = colour[flat, :3]*(1-land[:, None])+ground*land[:, None]
        water[flat] = np.minimum(water[flat], 1-land)
        colour[flat, 3] = np.minimum(colour[flat, 3], (1-land)*255)
        H[flat] = np.maximum(H[flat], land*L*0.004)
        mat[flat] = mat[flat]*(1-land[:, None])+(np.eye(8)[WET]*marsh+np.eye(8)[SAND]*(1-marsh))*land[:, None]

    def _falls(self, T, px, x0, y0, H, water, colour):
        # The river leaves one kind of ground for another: white water over a rock ledge.
        w = 0.02*T.node['side']
        if w < px*1.2:
            return
        ox, oy = T.outlet
        i0, i1, j0, j1 = self._window((ox-w*3, oy-w*3, ox+w*3, oy+w*3), x0, y0, px)
        if i0 > i1 or j0 > j1:
            return
        jj, ii = np.mgrid[j0:j1+1, i0:i1+1]
        flat = (jj*N+ii).ravel()
        qx, qy = x0+ii.ravel()*px, y0+jj.ravel()*px
        d = np.hypot(qx-ox, qy-oy)/w
        ledge = np.clip(1-np.abs(d-1.6)/0.6, 0, 1)*(water[flat] < 0.5)
        colour[flat, :3] = colour[flat, :3]*(1-0.5*ledge[:, None])+np.array((96, 92, 88))*0.5*ledge[:, None]
        pool = np.clip((1.3-d)*w/px+0.5, 0, 1)
        foam = pool*np.clip(0.55+0.45*value_noise(qx, qy, 3.0/w, 404), 0, 1)
        colour[flat, :3] = colour[flat, :3]*(1-pool[:, None])+(RIVER*(1-foam[:, None])+np.array((232, 238, 236))*foam[:, None])*pool[:, None]
        water[flat] = np.maximum(water[flat], pool)
        colour[flat, 3] = np.maximum(colour[flat, 3], pool*255)

    # ---------------------------------------------------------------- fields

    def _sites(self, T):
        """Nearest-parcel lookup for a territory's files, rebuilt when its listing changes."""
        pl = T.places
        key = (T.node_id, id(pl))
        hit = self.site_cache.get(key) if hasattr(self, 'site_cache') else None
        if hit is None:
            if not hasattr(self, 'site_cache') or len(self.site_cache) > 256:
                self.site_cache = {}
            from scipy.spatial import cKDTree
            # Companions are drawn as their patch's plain ground: the patch kind, no landmark.
            codes = np.array([KIND_CODE.get(k, KIND_CODE['other']) for k in pl.get('layout_kinds', pl['kinds'])], dtype=np.int64)
            seeds = np.array([stable_hash(p) for p in pl['paths']], dtype=np.uint64)
            landmark = ~np.asarray(pl.get('companion', np.zeros(pl['n'], dtype=bool)))
            roles = np.array([ROLE_CODE.get(r, 0) for r in pl.get('roles', [''] * pl['n'])], dtype=np.int64)
            styles = [LANG_STYLE.get(os.path.splitext(nm)[1].lower()) for nm in pl['names']]
            h20 = (seeds % np.uint64(4)).astype(np.int64)
            style_model = np.array([st[0] if st else HOUSE for st in styles], dtype=np.int64)
            style_roof = np.array([st[1] if st else ROOFS[h20[i]] for i, st in enumerate(styles)], dtype=np.float64)
            hit = (cKDTree(np.column_stack([pl['x'], pl['y']])), codes, (seeds % np.uint64(1 << 20)).astype(np.float64)/(1 << 20),
                   landmark, roles, style_model, style_roof)
            self.site_cache[key] = hit
        return hit

    def _fields(self, xs, ys, px, visited, leaf_kind, leaf_ref, leaves, leaf_bd, fade_in, H, water, colour, mat, now):
        x0f, y0f = xs[0], ys[0]
        Sf = px*(N-1)
        """A folder's own files, drawn as fields: one cohesive patch per kind, one parcel per
        file. Far away a patch is a single forest or field system; closer, it divides into
        parcels (fields with hedgerows, city blocks with streets, peaks of a massif)."""
        home_refs = {}
        for ref in np.unique(leaf_ref[leaf_kind == 1]):
            T, entry = leaves[int(ref)]
            if entry is None:
                home_refs.setdefault(T.node_id, []).append(int(ref))
        for tid, refs in home_refs.items():
            T = visited[tid]
            pl = T.places
            if not pl.get('n'):
                continue
            k = np.flatnonzero((leaf_kind == 1) & np.isin(leaf_ref, refs))
            if k.size == 0:
                continue
            tree, codes, hue, landmark, roles, style_model, style_roof = self._sites(T)
            age_file = (now-pl['mtime'])/DAY
            self._file_landforms(T, pl, roles, x0f, y0f, Sf, px, now)
            R = pl['r']
            rmean = float(np.median(R))
            if rmean < px*0.25:
                continue   # parcels far below a pixel: the home district's cover says it all
            n = pl['n']
            amp = rmean*0.3
            fq = _octave(0.9/rmean)
            qx = xs[k]+(self.G.value(fq, 811)[k]*2-1)*amp
            qy = ys[k]+(self.G.value(fq, 812)[k]*2-1)*amp
            if n == 1:
                d1, i1 = tree.query(np.column_stack([qx, qy]), workers=1)
                d2 = np.full(k.size, np.inf)
                i2 = i1
            else:
                dd, ii = tree.query(np.column_stack([qx, qy]), k=2, workers=1)
                d1, d2, i1, i2 = dd[:, 0], dd[:, 1], ii[:, 0], ii[:, 1]
            sx, sy = pl['x'], pl['y']
            gap = np.hypot(sx[i2]-sx[i1], sy[i2]-sy[i1])
            e = np.where(n > 1, (d2**2-d1**2)/(2*np.maximum(gap, 1e-300)), np.inf)   # distance to the parcel edge
            r1 = R[i1]
            rf = pl['rf'][i1] if 'rf' in pl else r1
            er = e/r1
            rel = d1/rf                                           # landmark scale (peaks, mesas)
            other_patch = pl['patch'][i2] != pl['patch'][i1] if n > 1 else np.zeros(k.size, dtype=bool)
            pe = np.where(other_patch, er, np.inf)               # distance to the patch edge, in parcels
            detail = smoothstep(1.5, 5.0, r1/px)                 # parcels large enough to show
            fine = smoothstep(12.0, 40.0, r1/px)                 # furrows, roofs, crowns
            # Heights vanish at the home district's border and at every patch edge.
            hf = fade_in[k]*smoothstep(0, min(0.6*rmean, 0.5*T.cell), leaf_bd[k])*smoothstep(0, 0.25, pe)
            edge_soft = smoothstep(0.0, 0.18, pe+0.08*(self.G.value(_octave(3/rmean), 813)[k]-0.5))
            age = (now-pl['mtime'][i1])/DAY
            prom = (0.6+0.4*np.clip(np.log10(pl['size'][i1]+1)/9, 0, 1))*landmark[i1]
            tone = hue[i1]
            n1 = self.G.smooth_fbm(_octave(3.0/rmean), 3, 815)[k]
            code = codes[i1]
            base = colour[k, :3].copy()
            out = base.copy()
            h = np.zeros(k.size)
            m = np.zeros((k.size, 8))
            wet = np.zeros(k.size)
            vary = (0.9+0.2*tone)[:, None]
            for c in np.unique(code):
                s_ = code == c
                kind = KIND_NAMES[c]
                rr, ee, tt = rel[s_], er[s_], tone[s_]
                if kind == 'pdf':
                    # A range, not a field of bumps: the whole patch rises as one massif from
                    # its foothills, ridged along its length, and each PDF is a summit on it
                    # (broad, so neighbouring summits meet at high saddles). Rock by age.
                    sharp = np.where(age[s_] < 180, 1.6, np.where(age[s_] < 1095, 1.0, 0.7))
                    rp = d1[s_]/r1[s_]
                    peak = np.clip(1-rp/1.9, 0, 1)**sharp*landmark[i1][s_]
                    ridge = (1-np.abs(self.G.smooth_fbm(_octave(1.2/rmean), 4, 1221)[k][s_]))**2
                    massif = smoothstep(0, 2.2, pe[s_])
                    h[s_] = r1[s_]*massif*(0.12+0.16*ridge)+r1[s_]*0.2*prom[s_]*peak*massif
                    rock = ROCK_LUT[np.searchsorted(ROCK_LIMITS, age[s_])]
                    mix = np.clip(0.35+peak*1.6, 0, 1)[:, None]
                    col = base[s_]*(1-mix)+rock*mix*(0.9+0.2*(n1[s_, None]*0.5+0.5))
                    snow = (np.clip((peak-0.45)*3, 0, 1)*(age[s_] > 730))[:, None]
                    out[s_] = col*(1-snow)+SNOW*snow
                    m[s_, ROCK] = mix[:, 0]*(1-snow[:, 0])
                    m[s_, MEADOW] = 1-mix[:, 0]
                    m[s_, SNOWM] = snow[:, 0]
                elif kind == 'images':
                    # A forest: stands of different tone, crowns showing up close.
                    crown = self.G.value(_octave(7.0/rmean), 4242)[k][s_]
                    canopy = np.array((52, 76, 50))*vary[s_]*(0.72+0.45*crown*fine[s_]+0.28*(1-fine[s_]))[:, None]
                    # The season is the age: this year's photographs in leaf, last year's in
                    # autumn colours (older ones lie under snow, below).
                    fall = smoothstep(300, 600, age[s_])[:, None]*(1-smoothstep(700, 760, age[s_]))[:, None]
                    turned = AUTUMN[(tt*3).astype(np.int64) % 3]*(0.8+0.3*crown[:, None])
                    out[s_] = canopy*(1-fall)+turned*fall
                    # Camera originals grow as old oaks, screenshots as shrubs.
                    rc = roles[i1][s_]
                    self._species[k[s_][rc == ROLE_CODE['oak']]] = OAK
                    self._species[k[s_][rc == ROLE_CODE['shrub']]] = SHRUB
                    h[s_] = r1[s_]*0.05*(0.6+0.8*crown*fine[s_])
                    m[s_, FOREST] = 1
                elif kind == 'video':
                    # Canyon country: the patch is one tableland of banded strata (film is banded
                    # in frames), each video a mesa of it, with narrow canyons between them.
                    # A large file stands a little taller than a small one.
                    table = smoothstep(0, 1.6, pe[s_])
                    top = smoothstep(0.03, 0.14, ee+0.03*n1[s_])*landmark[i1][s_]*table
                    hh = r1[s_]*(0.22*table+0.16*prom[s_]*top)
                    h[s_] = hh
                    band = np.sin(hh/(r1[s_]*0.02)*math.pi)*0.5+0.5
                    strata = np.array((158, 92, 64))*(1-band[:, None])+np.array((204, 164, 116))*band[:, None]
                    flat_top = smoothstep(0.9, 1.0, top)[:, None]
                    ground = np.array((188, 150, 108))
                    col = ground*(1-top[:, None])+strata*top[:, None]
                    out[s_] = col*(1-flat_top*0.5)+np.array((170, 118, 82))*flat_top*0.5
                    m[s_, SAND] = 1-top
                    m[s_, ROCK] = top
                elif kind == 'audio':
                    # Wetland: reed beds and standing pools.
                    pond = self.G.value(_octave(4.0/rmean), 3131)[k][s_]
                    pool = np.clip((pond-0.62)*r1[s_]/px*0.3+0.5, 0, 1)*detail[s_]*(d1[s_]/r1[s_] < 0.95)
                    reeds = np.array((118, 120, 76))*vary[s_]*(0.9+0.2*n1[s_, None])
                    out[s_] = reeds*(1-pool[:, None])+(RIVER*0.8+np.array((90, 100, 70))*0.2)*pool[:, None]
                    wet[s_] = pool
                    h[s_] = -r1[s_]*0.01*pool
                    m[s_, WET] = 1
                elif kind == 'tables':
                    # A field system: crops per parcel, furrows in each field's own direction,
                    # hedgerows between.
                    crop = CROPS[(tt*len(CROPS)).astype(np.int64) % len(CROPS)]
                    ang = tt*math.pi
                    ph = ((xs[k][s_]-sx[i1][s_])*np.cos(ang)+(ys[k][s_]-sy[i1][s_])*np.sin(ang))/(r1[s_]*0.08)
                    furrow = (np.sin(ph*math.tau)*0.5+0.5)*fine[s_]
                    col = crop*(0.94+0.1*furrow[:, None])
                    hedge = (1-smoothstep(0.03, 0.08, ee))*detail[s_]
                    out[s_] = col*(1-hedge[:, None])+np.array((66, 84, 52))*hedge[:, None]
                    h[s_] = r1[s_]*0.02*hedge
                    m[s_, FIELD] = 1-hedge
                    m[s_, FOREST] = hedge
                elif kind in ('code', 'databases', 'industry'):
                    # A town: one block per file, streets between, roofs up close. Buildings
                    # follow what the file does (see _lot_models); an industrial quarter holds
                    # build output and model weights.
                    street = (1-smoothstep(0.05, 0.1, ee))*detail[s_]
                    ang = tt*math.pi/2
                    lx, ly = xs[k][s_]-sx[i1][s_], ys[k][s_]-sy[i1][s_]
                    u = (lx*np.cos(ang)+ly*np.sin(ang))/(r1[s_]*0.16)
                    v = (-lx*np.sin(ang)+ly*np.cos(ang))/(r1[s_]*0.16)
                    fu, fv = u-np.floor(u), v-np.floor(v)
                    lot = lattice(np.floor(u).astype(np.int64), np.floor(v).astype(np.int64), 991+c)
                    # Houses fill each block, denser toward its heart, with gardens between.
                    size = 0.26+0.14*lot
                    rc = roles[i1][s_]
                    single = (rc == ROLE_CODE['hall']) | (rc == ROLE_CODE['power'])
                    bld = ((np.abs(fu-0.5) < size) & (np.abs(fv-0.5) < size*0.8) & (d1[s_]/r1[s_] < 0.88) & (lot > 0.2+0.5*d1[s_]/r1[s_]) & ~single).astype(np.float64)*fine[s_]
                    # Painted roofs only while a lot is small on screen; beyond that the 3D
                    # houses (symbol-sized) stand in for them.
                    painted = bld*(1-smoothstep(10.0, 16.0, r1[s_]*0.16/px))
                    roof = style_roof[i1][s_]
                    ground = (112, 106, 98) if kind == 'industry' else (134, 126, 116)
                    town = np.array(ground)*(0.94+0.12*n1[s_, None])
                    # A square before the town hall.
                    town = np.where(single[:, None] & (d1[s_]/r1[s_] < 0.6)[:, None], np.array((170, 160, 140)), town)
                    col = town*(1-painted[:, None])+roof*painted[:, None]
                    out[s_] = col*(1-street[:, None])+np.array((104, 102, 98))*street[:, None]
                    if T.repo and kind == 'code':
                        # A git repository is a walled town: a pale stone rampart round the town,
                        # shadowed on its outer side, with towers along it (3D, see below).
                        edge_px = np.minimum(pe[s_]*r1[s_], leaf_bd[k][s_])/px
                        wall = (edge_px < 2.2) & (edge_px > 0.4)
                        shadow = edge_px <= 0.4
                        out[s_] = np.where(wall[:, None], np.array((206, 196, 172)), np.where(shadow[:, None], np.array((82, 76, 68)), out[s_]))
                        h[s_] += np.where(wall, 1.5*px, 0.0)
                        wk = k[s_][wall]
                        if wk.size:
                            cell = (wk // N)//6*1000+(wk % N)//6
                            _, first = np.unique(cell, return_index=True)
                            wk = wk[first][::2]
                            self._lots.append(np.column_stack([xs[wk], ys[wk], np.full(wk.size, 1.6*SP*px), np.zeros(wk.size),
                                                               np.full((wk.size, 3), (196, 186, 164)), np.full(wk.size, WALL_TOWER), np.ones(wk.size)]))
                    # No building heights in the terrain: the houses are 3D models (see
                    # _instances); raised lots became pillars on wide parcels.
                    m[s_, TOWN] = 1
                    # The same lots become 3D houses, lined up with their streets.
                    pix = k[s_]
                    self._planned[pix[fine[s_] > 0.3]] = True
                    on = bld > 0.5
                    if on.any():
                        fu0, fv0 = np.floor(u[on]), np.floor(v[on])
                        key = np.stack([i1[s_][on], fu0, fv0], axis=1)
                        _, first = np.unique(key, axis=0, return_index=True)
                        cu, cv = fu0[first]+0.5, fv0[first]+0.5
                        rr_ = r1[s_][on][first]*0.16
                        an = ang[on][first]
                        wx_ = sx[i1][s_][on][first]+(cu*np.cos(an)-cv*np.sin(an))*rr_
                        wy_ = sy[i1][s_][on][first]+(cu*np.sin(an)+cv*np.cos(an))*rr_
                        fi = i1[s_][on][first]
                        model, tint = self._lot_models(fi, roles, style_model, style_roof, age_file)
                        lot_size = np.clip(2*size[on][first]*rr_*1.4, 2.5*px, 3.0*SP*px)
                        # Big towns rise toward their centre: blocks, then towers.
                        lift = self._skyline(pl, fi, wx_, wy_, lot[on][first])
                        model = np.where((lift > 2.4) & (model != RUIN), TOWER, np.where((lift > 1.4) & (model != RUIN), BLOCK, model))
                        tint = np.where((lift > 1.4)[:, None] & (model != RUIN)[:, None], CITY_TINTS[fi % len(CITY_TINTS)], tint)
                        self._lots.append(np.column_stack([wx_, wy_, lot_size, an, tint, model, lift]))
                        # The painted city centre (zoomed out) makes way for these lots.
                        self._city_done.add((T.node_id, int(pl['patch'][fi[0]])))
                elif kind == 'archives':
                    # A glacier: one ice body, crevassed between files.
                    crev = (1-smoothstep(0.02, 0.06, ee))*detail[s_]
                    streak = 0.93+0.07*np.sin(n1[s_]*9)
                    out[s_] = np.array((222, 230, 236))*streak[:, None]*(1-crev[:, None]*0.3)+np.array((120, 146, 170))*crev[:, None]*0.3
                    h[s_] = r1[s_]*0.08*smoothstep(0, 0.6, pe[s_])*(1-crev*0.3)
                    m[s_, SNOWM] = 1
                elif kind == 'binaries':
                    # Obsidian: dark volcanic glass in blocky flows.
                    shard = np.clip(1-rr/0.85, 0, 1)**1.5*(0.6+0.4*np.abs(n1[s_]))*landmark[i1][s_]
                    out[s_] = np.array((88, 84, 92))*(0.85+0.4*shard[:, None])
                    h[s_] = rf[s_]*0.12*shard*smoothstep(0, 0.1, ee)
                    m[s_, ROCK] = 1
                elif kind == 'disks':
                    rim = np.exp(-((rr-0.7)/0.15)**2)*landmark[i1][s_]
                    inner = np.clip((0.55-rr)*rf[s_]/px+0.5, 0, 1)*landmark[i1][s_]
                    col = base[s_]*(1-rim[:, None]*0.7)+np.array((96, 76, 66))*rim[:, None]*0.7
                    out[s_] = col*(1-inner[:, None])+SEA_SHALLOW*inner[:, None]
                    h[s_] = (rim*0.18-inner*0.04)*rf[s_]*smoothstep(0, 0.1, ee)
                    wet[s_] = inner
                    m[s_, ROCK] = 1-inner
                elif kind == 'documents':
                    # Meadow in flower, dry-stone walls between.
                    flowers = (self.G.value(_octave(20.0/rmean), 5151)[k][s_] > 0.78)*fine[s_]
                    wall = (1-smoothstep(0.02, 0.05, ee))*detail[s_]*0.6
                    col = np.array((136, 146, 94))*vary[s_]
                    col = col*(1-flowers[:, None]*0.6)+np.array((214, 196, 110))*flowers[:, None]*0.6
                    out[s_] = col*(1-wall[:, None])+np.array((156, 150, 134))*wall[:, None]
                    m[s_, MEADOW] = 1
                else:
                    # Scrub with a cairn at each file.
                    shrub = (self.G.value(_octave(14.0/rmean), 6161)[k][s_] > 0.7)*fine[s_]
                    cairn = np.clip((0.12-rr)*rf[s_]/px+0.5, 0, 1)*detail[s_]*landmark[i1][s_]*(roles[i1][s_] != ROLE_CODE['arch'])
                    col = np.array((150, 142, 110))*(0.97+0.06*tt[:, None])
                    col = col*(1-shrub[:, None]*0.5)+np.array((96, 104, 72))*shrub[:, None]*0.5
                    out[s_] = col*(1-cairn[:, None])+np.array((168, 164, 152))*cairn[:, None]
                    h[s_] = rf[s_]*0.05*cairn
                    m[s_, SAND] = 0.6
                    m[s_, MEADOW] = 0.4
            # Dormant files lie under snow, as whole regions do.
            # Soft at parcel edges and patchy within, so it reads as snow cover, not as tiles.
            snowy = (np.clip((age-730)/1500+0.3, 0, 0.6)*(age > 730)*np.clip(0.2+1.2*n1, 0, 1)*(wet < 0.5)
                     * smoothstep(0.0, 0.35, np.minimum(er, 1.0)))
            out = out*(1-snowy[:, None])+SNOW*snowy[:, None]
            m = m*(1-snowy[:, None])+np.eye(8)[SNOWM]*snowy[:, None]
            a = edge_soft
            colour[k, :3] = base*(1-a[:, None])+out*a[:, None]
            mat[k] = mat[k]*(1-a[:, None])+m*a[:, None]
            H[k] += h*hf
            wa = wet*a
            water[k] = np.maximum(water[k], wa)
            colour[k, 3] = np.maximum(colour[k, 3], wa*255)

    # ---------------------------------------------------------------- contours

    def _contours(self, H, water, colour, px):
        # Faint index contours (topographic-map legibility), spaced relative to the view.
        spacing = px*14
        phase = (H/spacing) % 1.0
        line = np.clip(1-np.minimum(phase, 1-phase)*spacing/(px*0.9), 0, 1)*(water < 0.5)
        colour[:, :3] *= (1-0.1*line)[:, None]
        return colour


def _volcanic(c) -> bool:
    """Most of a sizeable folder changed this week."""
    return (c.get('week') or 0) >= 200 and (c.get('week') or 0) >= 0.5*max(c.get('files') or 0, 1)


def encode(tile: dict) -> bytes:
    """Binary tile: header (magic, n, level, x, y, base) then float32 heights, RGBA colour,
    RGBA aux (rain, fog, depth, ridge), two RGBA material-weight maps (MATERIALS order), and
    the 3D instances: a uint32 count then INSTANCE records (tile-relative u, v, height/S,
    size/S as float32; kind, yaw/256 turn, RGB tint as bytes)."""
    head = struct.pack('<4sIIqqd', b'BTL3', N, tile['level'], tile['x'], tile['y'], tile['base'])
    inst = tile.get('instances')
    if inst is None:
        inst = np.zeros(0, dtype=INSTANCE)
    return (head + tile['height'].astype('<f4').tobytes() + tile['colour'].tobytes() + tile['aux'].tobytes()
            + tile['mat_a'].tobytes() + tile['mat_b'].tobytes() + struct.pack('<I', len(inst)) + inst.tobytes())
