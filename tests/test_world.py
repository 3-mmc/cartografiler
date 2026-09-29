"""The survey index, world layout and terrain synthesis, on small generated trees."""
import os
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from branchfm.atlas_api import TILE_CACHE_MAX_AGE, TILE_CACHE_TRANSIENT, TileStore
from branchfm import fastnoise, world
from branchfm.index import Index, list_linux
from branchfm.tiles import N, Synth, encode
from branchfm.world import FIRST_CHILD, HOME, World, label_at


def survey(index, root: Path):
    # Store every directory under root, then settle aggregates (what the Surveyor does).
    stack = [str(root)]
    while stack:
        path = stack.pop()
        stack.extend(index.store(path, list_linux(path)))
    while index.settle():
        pass


class WorldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.tree = base/'tree'
        # Very uneven siblings: one huge, several tiny; nested places; varied kinds.
        (self.tree/'huge').mkdir(parents=True)
        for i in range(400):
            (self.tree/'huge'/f'photo-{i:03d}.jpg').touch()
        for name in ('tiny-a', 'tiny-b', 'tiny-c', 'tiny-d'):
            (self.tree/name).mkdir()
            (self.tree/name/'note.md').touch()
        (self.tree/'deep'/'deeper'/'deepest').mkdir(parents=True)
        (self.tree/'deep'/'deeper'/'deepest'/'paper.pdf').touch()
        (self.tree/'empty').mkdir()
        for name in ('a.pdf', 'b.csv', 'c.py', 'd.zip'):
            (self.tree/name).touch()
        self.index = Index(base/'index.sqlite')
        survey(self.index, self.tree)
        self.world = World(self.index)

    def tearDown(self):
        self.world.store.close()
        self.index.close()
        self.tmp.cleanup()

    def test_index_aggregates_whole_subtree(self):
        node = self.index.node(str(self.tree))
        self.assertEqual(node['files'], 400+4+1+4)
        self.assertEqual(node['dirs'], 1+4+3+1)
        self.assertEqual(self.index.node(str(self.tree/'huge'))['files'], 400)

    def test_rescan_removes_vanished_entries(self):
        (self.tree/'tiny-a'/'note.md').unlink()
        (self.tree/'tiny-a').rmdir()
        self.index.store(str(self.tree), list_linux(str(self.tree)))
        while self.index.settle():
            pass
        self.assertIsNone(self.index.node(str(self.tree/'tiny-a')))
        self.assertIsNone(self.index.node(str(self.tree/'tiny-a'/'note.md')))

    def test_no_place_is_ever_dropped(self):
        t = self.world.territory_for(str(self.tree))
        self.assertIsNotNone(t)
        names = {c['name'] for c in t.children}
        self.assertEqual(names, {'huge', 'tiny-a', 'tiny-b', 'tiny-c', 'tiny-d', 'deep', 'empty'})
        self.assertEqual(t.places['n'], 4)   # its own files stand in its home district

    def test_child_mask_matches_parent_borders(self):
        # A child's territory is sampled through the parent's label field: the two agree.
        t = self.world.territory_for(str(self.tree))
        entry = next(c for c in t.children if c['name'] == 'huge')
        inner = self.world.child(t, entry)
        n = inner.n
        c = (np.arange(n)+0.5)/n*inner.size
        gx, gy = np.meshgrid(inner.x0+c, inner.y0+c)
        lab, _ = label_at(t, gx.ravel(), gy.ravel(), inner.size/n)
        inside_parent = lab == entry['label']
        inside_child = inner.labels.ravel() != 0
        self.assertGreater((inside_parent == inside_child).mean(), 0.97)

    def test_terrain_has_no_cliffs(self):
        # Every height term must be continuous across borders; walls were a real regression.
        synth = Synth(self.world)
        t = self.world.territory_for(str(self.tree))
        level = int(np.floor(np.log2(1/t.size)))+1
        S = 0.5**level
        tx, ty = int((t.x0+t.size/2)/S), int((t.y0+t.size/2)/S)
        for dx, dy in ((0, 0), (1, 0), (0, 1)):
            h = synth.tile(level, tx+dx, ty+dy)['height']
            slope = max(np.abs(np.diff(h, axis=0)).max(), np.abs(np.diff(h, axis=1)).max())/(S/(N-1))
            self.assertLess(slope, 20.0)

    def test_tile_encoding(self):
        tile = Synth(self.world).tile(0, 0, 0)
        body = encode(tile)
        self.assertEqual(body[:4], b'BTL3')
        import struct
        count = struct.unpack_from('<I', body, 36+N*N*4*5)[0]
        self.assertEqual(len(body), 36+N*N*4*5+4+count*24)

    def test_files_of_a_kind_form_one_patch(self):
        # Mixed files in one folder: each kind clusters into a cohesive patch, not confetti.
        mixed = self.tree/'mixed'
        mixed.mkdir()
        for i in range(60):
            (mixed/f'photo-{i:02d}.jpg').touch()
            (mixed/f'sheet-{i:02d}.csv').touch()
            (mixed/f'script-{i:02d}.py').touch()
        survey(self.index, self.tree)
        world = World(self.index)
        try:
            t = world.territory_for(str(mixed))
            pl = t.places
            self.assertEqual(pl['n'], 180)
            self.assertEqual({q['kind'] for q in pl['patches']}, {'images', 'tables', 'code'})
            from scipy.spatial import cKDTree
            pts = np.column_stack([pl['x'], pl['y']])
            _, nb = cKDTree(pts).query(pts, k=2)
            kinds = np.array(pl['kinds'])
            self.assertGreater((kinds[nb[:, 1]] == kinds).mean(), 0.9)
            # Parcels run alphabetically: names sorted within each patch.
            for q in range(len(pl['patches'])):
                names = [pl['names'][i] for i in np.flatnonzero(pl['patch'] == q)]
                self.assertEqual(names, sorted(names, key=str.casefold))
        finally:
            world.store.close()

    def test_rivers_form_a_network(self):
        # Tributaries end on other streams (confluences); widths never shrink downstream.
        t = self.world.territory_for(str(self.tree))
        streams = [r for r in t.rivers if r['kind'] == 'stream']
        self.assertGreaterEqual(len(streams), 2)
        for r in streams:
            self.assertTrue(np.all(np.diff(r['w']) >= -1e-12))
        ends = [tuple(np.round(r['pts'][-1], 12)) for r in streams[1:]]
        on_network = 0
        for e in ends:
            for other in streams:
                if other is not None and np.min(np.hypot(other['pts'][:, 0]-e[0], other['pts'][:, 1]-e[1])) < t.cell:
                    on_network += 1
                    break
        self.assertEqual(on_network, len(ends))

    def test_coast_distance_agrees_with_labels(self):
        # Coastal slopes are built on coast_distance: it must be <= 0 wherever the map draws
        # sea and >= 0 on land, or the shore becomes a wall (a real regression near Kino).
        from branchfm.world import SEA, OUT, coast_distance
        root = self.world.root()
        rng = np.random.default_rng(3)
        xs, ys = rng.uniform(0.05, 0.95, 20000), rng.uniform(0.05, 0.95, 20000)
        px = 1e-4
        lab, _ = label_at(root, xs, ys, px)
        d = coast_distance(root, xs, ys, px)
        sea = (lab == SEA) | (lab == OUT)
        self.assertTrue(sea.any() and (~sea).any())
        self.assertLessEqual(float(d[sea].max()), 1e-9)
        self.assertGreaterEqual(float(d[~sea].min()), -1e-9)

    def test_companion_files_join_the_main_patch(self):
        # A film folder: one large video and its subtitles. The subtitles are plain ground
        # in the video's patch, not a scrub patch of their own.
        film = self.tree/'film'
        film.mkdir()
        with open(film/'movie.mkv', 'wb') as f:
            f.truncate(200_000_000)
        for lang in ('en', 'de', 'fr', 'ru', 'es'):
            (film/f'movie.{lang}.srt').write_text('1\n00:00:01,000 --> 00:00:02,000\nhi\n')
        survey(self.index, self.tree)
        world = World(self.index)
        try:
            pl = world.territory_for(str(film)).places
            self.assertEqual([q['kind'] for q in pl['patches']], ['video'])
            self.assertEqual(pl['patches'][0]['n'], 1)
            self.assertEqual(int(pl['companion'].sum()), 5)
        finally:
            world.store.close()

    def test_libraries_are_recognised(self):
        # Ten album folders under one parent: a music library, drawn as one landscape.
        lib = self.tree/'music'
        for i in range(10):
            album = lib/f'album-{i}'
            album.mkdir(parents=True)
            for j in range(4):
                with open(album/f'{j:02d}.flac', 'wb') as f:
                    f.truncate(5_000_000)
            (album/'cover.nfo').touch()
        survey(self.index, self.tree)
        world = World(self.index)
        try:
            t = world.territory_for(str(lib))
            self.assertEqual(t.uniform, 'audio')
            self.assertFalse([r for r in t.rivers if r['kind'] == 'upper'])
        finally:
            world.store.close()

    def test_roles_become_buildings_and_landforms(self):
        from branchfm.world import file_role
        self.assertEqual(file_role('/p/pyproject.toml', 'pyproject.toml', 'other', 1, False), 'hall')
        self.assertEqual(file_role('/p/model.safetensors', 'model.safetensors', 'weights', 1e9, False), 'power')
        self.assertEqual(file_role('/p/target/release/app', 'app', 'binaries', 1e6, False), 'factory')
        self.assertEqual(file_role('/p/node_modules/x/index.js', 'index.js', 'code', 1e3, False), 'depot')
        self.assertEqual(file_role('/p/app.db', 'app.db', 'databases', 1e6, False), 'silo')
        self.assertEqual(file_role('/p/link', 'link', 'other', 0, True), 'arch')
        self.assertEqual(file_role('/p/IMG_1.CR2', 'IMG_1.CR2', 'images', 3e7, False), 'oak')
        self.assertEqual(file_role('/p/Screenshot 1.png', 'Screenshot 1.png', 'images', 9e5, False), 'shrub')
        # A git repository is a walled town; a folder holding only one folder, a slot canyon.
        repo = self.tree/'repo'
        (repo/'.git').mkdir(parents=True)
        (repo/'pyproject.toml').touch()
        (repo/'main.py').touch()
        (self.tree/'chain'/'a'/'b').mkdir(parents=True)
        (self.tree/'chain'/'a'/'b'/'x.txt').touch()
        survey(self.index, self.tree)
        world = World(self.index)
        try:
            t = world.territory_for(str(repo))
            self.assertTrue(t.repo)
            self.assertIn('hall', t.places['roles'])
            self.assertTrue(world.territory_for(str(self.tree/'chain')).slot)
        finally:
            world.store.close()

    def test_a_big_town_renders_at_every_zoom(self):
        # A town past CITY_MIN files grows a city centre, and its lots take heights; tiles over
        # it must render at every level (a clobbered variable once broke exactly this).
        from branchfm.tiles import BLOCK, CITY_MIN, TOWER
        town = self.tree/'town'
        town.mkdir()
        for i in range(CITY_MIN+15):
            (town/f'module_{i:02d}.py').write_text('x = 1\n'*(i+1))
        survey(self.index, self.tree)
        world = World(self.index)
        try:
            synth = Synth(world)
            t = world.territory_for(str(town))
            cx, cy = t.x0+t.size/2, t.y0+t.size/2
            tall = 0
            for level in range(max(0, int(np.floor(np.log2(1/t.size)))-2), int(np.floor(np.log2(1/t.size)))+6):
                S = 0.5**level
                inst = synth.tile(level, int(cx/S), int(cy/S))['instances']
                tall += int(np.isin(inst['kind'], (BLOCK, TOWER)).sum())
            self.assertGreater(tall, 0)
        finally:
            world.store.close()

    def test_no_sea_walls_at_the_coast(self):
        # Every land-only height term must fade to nothing at the shoreline (the fractal detail
        # once stood as a wall a tenth of a tile high along every coast).
        from branchfm.world import SEA, OUT
        root = self.world.root()
        xs = np.linspace(0.5, 0.99, 4000)
        lab, _ = label_at(root, xs, np.full(xs.size, 0.5), 1e-5)
        sea = (lab == SEA) | (lab == OUT)
        cx = float(xs[np.argmax(sea)])
        synth = Synth(self.world)
        for level in (5, 7, 9):
            S = 0.5**level
            h = synth.tile(level, int(cx/S), int(0.5/S))['height']
            slope = max(np.abs(np.diff(h, axis=0)).max(), np.abs(np.diff(h, axis=1)).max())/(S/(N-1))
            self.assertLess(slope, 20.0, f'level {level}')

    def test_continents_end_in_deltas(self):
        root = self.world.root()
        self.assertTrue(any('delta' in lake for lake in root.lakes) or root.mouth is None)

    def test_disks_are_continents(self):
        self.assertEqual(World.continent_of('/mnt/e/Photos/x.jpg'), '/mnt/e')
        self.assertEqual(World.continent_of('/home/praetor'), '/')
        self.assertEqual(World.display_name('/mnt/c', 'c'), 'C:')
        root = self.world.root()
        self.assertTrue(root.continental)
        for c in root.children:
            if c['path'] != str(self.tree):
                continue
            inner = self.world.child(root, c)
            self.assertIsNotNone(inner)

    def test_layout_cache_is_shared_and_persistent(self):
        t = self.world.territory_for(str(self.tree))
        other = World(self.index)
        again = other.territory_for(str(self.tree))
        other.store.close()
        self.assertEqual(t.sig, again.sig)
        self.assertTrue(np.array_equal(t.labels, again.labels))

    def test_atlas_places_and_picking(self):
        from branchfm.atlas_api import Atlas
        atlas = Atlas(str(self.tree), index=self.index, survey=False, workers=0)
        try:
            place = atlas.region(str(self.tree/'huge'))
            self.assertEqual(place['name'], 'huge')
            hit = atlas.at(place['x'], place['y'], place['side']/400)
            self.assertIn(str(self.tree/'huge'), [c['path'] for c in hit['chain']])
            names = atlas.places(place['bbox'][0], place['bbox'][1], place['bbox'][2], place['bbox'][3], place['side']/800)
            self.assertTrue(names['files'] or names['regions'])
        finally:
            atlas.close()
            atlas.world.store.close()


