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
import struct
import time
from functools import lru_cache

import numpy as np

from .index import NO_CRAWL

from .world import FIRST_CHILD, HOME, OUT, SEA, GridNoise, World, _bilinear, fbm, label_at, lattice, sea_distance, stable_hash, value_noise

N = 257
DAY = 86400.0

# Muted, satellite-like palettes per climate (the mount), MSI-restrained.
CLIMATE = {
    'native':    {'meadow': (122, 128, 94),  'dry': (146, 142, 112), 'forest': (72, 88, 64),  'field': (150, 146, 106), 'shore': (190, 182, 152)},
    'windows':   {'meadow': (108, 122, 84),  'dry': (134, 136, 100), 'forest': (60, 82, 56),  'field': (146, 144, 98),  'shore': (192, 184, 150)},
    'network':   {'meadow': (110, 120, 100), 'dry': (126, 130, 110), 'forest': (66, 82, 70),  'field': (132, 136, 110), 'shore': (170, 170, 150)},
    'ephemeral': {'meadow': (184, 170, 136), 'dry': (198, 184, 148), 'forest': (146, 136, 100), 'field': (190, 172, 132), 'shore': (208, 196, 164)},
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
KIND_NAMES = ('pdf', 'images', 'video', 'audio', 'tables', 'code', 'databases', 'archives', 'binaries', 'disks', 'documents', 'other')
KIND_CODE = {k: i for i, k in enumerate(KIND_NAMES)}
# Muted crops (wheat, young green, dark green, ploughed, stubble) and roofs (terracotta,
# slate, limestone, weathered); database towns are slate-blue.
CROPS = np.array([(176, 160, 92), (128, 142, 74), (96, 116, 62), (132, 106, 78), (164, 152, 112)], dtype=np.float64)
ROOFS = np.array([(152, 98, 78), (124, 122, 120), (170, 158, 140), (112, 104, 96)], dtype=np.float64)
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


def cover_fractions(kinds: dict) -> np.ndarray:
    f = np.zeros(len(COVERS))
    total = 0
    for kind, count in (kinds or {}).items():
        cover = COVER_OF.get(kind, 'dry')
        f[COVERS.index(cover)] += math.log1p(count)   # diversity shows even when one kind dominates
        total += 1
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
                    depth[k] = smoothstep(0, 0.08*side_T, np.minimum(bd[m], sea_distance(T, xs[k], ys[k])) if T.continental else bd[m])
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
                        bs = sea_distance(T, xs[k], ys[k])
                        shore = smoothstep(0, 0.09*typical*2, bs)
                        hills = self.G.smooth_fbm(_octave(1.1/typical), 5, 300)[k]
                        H[k] += typical*(0.02*shore + 0.016*shore*(hills+0.25))*f
                        coast[k] = np.maximum(coast[k], (1-smoothstep(0, 0.024*typical, bs))*f)
                        inland = b < bs*0.999
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
                        fade_in[kk] = f[sel]*smoothstep(24*px, 90*px, side_l[li])*smoothstep(0, 0.12*side_l[li], b_child[sel])
                        next_ids.append(np.full(kk.size, inside.node_id, dtype=np.int64))
                        next_idx.append(kk)
                    else:
                        leaves.append((T, entry))
                        leaf_lut[li] = len(leaves)-1
                leafy = leaf_lut[sub_inv] >= 0
                if leafy.any():
                    kk = k[leafy]
                    leaf_ref[kk] = leaf_lut[sub_inv[leafy]]
                    # Virtual filesystems (/proc, /sys…) are never crawled on purpose: they are
                    # desert, not terra incognita.
                    surveyed = np.array([bool(e and (e['scanned'] or e['files'] or e['dirs'] or e['path'] in NO_CRAWL)) for e in entries])
                    leaf_kind[kk] = np.where(surveyed[sub_inv[leafy]], 0, 3)
            if next_idx:
                active = np.concatenate(next_idx)
                current[active] = np.concatenate(next_ids)
            else:
                active = np.array([], dtype=np.int64)
        lap('descent')
        # Fractal detail, self-similar in world units (octaves from 64 tiles down to 2 px).
        kmin = max(0, int(math.floor(math.log2(1/(64*S)))))
        kmax = int(math.floor(math.log2(1/(2*px))))
        land = water < 0.5
        detail = np.zeros(M)
        for k in range(kmin, kmax+1):
            f = 2.0**k
            fade = 1.0 if k < kmax else 0.5
            detail += (self.G.smooth(f, 7000+k)*2-1)*(0.0022/f)*fade
        H += detail*land

        lap('detail')
        mat = np.zeros((M, len(MATERIALS)))
        colour = self._colour(xs, ys, px, H, ridge, water, depth, leaf_kind, leaf_ref, leaves, now, coast, mat)
        lap('colour')
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
        mat = np.clip(mat*255, 0, 255).astype(np.uint8)
        return {'level': level, 'x': tx, 'y': ty, 'base': base,
                'height': (H-base).astype(np.float32).reshape(N, N),
                'colour': np.clip(colour, 0, 255).astype(np.uint8).reshape(N, N, 4),
                'aux': np.stack([np.clip(rain*255, 0, 255), np.clip(fog*255, 0, 255), np.clip(depth*255, 0, 255), np.clip(ridge*255, 0, 255)], axis=1).astype(np.uint8).reshape(N, N, 4),
                'mat_a': mat[:, :4].reshape(N, N, 4).copy(), 'mat_b': mat[:, 4:].reshape(N, N, 4).copy()}

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
    def _aggregate(T, side_T):
        node = T.node
        return {'path': T.path, 'kinds': json.loads(node['kinds']) if node.get('kinds') else {}, 'newest': node.get('newest') or 0,
                'mtime': node.get('mtime') or 0, 'side': side_T*0.25, 'day': node.get('day') or 0, 'scanned': True,
                'files': node.get('files') or 0, 'dirs': node.get('dirs') or 0}

    # ---------------------------------------------------------------- colour

    def _colour(self, xs, ys, px, H, ridge, water, depth, leaf_kind, leaf_ref, leaves, now, coast, mat):
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
                zone = T.zone if T.writable else 'alpine'
            palette = CLIMATE.get(zone, CLIMATE['native'])
            if entry is not None:
                kinds, newest, side = entry['kinds'], entry['newest'] or entry['mtime'], entry['side']
            else:
                places = T.places
                kinds = {'documents': 1}   # a home district is lowland meadow under its landmarks
                for kd in places.get('kinds', []):
                    kinds[kd] = kinds.get(kd, 0)+1
                newest = float(places['mtime'].max()) if places.get('n') else (T.node.get('newest') or 0)
                side = T.node.get('side', T.size)
            fractions[i] = cover_fractions(kinds)
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
            rgb[li] = base
            m_land = m_land*(1-sand)+np.eye(8)[SAND][None, :]*sand
            m_land = m_land*(1-r)+np.eye(8)[ROCK][None, :]*r
            m_land = m_land*(1-snow[:, None])+np.eye(8)[SNOWM][None, :]*snow[:, None]
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
        if r['kind'] != 'delta':
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
            codes = np.array([KIND_CODE.get(k, KIND_CODE['other']) for k in pl['kinds']], dtype=np.int64)
            seeds = np.array([stable_hash(p) for p in pl['paths']], dtype=np.uint64)
            hit = (cKDTree(np.column_stack([pl['x'], pl['y']])), codes, (seeds % np.uint64(1 << 20)).astype(np.float64)/(1 << 20))
            self.site_cache[key] = hit
        return hit

    def _fields(self, xs, ys, px, visited, leaf_kind, leaf_ref, leaves, leaf_bd, fade_in, H, water, colour, mat, now):
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
            tree, codes, hue = self._sites(T)
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
            prom = 0.6+0.4*np.clip(np.log10(pl['size'][i1]+1)/9, 0, 1)
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
                    # A massif: each PDF a peak, saddles between them; rock by age.
                    sharp = np.where(age[s_] < 180, 1.6, np.where(age[s_] < 1095, 1.0, 0.7))
                    peak = np.clip(1-rr/1.15, 0, 1)**sharp
                    h[s_] = r1[s_]*0.18*smoothstep(0, 0.5, pe[s_])+rf[s_]*0.45*prom[s_]*peak*smoothstep(0, 0.15, ee)
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
                    out[s_] = canopy
                    h[s_] = r1[s_]*0.05*(0.6+0.8*crown*fine[s_])
                    m[s_, FOREST] = 1
                elif kind == 'video':
                    # Canyon country: each video a mesa of banded strata, as film is banded in
                    # frames; a large file is a broad, tall mesa, a small one a butte.
                    top = smoothstep(0.72, 0.52, rr+0.12*n1[s_])
                    hh = rf[s_]*0.3*prom[s_]*top
                    h[s_] = hh*smoothstep(0, 0.1, ee)
                    band = np.sin(hh/(rf[s_]*0.025)*math.pi)*0.5+0.5
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
                elif kind in ('code', 'databases'):
                    # A town: one block per file, streets between, roofs up close.
                    street = (1-smoothstep(0.05, 0.1, ee))*detail[s_]
                    ang = tt*math.pi/2
                    lx, ly = xs[k][s_]-sx[i1][s_], ys[k][s_]-sy[i1][s_]
                    u = (lx*np.cos(ang)+ly*np.sin(ang))/(r1[s_]*0.16)
                    v = (-lx*np.sin(ang)+ly*np.cos(ang))/(r1[s_]*0.16)
                    fu, fv = u-np.floor(u), v-np.floor(v)
                    lot = lattice(np.floor(u).astype(np.int64), np.floor(v).astype(np.int64), 991+c)
                    # Houses fill each block, denser toward its heart, with gardens between.
                    size = 0.26+0.14*lot
                    bld = ((np.abs(fu-0.5) < size) & (np.abs(fv-0.5) < size*0.8) & (d1[s_]/r1[s_] < 0.88) & (lot > 0.2+0.5*d1[s_]/r1[s_])).astype(np.float64)*fine[s_]
                    roofs = ROOFS_DB if kind == 'databases' else ROOFS
                    roof = roofs[(lot*len(roofs)).astype(np.int64) % len(roofs)]
                    town = np.array((134, 126, 116))*(0.94+0.12*n1[s_, None])
                    col = town*(1-bld[:, None])+roof*bld[:, None]
                    out[s_] = col*(1-street[:, None])+np.array((104, 102, 98))*street[:, None]
                    h[s_] = r1[s_]*0.03*(0.8+lot)*bld
                    m[s_, TOWN] = 1
                elif kind == 'archives':
                    # A glacier: one ice body, crevassed between files.
                    crev = (1-smoothstep(0.02, 0.06, ee))*detail[s_]
                    streak = 0.93+0.07*np.sin(n1[s_]*9)
                    out[s_] = np.array((222, 230, 236))*streak[:, None]*(1-crev[:, None]*0.3)+np.array((120, 146, 170))*crev[:, None]*0.3
                    h[s_] = r1[s_]*0.08*smoothstep(0, 0.6, pe[s_])*(1-crev*0.3)
                    m[s_, SNOWM] = 1
                elif kind == 'binaries':
                    # Obsidian: dark volcanic glass in blocky flows.
                    shard = np.clip(1-rr/0.85, 0, 1)**1.5*(0.6+0.4*np.abs(n1[s_]))
                    out[s_] = np.array((88, 84, 92))*(0.85+0.4*shard[:, None])
                    h[s_] = rf[s_]*0.12*shard*smoothstep(0, 0.1, ee)
                    m[s_, ROCK] = 1
                elif kind == 'disks':
                    rim = np.exp(-((rr-0.7)/0.15)**2)
                    inner = np.clip((0.55-rr)*rf[s_]/px+0.5, 0, 1)
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
                    cairn = np.clip((0.12-rr)*rf[s_]/px+0.5, 0, 1)*detail[s_]
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


def encode(tile: dict) -> bytes:
    """Binary tile: header (magic, n, level, x, y, base) then float32 heights, RGBA colour,
    RGBA aux (rain, fog, depth, ridge) and two RGBA material-weight maps (MATERIALS order)."""
    head = struct.pack('<4sIIqqd', b'BTL2', N, tile['level'], tile['x'], tile['y'], tile['base'])
    return (head + tile['height'].astype('<f4').tobytes() + tile['colour'].tobytes() + tile['aux'].tobytes()
            + tile['mat_a'].tobytes() + tile['mat_b'].tobytes())
