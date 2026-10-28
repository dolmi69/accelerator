import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile
from unittest.mock import patch

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse

from founder.models import User, StartupProfile, LabSiteVersion, LabManualDraft, LabBuilderDraft, ProjectMember
from founder.services import manual_site as manual
from founder.services.visual_builder import DocumentError
from founder.services.generated_backend import validate_files

HTML='''<!doctype html><html><head><title>Старый сайт</title><meta http-equiv="refresh" content="0;url=https://bad.test"><link rel="stylesheet" href="/assets/site.css"><style>body{margin:0;min-height:100vh}h1 span{color:green}</style></head><body data-project="unchanged"><header><h1 id="brand">Моя <span>смета</span> &amp; расчёты</h1><p>Точный расчёт &lt;до копейки&gt;.</p></header><button id="calculate" onclick="calculate()">Рассчитать</button><a href="/about">О сервисе</a><input id="price" name="price" type="number" value="10" placeholder="Стоимость"><img id="photo" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" srcset="x 1x" alt="Old"><svg id="art" viewBox="0 0 100 100"><rect width="100" height="100" fill="#147d73"/><circle cx="50" cy="50" r="20"/></svg><p id="result"></p><script>function calculate(){document.getElementById('result').textContent=Number(document.getElementById('price').value)*2;}const literal='<b>not markup</b>';fetch('/api/estimate').catch(()=>{});</script><script src="/assets/app.js"></script><iframe src="https://bad.test"></iframe></body></html>'''
SERVER="def application(environ, start_response):\n    start_response('200 OK', [('Content-Type','application/json')])\n    return [b'{\"items\": []}']\n"
FILES={'index.html':HTML,'app.py':SERVER,'assets/site.css':'h1{font-size:48px}.hero{background:#fff}','assets/app.js':'// Must remain byte-for-byte\nconst keep = true;'}


class ManualSourceTests(TestCase):
    def setUp(self):self.source=manual.Source(HTML)
    def node(self,id):return next(n for n in self.source.nodes.values()if n.attrs.get('id')==id)
    def changes(self,**values):return {'texts':{},'elements':{},**values}
    def render(self,changes,**options):return manual.render(self.source,changes,image_url=lambda key:'data:image/webp;base64,AAAA',**options)

    def test_noop_is_byte_identical(self):self.assertEqual(self.render(self.changes()),HTML)
    def test_text_edit_preserves_scripts_attributes_and_nested_markup(self):
        node=next(n for n in self.source.nodes.values()if n.tag=='span');slot=self.source.texts[node.texts[0]]
        changes=self.changes(texts={slot['id']:'новая смета <script>'})
        result=self.render(changes)
        expected=HTML[:slot['start']]+'новая смета &lt;script&gt;'+HTML[slot['end']:]
        self.assertEqual(result,expected)
        self.assertIn('onclick="calculate()"',result)
        self.assertIn("const literal='<b>not markup</b>'",result)
        self.assertNotIn('data-bm-',result)
    def test_entity_ranges_are_precise(self):
        node=next(n for n in self.source.nodes.values()if n.tag=='p');slot=self.source.texts[node.texts[0]]
        self.assertEqual(slot['text'],'Точный расчёт <до копейки>.')
        result=self.render(self.changes(texts={slot['id']:'Новый & точный'}))
        self.assertIn('<p>Новый &amp; точный</p>',result)
        self.assertIn('<header>',result)
    def test_style_changes_only_selected_opening_tag(self):
        node=self.node('calculate');changes=self.changes(elements={node.key:{'style':{'color':'#147d73','fontSize':22}}})
        clean=manual.validate_changes(self.source,changes,[]);result=self.render(clean)
        self.assertEqual(result[:node.start],HTML[:node.start])
        self.assertEqual(result[result.index('Рассчитать'):],HTML[HTML.index('Рассчитать'):])
        self.assertIn('onclick="calculate()"',result);self.assertIn('font-size:22px!important',result)
    def test_image_replacement_preserves_ids_classes_and_svg_outer_markup(self):
        img=self.node('photo');svg=self.node('art')
        result=self.render(self.changes(elements={img.key:{'image':'asset'},svg.key:{'image':'asset'}}))
        self.assertIn('id="photo"',result);self.assertNotIn('srcset=',result)
        self.assertIn('viewBox="0 0 100 100"',result)
        self.assertIn('<image href="data:image/webp;base64,AAAA"',result)
        self.assertIn('<script>function calculate()',result)
    def test_preview_strips_active_content_but_inlines_owned_css(self):
        result=self.render(self.changes(),preview=True,files=FILES)
        self.assertNotIn('<script',result);self.assertNotIn('onclick=',result)
        self.assertNotIn('<iframe',result);self.assertNotIn('http-equiv="refresh"',result)
        self.assertIn('h1{font-size:48px}',result)
        self.assertIn("script-src 'none'",result)
        self.assertIn('<bl-text data-bm-text=',result)
    def test_headless_preview_gets_csp_and_self_closing_svg_stays_valid(self):
        source=manual.Source('<body><svg id="logo" viewBox="0 0 10 10"/><h1>Текст</h1></body>')
        svg=next(n for n in source.nodes.values()if n.tag=='svg')
        changes=self.changes(elements={svg.key:{'image':'asset'}})
        result=manual.render(source,changes,image_url=lambda key:'/image.webp',preview=True)
        self.assertIn("script-src 'none'",result)
        self.assertIn('<image href="/image.webp"',result)
        self.assertIn('/></svg><h1',result)
        self.assertIn('Текст',result)

    def test_svg_replacement_preview_has_no_overlapping_ranges(self):
        result=self.render(self.changes(elements={self.node('art').key:{'image':'asset'}}),preview=True,files=FILES)
        self.assertIn('<image href=',result)
        self.assertNotIn('const literal',result)
    def test_untrusted_changes_cannot_change_code_or_inject_css_urls(self):
        cases=[{'elements':{self.node('calculate').key:{'onclick':'evil()'}}},
            {'elements':{self.node('calculate').key:{'style':{'backgroundColor':'url(https://bad.test)'}}}},
            {'elements':{self.node('calculate').key:{'style':{'fontSize':999999}}}},
            {'texts':{'missing':'Oops'}}, {'elements':{self.node('photo').key:{'image':'foreign'}}},
            {'elements':{next(n.key for n in self.source.nodes.values()if n.tag=='a'):{'href':'javascript:alert(1)'}}}]
        for values in cases:
            with self.subTest(values=values),self.assertRaises(DocumentError):manual.validate_changes(self.source,self.changes(**values),[])


