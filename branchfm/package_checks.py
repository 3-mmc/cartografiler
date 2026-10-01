"""Exercise frozen spawn workers and accelerators using explicitly synthetic data."""
from pathlib import Path
import json
import tempfile
import time


def check_package(report: Path) -> None:
    import numpy as np
    from PIL import Image
    from . import fastnoise, world
    from .atlas_api import Atlas, TILE_CODEC
    from .index import Index
    from .service import bounded_extract

    if not fastnoise.HAVE_NUMBA or TILE_CODEC != b'Z':
        raise RuntimeError('Standalone builds must include Numba and Zstandard.')
    points = np.linspace(-3.0, 3.0, 257)
    compiled = fastnoise.value_noise(points, points[::-1], 8.0, 4242)
    if not np.array_equal(compiled, world.value_noise_numpy(points, points[::-1], 8.0, 4242)):
        raise RuntimeError('Compiled noise differs from the NumPy terrain definition.')
    with tempfile.TemporaryDirectory(prefix='cartografiler-package-') as tmp:
        folder = Path(tmp)
        text = folder/'sample.txt'
        text.write_text('Cartografiler synthetic package check\n')
        picture = folder/'sample.png'
        Image.new('RGB', (32, 32), '#408060').save(picture)
        preview = bounded_extract(text, 'preview')
        if preview.get('error') or not preview.get('lines'):
            raise RuntimeError(f'Frozen text preview worker failed: {preview}')
        preview = bounded_extract(picture, 'preview')
        if preview.get('error') or not preview.get('image_png'):
            raise RuntimeError('Frozen Pillow image preview worker failed.')
        index = Index(folder/'index.sqlite')
        atlas = None
        try:
            index.store('/', [['synthetic', True, 0, 0, False, 0]])
            index.store('/synthetic', [['sample.txt', False, 42, time.time(), False, 0],
                                       ['sample.png', False, 100, time.time(), False, 0]])
            while index.settle():
                pass
            atlas = Atlas('/', index=index, survey=False, workers=2)
            started = time.perf_counter()
            cold = atlas.tile(1, 0, 0)
            cold_seconds = time.perf_counter()-started
            atlas.tiles.clear()  # force the second read through the persistent tile store
            started = time.perf_counter()
            warm = atlas.tile(1, 0, 0)
            warm_seconds = time.perf_counter()-started
            if cold != warm or cold[:4] != b'BTL3':
                raise RuntimeError('Frozen tile worker or persistent cache failed.')
            # Complete both worker shutdowns before the synthetic directory is removed.
            atlas.pool.shutdown(wait=True)
            atlas.pool = None
        finally:
            if atlas:
                atlas.close()
                if atlas.world.store:
                    atlas.world.store.close()
            index.close()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({'numba': True, 'zstandard': True, 'spawn_previews': True,
                                 'spawn_tiles': True, 'persistent_cache': True,
                                 'cold_tile_seconds': cold_seconds,
                                 'cached_tile_seconds': warm_seconds}, indent=2)+'\n')
