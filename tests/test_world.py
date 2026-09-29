"""The survey index, world layout and terrain synthesis, on small generated trees."""
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

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
        self.assertEqual(body[:4], b'BTL2')
        self.assertEqual(len(body), 36+N*N*4*5)

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


if __name__ == '__main__':
    unittest.main()