@override_settings(REDIS_URL='',DEBUG=True,BRUNOHOST_BASE_URL='http://localhost:8000',BRUNOHOST_ENGINE='disabled')
class ManualEditorTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        override=override_settings(MEDIA_ROOT=Path(self.temp.name)/'media',BRUNOHOST_ROOT=Path(self.temp.name)/'host');override.enable();self.addCleanup(override.disable)
        self.user=User.objects.create_user(username='manual-owner',email='manual@example.com')
        self.other=User.objects.create_user(username='manual-other',email='manual-other@example.com')
        self.startup=StartupProfile.objects.create(owner=self.user,name='AI смета')
        self.source=LabSiteVersion.objects.create(startup=self.startup,kind='generated',html=HTML,backend_files=FILES,prompt='Смета',model='test')
        self.client.force_login(self.user);self.parsed=manual.Source(HTML)
        text=next(t for t in self.parsed.texts.values()if t['text']=='смета')
        self.changes={'texts':{text['id']:'суперсмета'},'elements':{}}
    def url(self,name=''):
        return reverse('lab_manual'+('_'+name if name else ''),args=[self.startup.pk,self.source.pk])
    def post(self,name,data):return self.client.post(self.url(name),json.dumps(data),content_type='application/json')
    def save(self,changes=None,revision=1):
        return self.post('save',{'changes':changes if changes is not None else self.changes,'revision':revision,'digest':self.parsed.digest})
    def snapshot(self,revision):return self.post('snapshot',{'revision':revision})
    def test_opening_ai_version_uses_actual_site_and_leaves_block_draft_alone(self):
        block=LabBuilderDraft.objects.create(startup=self.startup,document={'keep':'native draft'})
        with patch('founder.services.qwen.generate_code')as ai:response=self.client.get(self.url())
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Ручной редактор AI-сайта');self.assertContains(response,'смета')
        block.refresh_from_db();self.assertEqual(block.document,{'keep':'native draft'});ai.assert_not_called()
        self.assertEqual(LabSiteVersion.objects.count(),1)
    def test_generated_snapshot_preserves_server_css_js_and_previous_version(self):
        self.assertEqual(self.save().status_code,200)
        response=self.snapshot(2);self.assertEqual(response.status_code,200,response.content)
        version=LabSiteVersion.objects.get(pk=response.json()['version'])
        self.assertEqual(version.kind,'generated');self.assertIn('суперсмета',version.html)
        self.assertEqual(version.html,version.backend_files['index.html'])
        for path in ('app.py','assets/site.css','assets/app.js'):self.assertEqual(version.backend_files[path],FILES[path])
        self.source.refresh_from_db();self.assertEqual(self.source.backend_files,FILES)
        self.assertEqual(version.source_id,self.source.pk)
        self.assertEqual(version.input_tokens,0);self.assertEqual(version.output_tokens,0)
        self.assertNotIn('data-bm-',version.html);validate_files(version.backend_files)
        second=self.snapshot(2);self.assertEqual(second.json()['version'],str(version.pk))
        normal=self.client.get(response.json()['url']);self.assertEqual(normal.status_code,200)
    def test_static_snapshot_and_hosting_publish_actual_manual_content(self):
        self.source.kind='static';self.source.backend_files={};self.source.save()
        self.assertEqual(self.save().status_code,200)
        version=self.snapshot(2).json()['version']
        response=self.client.post(reverse('lab_hosting',args=[self.startup.pk]),{'version':version},HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code,200)
        host=self.startup.hosted_site.slug+'.localhost:8000'
        self.assertContains(Client().get('/',HTTP_HOST=host),'суперсмета')
    def test_revisions_and_digest_protect_concurrent_edits(self):
        self.assertEqual(self.save().status_code,200)
        self.assertEqual(self.save(revision=1).status_code,409)
        self.assertEqual(self.post('save',{'changes':self.changes,'revision':2,'digest':'wrong'}).status_code,409)
        self.assertEqual(self.snapshot(1).status_code,409)
        self.assertEqual(LabSiteVersion.objects.count(),1)
    def upload(self,color='#147d73'):
        buffer=io.BytesIO();Image.new('RGB',(90,120),color).save(buffer,format='PNG')
        response=self.client.post(reverse('lab_builder_upload',args=[self.startup.pk]),{'image':SimpleUploadedFile('photo.png',buffer.getvalue(),content_type='image/png')})
        self.assertEqual(response.status_code,201,response.content)
        return response.json()

    def test_upload_and_svg_image_replacement_stays_outside_ai_code_and_is_exported(self):
        image=self.upload()
        svg=next(n for n in self.parsed.nodes.values()if n.attrs.get('id')=='art')
        self.changes['elements'][svg.key]={'image':image['id'],'alt':'Наше фото'}
        preview=self.post('preview',{'changes':self.changes,'digest':self.parsed.digest})
        self.assertEqual(preview.status_code,200,preview.content)
        self.assertIn(image['url'],preview.json()['html'])
        self.assertEqual(self.save().status_code,200)
        version=LabSiteVersion.objects.get(pk=self.snapshot(2).json()['version'])
        path='assets/bruno-images/'+image['id']+'.webp'
        self.assertIn('/'+path,version.html)
        self.assertNotIn('data:image/webp;base64,',version.html)
        self.assertNotIn(image['url'],version.html)
        self.assertEqual(version.presentation['manual_assets'],[image['id']])
        editor_preview=self.client.post(reverse('lab_manual_preview',args=[self.startup.pk,version.pk]),json.dumps({'changes':{'texts':{},'elements':{}},'digest':manual.Source(version.html).digest}),content_type='application/json')
        self.assertEqual(editor_preview.status_code,200)
        self.assertIn(image['url'],editor_preview.json()['html'])
        self.assertNotIn('/assets/bruno-images/',editor_preview.json()['html'])
        self.assertEqual(version.backend_files['app.py'],SERVER)
        from founder.services.manual_assets import stage
        folder=Path(self.temp.name)/'runtime';stage(version,folder)
        self.assertEqual(Image.open(folder/path).format,'WEBP')
        response=self.client.get(self.url('download'))
        with ZipFile(io.BytesIO(b''.join(response.streaming_content)))as archive:
            self.assertEqual(archive.read('site/app.py').decode(),SERVER)
            self.assertEqual(archive.read('site/'+path),(folder/path).read_bytes())
        response=self.client.get(reverse('lab_preview',args=[self.startup.pk,version.pk]))
        self.assertContains(response,'data:image/webp;base64,')

    def test_static_host_only_serves_published_owned_images_and_portable_download(self):
        image=self.upload();unused=self.upload('#d0a159')
        self.source.kind='static';self.source.backend_files={};self.source.save()
        img=next(n for n in self.parsed.nodes.values()if n.tag=='img')
        self.changes['elements'][img.key]={'image':image['id']}
        self.assertEqual(self.save().status_code,200)
        version=self.snapshot(2).json()['version']
        self.client.post(reverse('lab_hosting',args=[self.startup.pk]),{'version':version},HTTP_ACCEPT='application/json')
        host=self.startup.hosted_site.slug+'.localhost:8000';path='/assets/bruno-images/'+image['id']+'.webp'
        public=Client();response=public.get(path,HTTP_HOST=host)
        self.assertEqual(response.status_code,200);self.assertEqual(response['Content-Type'],'image/webp')
        self.assertEqual(public.head(path,HTTP_HOST=host).content,b'')
        self.assertEqual(public.post(path,HTTP_HOST=host,HTTP_ORIGIN='http://'+host).status_code,405)
        self.assertEqual(public.get('/assets/bruno-images/'+unused['id']+'.webp',HTTP_HOST=host).status_code,404)
        self.assertContains(self.client.get(self.url('download')),'data:image/webp;base64,')

    def test_repeated_manual_versions_keep_data_lineage(self):
        self.save();first=LabSiteVersion.objects.get(pk=self.snapshot(2).json()['version'])
        text=next(iter(self.changes['texts']));self.changes['texts'][text]='Третья смета'
        self.save(revision=2);second=LabSiteVersion.objects.get(pk=self.snapshot(3).json()['version'])
        self.assertEqual(second.source_id,first.pk)
        self.assertEqual(second.backend_files['app.py'],first.backend_files['app.py'])
        self.assertEqual(self.parsed.digest,manual.Source(self.source.html).digest)

    def test_ai_version_reuses_owned_image_manifest_and_ignores_bad_ids(self):
        from founder.lab_views import _create_version
        from founder.services.manual_assets import records,presentation
        image=self.upload();path='/assets/bruno-images/'+image['id']+'.webp'
        html=HTML.replace('id="photo"','id="photo" data-manual-image="'+path+'"')
        version=_create_version(startup=self.startup,kind='generated',html=html,backend_files={**FILES,'index.html':html},report={})
        self.assertEqual(version.presentation['manual_assets'],[image['id']])
        self.assertEqual(len(records(version)),1)
        self.assertEqual(presentation(self.startup,'/assets/bruno-images/'+'-'*36+'.webp',{},{}),{})
        foreign=StartupProfile.objects.create(owner=self.other,name='Чужой')
        copy=SimpleNamespace(startup_id=foreign.pk,presentation=version.presentation,html=html,backend_files={})
        self.assertEqual(records(copy),[])
    def test_other_users_and_viewers_cannot_open_or_change_manual_drafts(self):
        ProjectMember.objects.create(startup=self.startup,user=self.other,role='viewer',status='active',invited_by=self.user)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url()).status_code,404)
        for name in ('save','preview','snapshot'):self.assertEqual(self.post(name,{}).status_code,404)
        self.assertEqual(self.client.get(self.url('download')).status_code,404)
    def test_csrf_required_and_deep_json_is_rejected(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.user)
        self.assertEqual(client.post(self.url('save'),'{}',content_type='application/json').status_code,403)
        response=self.client.post(self.url('preview'),'['*100+'0'+']'*100,content_type='application/json')
        self.assertEqual(response.status_code,400)
    def test_preview_has_no_site_scripts_or_event_handlers(self):
        response=self.post('preview',{'changes':self.changes,'digest':self.parsed.digest})
        self.assertEqual(response.status_code,200)
        html=response.json()['html'];self.assertIn('суперсмета',html)
        self.assertNotIn('<script',html);self.assertNotIn('onclick=',html);self.assertNotIn('http-equiv="refresh"',html)
    def test_noop_snapshot_returns_source_without_extra_version(self):
        self.assertEqual(self.save(changes={'texts':{},'elements':{}}).status_code,200)
        self.assertEqual(self.snapshot(2).json()['version'],str(self.source.pk))
        self.assertEqual(LabSiteVersion.objects.count(),1)
    def test_invalid_change_keeps_previous_draft(self):
        self.assertEqual(self.save().status_code,200)
        invalid={'texts':{'missing':'X'},'elements':{}}
        self.assertEqual(self.save(invalid,2).status_code,400)
        self.assertEqual(LabManualDraft.objects.get().changes,self.changes)
