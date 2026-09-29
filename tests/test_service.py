import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from branchfm.service import Service


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for i in range(125): (self.root/f'file-{i:03d}.txt').write_text(f'content {i}')
        (self.root/'.hidden').write_text('hidden')
        self.service = Service(self.root)
        self.thread = threading.Thread(target=self.service.serve_forever,daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.service.server_port}'

    def tearDown(self):
        self.service.shutdown()
        self.service.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self,path,data=None,auth=True):
        headers = {'Authorization':'Bearer '+self.service.token} if auth else {}
        request = Request(self.base+path,data=None if data is None else json.dumps(data).encode(),headers=headers)
        with urlopen(request,timeout=15) as response:
            return json.load(response)

    def test_auth_required_for_reads_and_commands(self):
        with self.assertRaises(HTTPError) as failure: self.request('/list',auth=False)
        self.assertEqual(failure.exception.code,403)
        with self.assertRaises(HTTPError) as failure:
            self.request('/command',{'source':str(self.root),'command':'touch should-not-exist'},auth=False)
        self.assertEqual(failure.exception.code,403)
        self.assertFalse((self.root/'should-not-exist').exists())

    def test_pagination_and_focused_destination(self):
        from urllib.parse import urlencode
        first = self.request('/list')
        self.assertEqual(first['total'],125)
        self.assertEqual(len(first['entries']),120)
        selected = self.request('/list?'+urlencode({'focus':str(self.root/'file-124.txt')}))
        self.assertEqual(selected['page'],1)
        self.assertIn('file-124.txt',[e['name'] for e in selected['entries']])

    def test_preview_and_real_bash_result(self):
        from urllib.parse import urlencode
        result = self.request('/preview?'+urlencode({'path':str(self.root/'file-000.txt')}))
        self.assertIn('content 0',result['lines'])
        result = self.request('/command',{'source':str(self.root),'command':"printf '%s\\n' file-124.txt"})
        self.assertEqual(result['results'][0]['path'],str(self.root/'file-124.txt'))

    def test_survey_and_filesystem_facts(self):
        (self.root/'sub').mkdir()
        (self.root/'sub'/'a.txt').write_text('a')
        result = self.request('/survey',{'paths':[str(self.root/'sub'),str(self.root/'file-000.txt')]})
        self.assertEqual(result['facts'][str(self.root/'sub')]['items'],1)
        self.assertNotIn(str(self.root/'file-000.txt'),result['facts'])
        self.assertIn('zone',self.request('/list')['filesystem'])

    def test_explicit_destination_can_reveal_hidden_file(self):
        from urllib.parse import urlencode
        result = self.request('/list?'+urlencode({'focus':str(self.root/'.hidden')}))
        self.assertIn('.hidden',[e['name'] for e in result['entries']])


if __name__=='__main__': unittest.main()
