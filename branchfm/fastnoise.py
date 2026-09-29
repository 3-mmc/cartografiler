"""Compiled kernels for the noise primitives, used when numba is installed.

The arithmetic is world.py's, unchanged. Only the dispatch differs. The world is sampled on
many small point sets — about 20k lattice calls a tile at a median of 61 points — and there
NumPy spends roughly 60 us a call building and tearing down half a dozen uint64 temporaries
against about 1 us of actual work, a 56x loss. These are plain loops with no temporaries.

Bit-identity is not optional: laid-out territories, cached tiles and the border tests all key
on the exact values. `equivalent()` checks it, and the tests call it.
"""
from __future__ import annotations

import numpy as np

try:
    from numba import njit
    HAVE_NUMBA = True
except ImportError:                       # the NumPy path in world.py stays authoritative
    HAVE_NUMBA = False


if HAVE_NUMBA:
    _M1 = np.uint64(0x9E3779B97F4A7C15)
    _M2 = np.uint64(0xBF58476D1CE4E5B9)
    _M3 = np.uint64(0x94D049BB133111EB)
    _S30, _S27, _S31, _S11 = np.uint64(30), np.uint64(27), np.uint64(31), np.uint64(11)
    _MASK48 = np.uint64(0xFFFFFFFFFFFF)
    _SCALE = 1.0/float(1 << 53)

    @njit(cache=True, inline='always')
    def _mix(h):
        h = (h ^ (h >> _S30))*_M2
        h = (h ^ (h >> _S27))*_M3
        return h ^ (h >> _S31)

    @njit(cache=True, inline='always')
    def _lat(i, j, s):
        h = _mix(np.uint64(i)*_M1 ^ _mix(np.uint64(j) + s*_M3))
        return np.float64(h >> _S11)*_SCALE

    @njit(cache=True)
    def _value_noise(x, y, freq, seed, out):
        s = np.uint64(seed) & _MASK48
        for k in range(x.size):
            X = x.flat[k]*freq
            Y = y.flat[k]*freq
            xi = np.floor(X)
            yi = np.floor(Y)
            fx = X-xi
            fy = Y-yi
            fx = fx*fx*(3.0-2.0*fx)
            fy = fy*fy*(3.0-2.0*fy)
            ix = np.int64(xi)
            iy = np.int64(yi)
            a = _lat(ix, iy, s)
            b = _lat(ix+1, iy, s)
            c = _lat(ix, iy+1, s)
            d = _lat(ix+1, iy+1, s)
            top = a + (b-a)*fx
            out[k] = top + ((c + (d-c)*fx) - top)*fy

    @njit(cache=True)
    def _lattice(i, j, seed, out):
        s = np.uint64(seed) & _MASK48
        for k in range(out.size):
            out[k] = _lat(np.int64(i.flat[k]), np.int64(j.flat[k]), s)


    @njit(cache=True)
    def _smooth_sep(L, cx, cy, wx, wy, A, out):
        # Separable cubic B-spline: interpolate the lattice rows along x once, then those rows
        # along y — the same two passes, and the same summation order (taps 0..3), as the NumPy
        # spelling, so the result matches to the last bit. Doing x inside the y loop instead
        # would repeat it four times over.
        n = cx.size
        for r in range(L.shape[0]):
            for ii in range(n):
                s = 0.0
                for ox in range(4):
                    s += L[r, cx[ii]+ox-1]*wx[ox, ii]
                A[r, ii] = s
        for jj in range(cy.size):
            for ii in range(n):
                acc = 0.0
                for oy in range(4):
                    acc += A[cy[jj]+oy-1, ii]*wy[oy, jj]
                out[jj*n+ii] = acc


def smooth_separable(L, cx, cy, wx, wy):
    """The B-spline gather that GridNoise.smooth would otherwise do in eight big temporaries."""
    L = np.ascontiguousarray(L, dtype=np.float64)
    cx = np.ascontiguousarray(cx, dtype=np.int64)
    cy = np.ascontiguousarray(cy, dtype=np.int64)
    out = np.empty(cy.size*cx.size, dtype=np.float64)
    A = np.empty((L.shape[0], cx.size), dtype=np.float64)
    _smooth_sep(L, cx, cy, np.ascontiguousarray(wx, dtype=np.float64),
                np.ascontiguousarray(wy, dtype=np.float64), A, out)
    return out


def value_noise(x, y, freq: float, seed):
    x = np.ascontiguousarray(x, dtype=np.float64)
    y = np.ascontiguousarray(y, dtype=np.float64)
    out = np.empty(x.size, dtype=np.float64)
    _value_noise(x, y, float(freq), np.int64(seed), out)
    return out.reshape(x.shape)


def lattice(i, j, seed):
    i = np.ascontiguousarray(i, dtype=np.int64)
    j = np.ascontiguousarray(j, dtype=np.int64)
    out = np.empty(i.size, dtype=np.float64)
    _lattice(i, j, np.int64(seed), out)
    return out.reshape(i.shape)