class TileStoreTests(unittest.TestCase):
    """Rendered tiles kept across sessions: a revisited place is read back, a changed one is not."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TileStore(os.path.join(self.tmp.name, 'tiles.sqlite'))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_a_stored_tile_comes_back_whole(self):
        blob = os.urandom(4096)
        self.store.put(3, 4, 4, blob)
        self.assertEqual(self.store.get(3, 4, 4), blob)
        self.assertIsNone(self.store.get(3, 4, 5))

    def test_a_tile_past_its_expiry_is_not_served(self):
        self.store.put(3, 4, 4, b'stale')
        self.store.db().execute('UPDATE tiles SET expires=?', (time.time()-1,))
        self.store.db().commit()
        self.assertIsNone(self.store.get(3, 4, 4))

    def test_a_tile_with_a_geyser_outlives_only_the_geyser(self):
        self.store.put(3, 4, 4, b'erupting', ttl=TILE_CACHE_TRANSIENT)
        self.store.put(3, 0, 0, b'quiet')
        spans = {r[0]: r[1]-time.time() for r in self.store.db().execute('SELECT x, expires FROM tiles')}
        self.assertLessEqual(spans[4], TILE_CACHE_TRANSIENT)
        self.assertGreater(spans[0], TILE_CACHE_TRANSIENT)

    def test_a_changed_region_drops_only_the_tiles_it_touches(self):
        self.store.put(3, 4, 4, b'here')      # spans 0.500..0.625
        self.store.put(3, 0, 0, b'far')       # spans 0.000..0.125
        self.store.drop([(0.51, 0.51, 0.52, 0.52)])
        self.assertIsNone(self.store.get(3, 4, 4))
        self.assertEqual(self.store.get(3, 0, 0), b'far')

    def test_a_new_layout_version_empties_the_store(self):
        self.store.put(3, 4, 4, b'old world')
        self.store.db().execute("INSERT OR REPLACE INTO meta VALUES('layout_version', '-1')")
        self.store.db().commit()
        self.store.close()
        again = TileStore(os.path.join(self.tmp.name, 'tiles.sqlite'))
        self.addCleanup(again.close)
        self.assertIsNone(again.get(3, 4, 4))


@unittest.skipUnless(fastnoise.HAVE_NUMBA, 'numba is not installed; the NumPy path is in use')
class CompiledNoiseTests(unittest.TestCase):
    """The compiled kernels must agree with the NumPy definitions to the last bit: laid-out
    territories, cached tiles and the borders are all keyed on the exact values."""
    def test_value_noise_matches_across_ranges_and_seeds(self):
        rng = np.random.default_rng(11)
        for freq in (0.5, 8.0, 1024.0, 65536.0):
            for seed in (0, 1013, -623324649033, 2**40):
                x = rng.uniform(-50, 50, 257)
                y = rng.uniform(-50, 50, 257)
                self.assertTrue(np.array_equal(world.value_noise_numpy(x, y, freq, seed),
                                               fastnoise.value_noise(x, y, freq, seed)),
                                f'value_noise differs at freq={freq} seed={seed}')

    def test_lattice_matches_including_negative_coordinates(self):
        rng = np.random.default_rng(12)
        for lo, hi in ((-5, 5), (-10**6, 10**6), (0, 3)):
            i = rng.integers(lo, hi, 400).astype(np.int64)
            j = rng.integers(lo, hi, 400).astype(np.int64)
            self.assertTrue(np.array_equal(world.lattice_numpy(i, j, 4242), fastnoise.lattice(i, j, 4242)))

    def test_a_tile_is_byte_for_byte_what_numpy_draws(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'a').mkdir()
            for i in range(30):
                (root/'a'/f'f{i}.py').write_text('x = %d\n' % i)
            index = Index(os.path.join(tmp, 'ix.sqlite'))
            survey(index, root)
            self.addCleanup(index.close) if hasattr(index, 'close') else None
            def draw():
                w = World(index)
                try:
                    return encode(Synth(w).tile(0, 0, 0))
                finally:
                    if w.store: w.store.close()
            compiled = draw()
            fastnoise.HAVE_NUMBA = False
            world.value_noise, world.lattice = world.value_noise_numpy, world.lattice_numpy
            try:
                plain = draw()
            finally:
                fastnoise.HAVE_NUMBA = True
                world.value_noise, world.lattice = fastnoise.value_noise, fastnoise.lattice
            self.assertEqual(compiled, plain)


if __name__ == '__main__':
    unittest.main()
