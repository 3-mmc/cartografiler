import os
import shutil
import tempfile
import unittest
from pathlib import Path

from branchfm.atlas import metadata, mountain_height, terrain
from branchfm.demo import make_pdf
from branchfm.model import Node, Operations, clean, layout
from branchfm.preview import preview
from branchfm.service import bash_destinations


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ops = Operations(self.root/'trash')

    def tearDown(self):
        self.tmp.cleanup()

    def test_move_undo_never_overwrites(self):
        a,b = self.root/'a',self.root/'b'
        a.write_text('original')
        b.write_text('existing')
        with self.assertRaises(ValueError): self.ops.move(a,b)
        self.assertEqual(a.read_text(),'original')
        self.assertEqual(b.read_text(),'existing')
        b.unlink()
        self.ops.move(a,b)
        a.write_text('new occupant')
        with self.assertRaises(ValueError): self.ops.undo()
        self.assertEqual(b.read_text(),'original')
        a.unlink()
        self.ops.undo()
        self.assertEqual(a.read_text(),'original')

    def test_directory_cannot_copy_into_itself(self):
        directory = self.root/'source'
        directory.mkdir()
        with self.assertRaises(ValueError): self.ops.copy(directory,directory/'nested')

    def test_symlinks_preserved_and_collision_detected(self):
        link = self.root/'link'
        link.symlink_to('missing')
        self.ops.copy(link,self.root/'copy')
        self.assertEqual(os.readlink(self.root/'copy'),'missing')
        with self.assertRaises(ValueError): self.ops.copy(link,self.root/'copy')

    def test_trash_recovery(self):
        source = self.root/'notes.txt'
        source.write_text('keep me')
        self.ops.remove(source)
        self.assertFalse(source.exists())
        self.assertIn(str(source),(self.root/'trash/recovery.tsv').read_text())
        self.ops.undo()
        self.assertEqual(source.read_text(),'keep me')

    def test_special_file_is_not_read(self):
        path = self.root/'pipe'
        os.mkfifo(path)
        self.assertEqual(preview(path).kind,'Special file')

    def test_preview_and_metadata_csv(self):
        path = self.root/'data.csv'
        path.write_text('a,b\n"one,two",3\n')
        self.assertEqual(metadata(path),{'rows':2,'columns':2})
        self.assertIn('one,two │ 3',preview(path).lines)

    @unittest.skipUnless(shutil.which('pdfinfo') and shutil.which('pdftotext'),'Poppler unavailable')
    def test_pdf_pages_drive_relief(self):
        path = self.root/'book.pdf'
        make_pdf(path,32)
        self.assertEqual(metadata(path)['pages'],32)
        self.assertGreater(mountain_height(512),mountain_height(4))
        self.assertTrue(any('Page 1 of 32' in line for line in preview(path).lines))
        self.assertIn('?', ''.join(terrain('pdf',{})))

    def test_bash_cd_and_find_paths_with_spaces(self):
        folder = self.root/'a folder'
        folder.mkdir()
        (folder/'a file.pdf').write_text('fixture')
        result = bash_destinations("cd 'a folder'",self.root)
        self.assertEqual(result['results'][0]['path'],str(folder))
        result = bash_destinations("find . -name '*.pdf' -print0",self.root)
        self.assertEqual(Path(result['results'][0]['path']),folder/'a file.pdf')

    def test_bash_failure_and_nonpaths(self):
        with self.assertRaises(ValueError): bash_destinations('false',self.root)
        self.assertEqual(bash_destinations('printf not-a-file',self.root)['results'],[])

    def test_terminal_control_and_unicode(self):
        self.assertNotIn('\x1b',clean('\x1b[31m恶意.txt'))
        self.assertIn('恶意',clean('恶意.txt'))

    def test_directory_facts_measure_tributary_and_weather(self):
        folder = self.root/'node_modules'
        folder.mkdir()
        for i in range(3): (folder/str(i)).touch()
        old = folder/'old'
        old.touch()
        os.utime(old,(0,0))
        facts = metadata(folder)
        self.assertEqual(facts['items'],4)
        self.assertEqual(facts['changed_day'],3)
        self.assertTrue(facts['generated'])
        self.assertEqual(metadata(self.root/'node_modules'/'..'/'node_modules')['items'],4)

    def test_archive_is_a_glacier_with_real_index_counts(self):
        import zipfile
        from branchfm.atlas import biome
        path = self.root/'bundle.zip'
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('a.txt','a'*5000)
            archive.writestr('b/c.txt','hello')
        facts = metadata(path)
        self.assertEqual(biome(path),'archives')
        self.assertEqual(facts['entries'],2)
        self.assertEqual(facts['unpacked'],5005)
        self.assertLess(facts['ratio'],0.2)
        self.assertEqual(biome(self.root/'disk.vhdx'),'disks')
        self.assertEqual(biome(self.root/'tool.exe'),'binaries')
        self.assertEqual(biome(self.root/'store.sqlite'),'databases')
        for kind in ('archives','binaries','disks','databases'):
            self.assertTrue(terrain(kind))

    def test_docx_page_count_is_read_not_invented(self):
        import zipfile
        path = self.root/'report.docx'
        with zipfile.ZipFile(path,'w') as archive:
            archive.writestr('docProps/app.xml','<Properties><Pages>42</Pages></Properties>')
        self.assertEqual(metadata(path),{'pages':42})
        with zipfile.ZipFile(self.root/'blank.docx','w') as archive:
            archive.writestr('word/document.xml','<w:document/>')
        self.assertEqual(metadata(self.root/'blank.docx'),{})

    def test_filesystem_zone_for_climate(self):
        from branchfm.service import filesystem
        facts = filesystem(self.root)
        self.assertIn(facts['zone'],('native','windows','network','ephemeral'))
        self.assertTrue(facts['writable'])

    def test_expanded_siblings_have_disjoint_bands(self):
        for name in ('alpha','beta'):
            folder = self.root/name
            folder.mkdir()
            for i in range(8): (folder/str(i)).touch()
        root = Node(self.root)
        root.refresh()
        for path in root.entries:
            root.children[path] = Node(path,root)
            root.children[path].refresh()
        nodes = layout(root)
        self.assertEqual(len(nodes),3)
        self.assertGreater(nodes[2].y,nodes[1].y+len(nodes[1].entries))


if __name__=='__main__': unittest.main()
