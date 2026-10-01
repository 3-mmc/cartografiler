import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from branchfm import platform as host
from branchfm.mounts import _disk_mounts


class PlatformTests(unittest.TestCase):
    def test_runtime_does_not_select_another_hosts_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base/'tools').mkdir()
            linux = base/'tools/Godot_v4.7.2-stable_linux.x86_64'
            linux.touch()
            with patch.dict(host.os.environ, {}, clear=True), patch.object(host.sys, 'platform', 'darwin'):
                with self.assertRaises(ValueError):
                    host.godot_runtime(base)
                mac = base/'tools/Godot.app/Contents/MacOS/Godot'
                mac.parent.mkdir(parents=True)
                mac.touch()
                self.assertEqual(host.godot_runtime(base), mac)
            with patch.dict(host.os.environ, {}, clear=True), patch.object(host.sys, 'platform', 'win32'):
                win = base/'tools/Godot_v4.7.2-stable_win64.exe'
                win.touch()
                self.assertEqual(host.godot_runtime(base), win)

    def test_runtime_override_with_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp)/'My Godot'
            executable.touch()
            with patch.dict(host.os.environ, CARTOGRAFILER_GODOT=str(executable)):
                self.assertEqual(host.godot_runtime(Path(tmp)), executable)
            with patch.dict(host.os.environ, CARTOGRAFILER_GODOT=str(executable)+'missing'):
                with self.assertRaises(ValueError):
                    host.godot_runtime(Path(tmp))

    def test_mac_open_passes_filename_without_shell_interpretation(self):
        path = Path('/Users/dad/Photos/a $(touch surprise).jpg')
        with patch.object(host.sys, 'platform', 'darwin'), patch.object(host.subprocess, 'Popen') as popen:
            host.open_external(path)
            self.assertEqual(popen.call_args.args[0], ['/usr/bin/open', str(path)])
            self.assertNotIn('shell', popen.call_args.kwargs)

    def test_windows_open_uses_native_associations(self):
        with patch.object(host.sys, 'platform', 'win32'), patch.object(host.os, 'startfile', create=True) as start:
            host.open_external(Path('document.pdf'))
            start.assert_called_once_with('document.pdf')

    def test_non_linux_preview_does_not_require_resource_module(self):
        for platform in ('darwin', 'win32'):
            with patch.object(host.sys, 'platform', platform):
                self.assertFalse(host.limit_worker_memory(384*1024*1024))

    def test_mount_parsers_and_mac_disk_filter(self):
        self.assertEqual(host.parse_mount_table('/dev/sda /media/My\\040Disk ext4 rw 0 0'),
                         [('/media/My Disk', 'ext4')])
        table = host.parse_mount_table('/dev/disk3s1 on / (apfs, sealed, local)\n'
                                      '/dev/disk4s1 on /Volumes/Dad Photos (apfs, local)\n'
                                      'map auto_home on /System/Volumes/Data/home (autofs, automounted)', macos=True)
        self.assertEqual(table[1], ('/Volumes/Dad Photos', 'apfs'))
        with patch.object(host.sys, 'platform', 'darwin'), patch.object(host, 'mount_table', return_value=table):
            _disk_mounts.cache_clear()
            self.assertEqual(_disk_mounts(0), ('/Volumes/Dad Photos',))
        _disk_mounts.cache_clear()

    def test_mac_apfs_alias_tree_is_not_recursively_surveyed(self):
        from branchfm.index import Index, NO_CRAWL
        with patch.object(host.sys, 'platform', 'darwin'):
            exclusions = host.survey_exclusions()
        self.assertEqual(exclusions, {'/System/Volumes'})
        with tempfile.TemporaryDirectory() as tmp, patch('branchfm.index.NO_CRAWL', NO_CRAWL | exclusions):
            index = Index(Path(tmp)/'index.sqlite')
            try:
                children = index.store('/System', [['Volumes', True, 0, 0, False, 0],
                                                   ['Library', True, 0, 0, False, 0]])
                self.assertEqual(children, ['/System/Library'])
                self.assertIsNotNone(index.node('/System/Volumes'))
                self.assertEqual(index.store('/Users', [['dad', True, 0, 0, False, 0]]), ['/Users/dad'])
            finally:
                index.close()

    def test_frozen_assets_and_runtime_are_independent_of_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base/'runtime').mkdir()
            runtime = base/'runtime/CartografilerMap'
            runtime.touch()
            with patch.dict(host.os.environ, {}, clear=True), \
                 patch.object(host.sys, 'frozen', True, create=True), \
                 patch.object(host.sys, '_MEIPASS', str(base), create=True):
                self.assertEqual(host.asset_root(), base)
                self.assertEqual(host.godot_runtime(base), runtime)

    def test_mac_workers_leave_capacity_for_rendering(self):
        with patch.object(host.sys, 'platform', 'darwin'):
            for cores, expected in ((1, 1), (3, 1), (8, 4), (16, 4)):
                with patch.object(host.os, 'cpu_count', return_value=cores):
                    self.assertEqual(host.tile_worker_count(), expected)
