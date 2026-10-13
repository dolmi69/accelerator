"""Component export and edits remain self-contained and preserve user code."""
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from django.test import SimpleTestCase
from founder.services.site_components import assets, attach_components, model_source
from founder.services.site_generator import generate_site
from founder.services.site_patches import fragments
from founder.services.site_editor import customize
from founder.services.django_builder import project_files
from founder.services.qwen import CodeResult

HTML='<!doctype html><html><head><title>Demo</title><style>.my-hero{padding:33px}</style></head><body><section id="hero" class="forge-section"><div class="forge-shell"><h1 class="forge-title">Demo</h1><p>Keep this data</p></div></section><script>const myFormula=x=>x*17;</script></body></html>'
REPORT={'summary':'Изменил заголовок.', 'completed':['Данные сохранены.'], 'not_done':[]}

class ComponentIntegrationTests(SimpleTestCase):
    def test_generated_components_get_local_assets_once_and_custom_css_has_priority(self):
        reply=CodeResult(json.dumps({'html':HTML,'report':REPORT}), 'Qwen',100,150)
        with patch('founder.services.site_generator.generate_code',return_value=reply) as api:
            result=generate_site('Создай главную',report=True)
        api.assert_called_once()
        self.assertEqual(result.text.count('data-forge-components="v1"'),2)
        self.assertIn('@layer forge-components {', assets()[0])
        self.assertLess(result.text.index(assets()[0]),result.text.index('.my-hero{'))
        self.assertEqual(attach_components(result.text),result.text)
        self.assertEqual(model_source(result.text),HTML)
        self.assertNotIn(assets()[0],api.call_args.args[0])
        self.assertNotIn(assets()[1],api.call_args.args[0])

    def test_patch_does_not_send_shared_assets_and_changes_only_requested_heading(self):
        original=attach_components(HTML)
        reply=CodeResult(json.dumps({'changes':[{'target':'full','find':'>Demo</h1>','replace':'>New</h1>'}], 'report':REPORT}), 'Qwen',100,150)
        with patch('founder.services.site_generator.generate_code',return_value=reply) as api:
            result=generate_site('Измени главный заголовок',previous_html=original,report=True)
        self.assertEqual(result.text,original.replace('>Demo</h1>','>New</h1>'))
        payload=json.loads(api.call_args.args[1][0]['content'])
        self.assertEqual(payload['fragments'][0]['html'],HTML)
        self.assertNotIn('data-forge-components',payload['fragments'][0]['html'])
        self.assertIn('const myFormula=x=>x*17;',result.text)

    def test_explicit_scope_uses_original_region_key_after_asset_removal(self):
        original=attach_components(HTML)
        region=next(part for part in fragments(original) if original[part.start:part.end].startswith('<section id="hero"'))
        reply=CodeResult(json.dumps({'changes':[{'target':region.key,'find':'>Demo</h1>','replace':'>Scoped</h1>'}], 'report':REPORT}), 'Qwen',100,150)
        with patch('founder.services.site_generator.generate_code',return_value=reply) as api:
            result=generate_site('Измени заголовок',previous_html=original,edit_scope=region.key,report=True)
        self.assertEqual(result.text,original.replace('>Demo</h1>','>Scoped</h1>'))
        payload=json.loads(api.call_args.args[1][0]['content'])
        self.assertEqual(payload['fragments'][0]['id'],region.key)
        self.assertNotIn('<title>',payload['fragments'][0]['html'])

    def test_plain_design_is_not_forced_into_component_layout(self):
        plain=HTML.replace('class="forge-section"','class="custom"').replace('class="forge-shell"','class="custom-grid"').replace('class="forge-title"','class="custom-title"')
        self.assertEqual(attach_components(plain),plain)
        self.assertEqual(attach_components(plain.replace('myFormula=x=>x*17','myFormula="forge-button"')),plain.replace('myFormula=x=>x*17','myFormula="forge-button"'))

    def test_script_literals_and_comments_are_not_removed_as_managed_html(self):
        source=HTML.replace('</body>','<!-- <style data-forge-components="v1">not a style</style> --><script>const markup=\'<style data-forge-components="v1">keep string</style>\';</script></body>')
        self.assertEqual(model_source(source),source)

    def test_ready_theme_controls_and_django_export_keep_the_component_page(self):
        generated=attach_components(HTML)
        customized=customize(generated,{'palette':'dark','radius':'24'})
        self.assertIn('--primary-color:#60a5fa',customized)
        self.assertIn('--forge-on-accent:#111827',customized)
        self.assertIn('.forge-button{border-radius:24px!important}',customized)
        version=SimpleNamespace(kind='django',html=customized,presentation={},backend_modules=['accounts','registration'],startup=SimpleNamespace(name='Demo'),startup_id=uuid4(),pk=uuid4())
        exported=project_files(version)
        self.assertEqual(exported['prototype.html'].decode(),customized)
        self.assertIn(assets()[1],exported['prototype.html'].decode())
