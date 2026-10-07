"""Frame access stays closed outside the exact local launcher context."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.test import TestCase, override_settings


class LabViewerTests(TestCase):
    def test_launcher_toolbar_and_exact_ancestor_without_exported_secrets(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'prototype.html').write_text('<html><head></head><body>Site</body></html>')
            context = root / '.lab-viewer.json'
            with override_settings(BASE_DIR=root, DEBUG=True):
                page = self.client.get('/login/')
                self.assertNotContains(page, 'data-lab-return')
                self.assertEqual(page['X-Frame-Options'], 'DENY')
                self.assertIn("frame-ancestors 'none'", page['Content-Security-Policy'])
                context.write_text(json.dumps({'origin': 'http://127.0.0.1:8000',
                    'return_path': f"/startups/{settings.SITE['project_id']}/lab/?version=abc#backend-title"}))
                page = self.client.get('/login/')
                self.assertContains(page, 'Вернуться в лабораторию')
                self.assertNotIn('X-Frame-Options', page)
                self.assertIn("frame-ancestors 'self' http://127.0.0.1:8000", page['Content-Security-Policy'])
                prototype = self.client.get('/prototype/')
                self.assertContains(prototype, 'cofounder:lab-exit')
                self.assertIn('sandbox allow-scripts', prototype['Content-Security-Policy'])
                self.assertNotIn('allow-same-origin', prototype['Content-Security-Policy'])
                self.assertIn('http://127.0.0.1:8000', prototype['Content-Security-Policy'])
                self.assertNotIn('X-Frame-Options', prototype)
                with override_settings(DEBUG=False):
                    self.assertNotContains(self.client.get('/login/'), 'data-lab-return')
                for origin in ['https://evil.example', 'http://localhost.evil.example', 'http://localhost:8000/path', 'http://localhost:bad']:
                    context.write_text(json.dumps({'origin': origin, 'return_path': f"/startups/{settings.SITE['project_id']}/lab/?version=abc"}))
                    page = self.client.get('/login/')
                    self.assertNotContains(page, 'data-lab-return')
                    self.assertEqual(page['X-Frame-Options'], 'DENY')
