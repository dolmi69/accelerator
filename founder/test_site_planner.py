import json
from unittest.mock import patch
from django.test import SimpleTestCase
from founder.services.qwen import CodeResult, QwenOutputError
from founder.services.site_planner import chunks, parse_plan, plan_messages
from founder.services.site_generator import generate_site

HTML = '<!doctype html><html><head><style>body{color:black}</style></head><body><section><h1>Demo</h1>' + ('<p>Example content</p>\n' * 1100) + '</section><button>Buy</button></body></html>'

class PlannerTests(SimpleTestCase):
    def test_chunks_cover_document_exactly(self):
        parts = chunks(HTML)
        self.assertEqual(''.join(HTML[p.start:p.end] for p in parts), HTML)
        self.assertTrue(all(a.end == b.start for a,b in zip(parts, parts[1:])))
        messages, _ = plan_messages('Change button', HTML)
        self.assertLess(len(messages[0]['content']), len(HTML))

    def test_invalid_plan_never_reaches_edit_call(self):
        with patch('founder.services.site_generator.generate_code', return_value=CodeResult('{"targets":["unknown"]}', 'Qwen', 1, 2)) as api:
            with self.assertRaises(QwenOutputError):
                generate_site('Change button', previous_html=HTML)
            self.assertEqual(api.call_count, 1)
        for text in ['{}', '{"targets":[]}', '{"targets":["c1","c1"]}', '{"targets":"c1"}']:
            with self.assertRaises(QwenOutputError): parse_plan(text, chunks(HTML), HTML)

    def test_large_site_uses_one_edit_call_and_preserves_other_html(self):
        response = CodeResult(json.dumps({'changes':[{'target':'full','find':'Buy','replace':'Browse'}]}), 'Qwen', 20, 30, 'edit')
        with patch('founder.services.site_generator.generate_code', return_value=response) as api:
            result = generate_site('Change button', previous_html=HTML)
        self.assertEqual(result.text, HTML.replace('Buy','Browse'))
        self.assertEqual((result.input_tokens, result.output_tokens), (20,30))
        self.assertEqual(result.request_ids, ('edit',))
        api.assert_called_once()
        self.assertIn('body{color:black}', api.call_args.args[1][0]['content'])
