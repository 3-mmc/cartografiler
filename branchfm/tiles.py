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

from .world import FIRST_CHILD, HOME, OUT, SEA, GridNoise, World, fbm, label_at, sea_distance, stable_hash, value_noise

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
RIVER = np.array((52, 98, 108), dtype=np.float64)
SNOW = np.array((236, 238, 236), dtype=np.float64)
PARCHMENT = np.array((206, 194, 162), dtype=np.float64)

# Land cover from content: what a place holds decides what grows there.
COVER_OF = {'images': 'forest', 'video': 'forest', 'tables': 'field', 'code': 'town', 'databases': 'town',
            'pdf': 'meadow', 'documents': 'meadow', 'audio': 'wet', 'archives': 'ice', 'binaries': 'rock',
            'disks': 'rock', 'other': 'dry', 'folders': 'dry'}
COVERS = ('meadow', 'forest', 'field', 'town', 'wet', 'ice', 'rock', 'dry')


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
                        hills = self.G.fbm(_octave(1.1/typical), 5, 300)[k]
                        H[k] += typical*(0.02*shore + 0.016*shore*(hills+0.25))*f
                        coast[k] = np.maximum(coast[k], (1-smoothstep(0, 0.024*typical, bs))*f)
                        inland = b < bs*0.999
                    else:
                        # Provinces rise a little above the parent's basin (the home district is
                        # the basin); divides come and go.
                        child = pk[relief] != 1
                        H[k[child]] += (0.004*sc*smoothstep(0, 0.25*sc, b))[child]*f[child]
                        inland = np.ones(k.size, dtype=bool)
                        show = smoothstep(300, 900, side_T/px)*(1-smoothstep(3000, 9000, side_T/px))
                        if show > 0:
                            dots = (self.G.value(0.25/px, 77)[k] > 0.45)
                            border_line[k] = np.maximum(border_line[k], np.clip(1.0-b/px, 0, 1)*show*dots*0.6)
                    if inland.any():
                        ki = k[inland]
                        gate = np.clip((1.0-np.abs(self.G.fbm(_octave(2.0/typical), 4, 400)[ki]))*2.0-1.1, 0, 1)
                        rr = np.exp(-(b[inland]/(0.07*typical))**2)*gate
                        H[ki] += 0.02*typical*rr*f[inland]
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
            detail += (self.G.value(f, 7000+k)*2-1)*(0.0022/f)*fade
        H += detail*land

        lap('detail')
        colour = self._colour(xs, ys, px, H, ridge, water, depth, leaf_kind, leaf_ref, leaves, now, coast)
        lap('colour')
        rain = self._rain(leaf_ref, leaves, M)
        fog = (leaf_kind == 3).astype(np.float64)
        lap('rain')
        self._rivers(xs, ys, px, S, x0, y0, visited, descended, H, water, colour)
        lap('rivers')
        self._landmarks(x0, y0, px, visited, leaf_kind, leaf_ref, leaves, H, water, colour, now)
        lap('landmarks')
        colour = self._contours(H, water, colour, px)
        lap('contours')
        colour[:, :3] = colour[:, :3]*(1-0.35*border_line[:, None]) + np.array([70, 60, 45])*0.35*border_line[:, None]

        base = float(H.min())
        return {'level': level, 'x': tx, 'y': ty, 'base': base,
                'height': (H-base).astype(np.float32).reshape(N, N),
                'colour': np.clip(colour, 0, 255).astype(np.uint8).reshape(N, N, 4),
                'aux': np.stack([np.clip(rain*255, 0, 255), np.clip(fog*255, 0, 255), np.clip(depth*255, 0, 255), np.clip(ridge*255, 0, 255)], axis=1).astype(np.uint8).reshape(N, N, 4)}

    def _grouped_fbm(self, k, side, scale, octaves, base_seed, seed_class):
        """Noise with wavelength proportional to each place's size, from shared tile fields."""
        freq_key = np.round(np.log2(scale/side)).astype(np.int64)
        out = np.empty(k.size)
        for fk in np.unique(freq_key):
            for sc in np.unique(seed_class[freq_key == fk]):
                sel = (freq_key == fk) & (seed_class == sc)
                out[sel] = self.G.fbm(2.0**float(fk), octaves, base_seed)[k[sel]]
        return out

    @staticmethod
    def _aggregate(T, side_T):
        node = T.node
        return {'path': T.path, 'kinds': json.loads(node['kinds']) if node.get('kinds') else {}, 'newest': node.get('newest') or 0,
                'mtime': node.get('mtime') or 0, 'side': side_T*0.25, 'day': node.get('day') or 0, 'scanned': True,
                'files': node.get('files') or 0, 'dirs': node.get('dirs') or 0}

    # ---------------------------------------------------------------- colour

    def _colour(self, xs, ys, px, H, ridge, water, depth, leaf_kind, leaf_ref, leaves, now, coast):
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
        macro = self.G.fbm(1/(px*90), 4, 5)
        grain = self.G.fbm(1/(px*5), 3, 11)
        ref = leaf_ref
        # Land: soft blend of covers, each weighted by its share and its own patch noise.
        land = (leaf_kind == 0) | (leaf_kind == 1)
        rgb = np.zeros((M, 3))
        if land.any():
            li = np.flatnonzero(land)
            fk = freq_key[ref[li]]
            acc = np.zeros((li.size, 3))
            total = np.zeros(li.size)
            for key in np.unique(fk):
                sel = fk == key
                pix = li[sel]
                lrefs = ref[pix]
                f1 = 2.0**float(key)
                for ci in range(len(COVERS)):
                    share = fractions[lrefs, ci]
                    if not share.any():
                        continue
                    nz = self.G.value(f1, 500+ci*31)[pix]*0.65 + self.G.value(f1*4, 507+ci*31)[pix]*0.35
                    w = share*np.exp(3.2*(nz-0.5))
                    acc[sel] += w[:, None]*cover_rgb[lrefs, ci]
                    total[sel] += w
            base = acc/np.maximum(total, 1e-9)[:, None]
            base *= (0.93+0.12*macro[li])[:, None]*(0.96+0.08*grain[li])[:, None]
            sand = np.clip(coast[li]*1.2, 0, 1)[:, None]
            base = base*(1-sand)+shore[ref[li]]*sand
            r = np.clip(ridge[li]*1.1, 0, 0.7)[:, None]
            base = base*(1-r)+rock[ref[li]]*r*(0.9+0.2*grain[li][:, None])
            sa = snow_amount[ref[li]]
            snow = np.clip((ridge[li]-0.2)*2.5+grain[li]*0.25, 0, 1)*sa   # snowbound: untouched > 2 years
            base = base*(1-snow[:, None])+SNOW[None, :]*snow[:, None]
            rgb[li] = base
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
                'dry': palette['dry']}[name]

    def _rain(self, leaf_ref, leaves, M):
        rain = np.zeros(M)
        for ref in np.unique(leaf_ref):
            T, entry = leaves[int(ref)]
            day = entry['day'] if entry else sum(1 for m in T.places.get('mtime', []) if time.time()-m < DAY)
            if day:
                rain[leaf_ref == ref] = min(1.0, math.log1p(day)/math.log(300))
        return rain

    # ---------------------------------------------------------------- rivers

    def _rivers(self, xs, ys, px, S, x0, y0, visited, descended, H, water, colour):
        for T in visited.values():
            inside = descended.get(T.node_id, set())
            for r in T.rivers:
                if r['kind'] == 'upper' and r['child'] in inside:
                    continue   # replaced by that place's own river network
                w = r['width']
                if w < px*0.6:
                    continue
                bx0, by0, bx1, by1 = r['bbox']
                reach = w*3
                if bx1+reach < x0 or by1+reach < y0 or bx0-reach > x0+S or by0-reach > y0+S:
                    continue
                # Only segments that pass within reach of this tile.
                p = r['pts']
                sx0 = np.minimum(p[:-1, 0], p[1:, 0])-reach
                sx1 = np.maximum(p[:-1, 0], p[1:, 0])+reach
                sy0 = np.minimum(p[:-1, 1], p[1:, 1])-reach
                sy1 = np.maximum(p[:-1, 1], p[1:, 1])+reach
                keep = (sx1 >= x0) & (sy1 >= y0) & (sx0 <= x0+S) & (sy0 <= y0+S)
                if not keep.any():
                    continue
                i0 = int(max(0, math.floor((sx0[keep].min()-x0)/px)))
                i1 = int(min(N-1, math.ceil((sx1[keep].max()-x0)/px)))
                j0 = int(max(0, math.floor((sy0[keep].min()-y0)/px)))
                j1 = int(min(N-1, math.ceil((sy1[keep].max()-y0)/px)))
                if i0 > i1 or j0 > j1:
                    continue
                # One vectorised pass per river: distance to the nearest of its segments.
                jj, ii = np.mgrid[j0:j1+1, i0:i1+1]
                flat = (jj*N+ii).ravel()
                qx = (x0+ii.ravel()*px)[:, None]
                qy = (y0+jj.ravel()*px)[:, None]
                a_pts, b_pts = p[:-1][keep], p[1:][keep]
                ax, ay = a_pts[:, 0][None, :], a_pts[:, 1][None, :]
                dx, dy = (b_pts[:, 0]-a_pts[:, 0])[None, :], (b_pts[:, 1]-a_pts[:, 1])[None, :]
                L2 = np.maximum(dx*dx+dy*dy, 1e-300)
                t = np.clip(((qx-ax)*dx+(qy-ay)*dy)/L2, 0, 1)
                d = np.hypot(qx-(ax+t*dx), qy-(ay+t*dy)).min(axis=1)
                near = d < reach
                if not near.any():
                    continue
                flat, d = flat[near], d[near]
                # A river belongs to its own ground: trunk and lower courses to the home
                # district, an upper course to its child's territory. Clipped there, rivers of
                # distant ancestors never cut trenches through the place you are looking at.
                lab, _ = label_at(T, x0+(flat % N)*px, y0+(flat // N)*px, px)
                own = (lab == r['child']) if r['kind'] == 'upper' else (lab == HOME)
                if not own.any():
                    continue
                flat, d = flat[own], d[own]
                valley = np.clip(1-d/reach, 0, 1)
                H[flat] -= min(w*0.18, px*40)*valley**2
                wet = np.clip((w/2-d)/px+0.5, 0, 1)
                colour[flat, :3] = colour[flat, :3]*(1-wet[:, None])+RIVER[None, :]*wet[:, None]
                bank = np.clip(1-d/(w*1.5), 0, 1)*(1-wet)
                colour[flat, :3] *= (1-0.12*bank)[:, None]
                water[flat] = np.maximum(water[flat], wet)
                colour[flat, 3] = np.maximum(colour[flat, 3], wet*255)

    # ---------------------------------------------------------------- landmarks

    def _landmarks(self, x0, y0, px, visited, leaf_kind, leaf_ref, leaves, H, water, colour, now):
        homes = {leaves[int(r)][0].node_id for r in np.unique(leaf_ref[leaf_kind == 1]) if leaves[int(r)][1] is None}
        Hg = H.reshape(N, N)
        Cg = colour.reshape(N, N, 4)
        Wg = water.reshape(N, N)
        S = px*(N-1)
        for tid in homes:
            T = visited[tid]
            pl = T.places
            if not pl.get('n'):
                continue
            r = pl['r']
            vis = np.flatnonzero((r/px >= 2.0) & (pl['x']+r*1.6 > x0) & (pl['x']-r*1.6 < x0+S) & (pl['y']+r*1.6 > y0) & (pl['y']-r*1.6 < y0+S))
            # Largest first; beyond ~900 per tile the rest are a few pixels each and read as texture.
            for i in vis[np.argsort(-r[vis])][:900]:
                self._stamp(Hg, Cg, Wg, x0, y0, px, float(pl['x'][i]), float(pl['y'][i]), float(r[i]), pl['kinds'][i],
                            (now-pl['mtime'][i])/DAY, stable_hash(pl['paths'][i]), pl['link'][i])

    def _stamp(self, Hg, Cg, Wg, x0, y0, px, cx, cy, r, kind, age, seed, link):
        reach = r*1.6
        i0 = max(0, int((cx-reach-x0)/px)); i1 = min(N-1, int((cx+reach-x0)/px)+1)
        j0 = max(0, int((cy-reach-y0)/px)); j1 = min(N-1, int((cy+reach-y0)/px)+1)
        if i0 > i1 or j0 > j1:
            return
        jj, ii = np.mgrid[j0:j1+1, i0:i1+1]
        qx = x0+ii*px
        qy = y0+jj*px
        d = np.hypot(qx-cx, qy-cy)/r
        h = Hg[j0:j1+1, i0:i1+1]
        c = Cg[j0:j1+1, i0:i1+1]
        w = Wg[j0:j1+1, i0:i1+1]
        n1 = fbm(qx, qy, 3.0/r, 3, seed & 0xFFFF)
        edge = np.clip((1.0-d)/(px/r*1.5+1e-12), 0, 1)       # antialiased footprint
        if kind == 'pdf':
            # A mountain. Height stays neutral until page counts are known; rock is its age.
            rock = rock_colour(age)
            sharp = 1.6 if age < 180 else 1.0 if age < 1095 else 0.6
            prof = np.clip(1-d, 0, 1)**sharp*(0.75+0.25*(1-np.abs(n1)))
            h += prof*r*0.55
            m = np.clip(prof*2.2, 0, 1)[..., None]
            c[..., :3] = c[..., :3]*(1-m)+rock*m
            if age > 730:
                snow = np.clip((prof-0.55)*4, 0, 1)[..., None]
                c[..., :3] = c[..., :3]*(1-snow)+SNOW*snow
        elif kind == 'images':
            canopy = np.clip((0.35-n1*0.5-d*0.6)*6, 0, 1)*edge
            h += canopy*r*0.08
            tone = np.array((44, 70, 46))*(0.85+0.3*value_noise(qx, qy, 12.0/r, seed & 0xFFF))[..., None]
            c[..., :3] = c[..., :3]*(1-canopy[..., None])+tone*canopy[..., None]
        elif kind in ('audio', 'video'):
            lake = np.clip((0.8+0.25*n1-d)/(px/r*1.5+1e-9), 0, 1)
            h -= lake*r*0.04
            w[...] = np.maximum(w, lake)
            col = SEA_SHALLOW*0.6+RIVER*0.4
            c[..., :3] = c[..., :3]*(1-lake[..., None])+col*lake[..., None]
            c[..., 3] = np.maximum(c[..., 3], lake*255)
            if kind == 'video':
                cliff = np.clip((qy-cy)/r+0.35, 0, 1)*np.clip(1-d*0.9, 0, 1)
                h += cliff*r*0.35*(1-lake)
        elif kind == 'tables':
            box = (np.abs(qx-cx) < r*0.75) & (np.abs(qy-cy) < r*0.6)
            stripe = (np.floor((qx-cx)/(r*0.18)) % 2 == 0)
            col = np.where(stripe[..., None], np.array((172, 160, 96)), np.array((122, 138, 74)))
            c[..., :3] = np.where(box[..., None], col, c[..., :3])
        elif kind in ('code', 'databases'):
            lots = (np.floor((qx-cx)/(r*0.28)) + np.floor((qy-cy)/(r*0.28))*7) % 5
            roof = (d < 0.85) & (lots != 0) & ((np.abs(((qx-cx)/(r*0.28)) % 1-0.5) < 0.32) & (np.abs(((qy-cy)/(r*0.28)) % 1-0.5) < 0.32))
            col = np.where((lots % 2 == 0)[..., None], np.array((150, 88, 70)), np.array((170, 160, 150)))
            c[..., :3] = np.where(roof[..., None], col, c[..., :3])
            h += roof*r*0.06
        elif kind == 'archives':
            ice = np.clip((0.9+0.2*n1-d)/(px/r*1.5+1e-9), 0, 1)
            streak = 0.9+0.1*np.sin((qx-cx)/r*18+n1*3)
            col = np.array((222, 232, 238))*streak[..., None]
            c[..., :3] = c[..., :3]*(1-ice[..., None])+col*ice[..., None]
            h += ice*r*0.12
        elif kind == 'binaries':
            # Obsidian: a small cluster of dark, glassy shards, not a hole in the map.
            shards = np.clip(1-d/0.6, 0, 1)**2*np.clip(0.3+np.abs(n1)*1.6, 0, 1)
            h += shards*r*0.25
            m = np.clip(shards*1.6, 0, 0.75)[..., None]
            tone = np.array((66, 62, 72))*(0.85+0.3*np.clip(n1, -0.5, 0.5)[..., None])
            c[..., :3] = c[..., :3]*(1-m)+tone*m
        elif kind == 'disks':
            rim = np.exp(-((d-0.7)/0.15)**2)
            h += rim*r*0.3 - (d < 0.6)*r*0.05
            inner = np.clip((0.55-d)/(px/r*1.5+1e-9), 0, 1)
            c[..., :3] = c[..., :3]*(1-rim[..., None]*0.7)+np.array((96, 76, 66))*rim[..., None]*0.7
            c[..., :3] = c[..., :3]*(1-inner[..., None])+SEA_SHALLOW*inner[..., None]
            w[...] = np.maximum(w, inner)
            c[..., 3] = np.maximum(c[..., 3], inner*255)
        else:
            dot = np.clip((0.45-d)/(px/r*1.5+1e-9), 0, 1)
            c[..., :3] = c[..., :3]*(1-dot[..., None]*0.6)+np.array((150, 146, 132))*dot[..., None]*0.6
            h += dot*r*0.1
        if kind == 'documents':
            bloom = np.clip((0.6-d)*3, 0, 1)*(value_noise(qx, qy, 14.0/r, seed & 0xFFF) > 0.72)
            c[..., :3] = c[..., :3]*(1-bloom[..., None]*0.7)+np.array((214, 196, 110))*bloom[..., None]*0.7

    # ---------------------------------------------------------------- contours

    def _contours(self, H, water, colour, px):
        # Faint index contours (topographic-map legibility), spaced relative to the view.
        spacing = px*14
        phase = (H/spacing) % 1.0
        line = np.clip(1-np.minimum(phase, 1-phase)*spacing/(px*0.9), 0, 1)*(water < 0.5)
        colour[:, :3] *= (1-0.1*line)[:, None]
        return colour


def encode(tile: dict) -> bytes:
    """Binary tile: header (magic, n, level, x, y, base) then float32 heights, RGBA colour, RGBA aux."""
    head = struct.pack('<4sIIqqd', b'BTL1', N, tile['level'], tile['x'], tile['y'], tile['base'])
    return head + tile['height'].astype('<f4').tobytes() + tile['colour'].tobytes() + tile['aux'].tobytes()
