import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from branchfm.index import Index, Surveyor, NO_CRAWL
from branchfm.mounts import parse_disk_mounts
from branchfm.world import World
from branchfm.atlas_api import Atlas


class MountTests(unittest.TestCase):
    def test_native_mounts_and_escaped_names(self):
        mounts = parse_disk_mounts(r'''
/dev/root / btrfs rw 0 0
/dev/sda1 /run/media/user/Tuppum ntfs3 rw 0 0
/dev/sdb1 /media/user/My\040Disk ext4 rw 0 0
C: /mnt/c 9p rw 0 0
none /mnt/wsl tmpfs rw 0 0
none /mnt/wslg/rootfs ext4 rw 0 0
/dev/sda1 /run/media/user/Tuppum/nested ntfs3 rw 0 0
''')
        self.assertEqual(mounts, ('/media/user/My Disk', '/mnt/c', '/run/media/user/Tuppum'))

    def test_drive_under_excluded_run_is_discovered_and_promoted(self):
        drive = '/run/media/user/Tuppum'
        with tempfile.TemporaryDirectory() as tmp, \
             patch('branchfm.index.disk_mounts', return_value=(drive,)), \
             patch('branchfm.world.disk_mounts', return_value=(drive,)):
            index = Index(Path(tmp)/'index.sqlite')
            surveyor = Surveyor(index)
            atlas = None
            try:
                surveyor.discover_mounts()
                self.assertIn('/run', NO_CRAWL)
                self.assertEqual(surveyor.queued[drive], (1, True))
                index.store(drive, [['Photos', True, 0, 0, False, 0]])
                index.store(drive+'/Photos', [['a.jpg', False, 12, 0, False, 0]])
                while index.settle():
                    pass
                atlas = Atlas('/', index=index, survey=False, workers=0)
                root = atlas.world.root()
                entry = next(c for c in root.children if c['path'] == drive)
                self.assertEqual(entry['name'], 'Tuppum')
                self.assertFalse(entry['same_land'])
                self.assertEqual(World.continent_of(drive+'/Photos/a.jpg'), drive)
                self.assertEqual(World.continent_of(drive+'-backup'), '/')
                self.assertEqual(atlas.world.territory_for(drive+'/Photos').path, drive+'/Photos')
                self.assertEqual(atlas.region(drive, survey=False)['path'], drive)
                self.assertEqual(atlas.region(drive+'/Photos', survey=False)['path'], drive+'/Photos')
                # The mount's ordinary parent must not draw a second copy.
                parent = atlas.world.territory_for('/run/media/user')
                if parent is not None:
                    self.assertNotIn(drive, [c['path'] for c in parent.children])
            finally:
                surveyor.stop()
                if atlas:
                    atlas.close()
                    atlas.world.store.close()
                index.close()
