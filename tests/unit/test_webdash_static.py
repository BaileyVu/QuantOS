"""The read-only asset route cannot expose files outside its dashboard folder."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from quantos.paper import webdash


class StaticAssetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.page = root / 'webdash.html'
        self.page.write_text('<html>dashboard</html>')
        self.assets = root / 'static' / 'aura-zenith'
        self.assets.mkdir(parents=True)
        (self.assets / 'icon.svg').write_text('<svg/>')
        (self.assets / 'secret.txt').write_text('private')
        (root / 'outside.svg').write_text('outside')
        (self.assets / 'linked.svg').symlink_to(root / 'outside.svg')
        with patch.object(webdash, 'ThreadingHTTPServer') as server:
            webdash.serve([], None, 8788)
            self.handler = server.call_args.args[1]
            self.assertEqual(server.call_args.args[0], ('127.0.0.1', 8788))

    def request(self, path):
        h = self.handler.__new__(self.handler)
        h.path, h.wfile = path, io.BytesIO()
        h.send_response = Mock()
        h.send_header = Mock()
        h.send_error = Mock()
        h.end_headers = Mock()
        with patch.object(webdash, 'PAGE', self.page):
            h.do_GET()
        return h

    def test_serves_only_asset_bytes_and_mime(self):
        h = self.request('/static/aura-zenith/icon.svg?v=1')
        h.send_response.assert_called_once_with(200)
        h.send_header.assert_any_call('Content-Type', 'image/svg+xml')
        self.assertEqual(h.wfile.getvalue(), b'<svg/>')

    def test_traversal_unknown_types_and_symlinks_are_rejected(self):
        for path in ('../outside.svg', '%2e%2e/outside.svg', 'secret.txt', 'linked.svg', 'missing.svg', ''):
            with self.subTest(path=path):
                h = self.request('/static/aura-zenith/' + path)
                h.send_error.assert_called_once_with(404)
                self.assertEqual(h.wfile.getvalue(), b'')
