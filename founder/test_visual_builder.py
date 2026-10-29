import copy
import io
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from founder.models import User, StartupProfile, ProjectMember, LabBuilderAsset, LabBuilderDraft, LabSiteVersion, HostedSite
from founder.services import visual_builder as builder


@override_settings(DEBUG=True, BRUNOHOST_BASE_URL='http://localhost:8000', BRUNOHOST_ENGINE='disabled', REDIS_URL='')
class VisualBuilderTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=Path(self.temp.name)/'media', BRUNOHOST_ROOT=Path(self.temp.name)/'host')
        self.settings_override.enable(); self.addCleanup(self.settings_override.disable)
        self.owner = User.objects.create_user(username='builder-owner', email='builder@example.com')
        self.other = User.objects.create_user(username='builder-other', email='other@example.com')
        self.startup = StartupProfile.objects.create(owner=self.owner, name='Своя история')
        self.foreign = StartupProfile.objects.create(owner=self.other, name='Другой проект')
        self.client.force_login(self.owner)
        self.document = builder.default_document(self.startup)

    def url(self, name, *extra):
        return reverse('lab_builder' + ('_'+name if name else ''), args=[self.startup.pk,*extra])

    def post(self, name, data):
        return self.client.post(self.url(name), json.dumps(data), content_type='application/json')

    def draft(self):
        return self.client.get(self.url('draft')).json()

    def save(self, document=None, revision=None):
        current=self.draft()
        return self.post('draft',{'document':document or self.document,'revision':revision if revision is not None else current['revision']})

    def version(self, document=None):
        response=self.save(document);self.assertEqual(response.status_code,200,response.content)
        response=self.post('snapshot',{'revision':response.json()['revision']});self.assertEqual(response.status_code,200,response.content)
        return LabSiteVersion.objects.get(pk=response.json()['version'])

    def upload(self, name='photo.png', color='red'):
        data=io.BytesIO();Image.new('RGB',(120,90),color).save(data,format='PNG')
        return self.client.post(self.url('upload'), {'image':SimpleUploadedFile(name,data.getvalue(),content_type='image/png')})

    def add_page(self, document):
        document['pages'].insert(1, {'id':'about-page','title':'О проекте','slug':'about','description':'Наша история','blocks':[builder.new_block('text')]})
        return document

    def test_starter_has_editable_pages_working_local_links_and_no_required_uploads(self):
        document=builder.validate_document(self.document)
        self.assertEqual([p['slug'] for p in document['pages']], ['', 'features', 'guide'])
        self.assertFalse(builder.asset_ids(document))
        pages={builder.page_path(p):p for p in document['pages']}
        for page in document['pages']:
            html=builder.render_document(document,page['id'],editor=True)
            self.assertNotIn('<script',html)
            self.assertIn('data-block-type="nav"',html)
            self.assertIn('data-block-type="footer"',html)
            for block in page['blocks']:
                content=[block['props'],*block['props'].get('items',[]),
                         *(e['content'] for e in block['props'].get('elements',[]))]
                for item in content:
                    link=item.get('link','')
                    if link.startswith(('/', '#')):
                        path,_,anchor=link.partition('#')
                        target=pages[path] if path else page
                        if anchor:
                            self.assertIn(anchor,[b['id'] for b in target['blocks']])
        canvas=next(b for p in document['pages'] for b in p['blocks'] if b['type']=='canvas')
        self.assertEqual({e['type'] for e in canvas['props']['elements']},{'shape','text','button','art'})
        independent=builder.default_document(self.startup)
        independent['pages'][1]['blocks'][3]['props']['elements'][0]['style']['background']='#ffffff'
        self.assertNotEqual(independent['pages'][1]['blocks'][3],canvas)

    def test_opening_editor_keeps_an_existing_custom_draft_instead_of_installing_starter(self):
        custom=copy.deepcopy(self.document)
        custom['pages']=[custom['pages'][0]]
        custom['pages'][0]['blocks'][1]['props']['title']='Мой готовый сайт'
        response=self.save(custom)
        self.assertEqual(response.status_code,200,response.content)
        revision=response.json()['revision']
        response=self.client.get(self.url(''))
        self.assertEqual(response.status_code,200)
        current=self.draft()
        self.assertEqual(current['document'],custom)
        self.assertEqual(current['revision'],revision)

    def test_editor_loads_with_catalog_and_draft_without_ai_call(self):
        with patch('founder.services.qwen.generate_code') as model:
            response=self.client.get(self.url(''))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Визуальный конструктор')
        self.assertContains(response,'visual-builder-config')
        self.assertEqual(LabBuilderDraft.objects.count(),1)
        self.assertEqual(LabSiteVersion.objects.count(),0)
        model.assert_not_called()

    def test_all_catalog_blocks_validate_and_render(self):
        self.document['pages'][0]['blocks']=[builder.new_block(kind) for kind in builder.CATALOG]
        validated=builder.validate_document(self.document)
        html=builder.render_document(validated,editor=True)
        for kind in builder.CATALOG:
            self.assertIn(f'data-block-type="{kind}"',html)
        self.assertNotIn('<script',html)

    def test_optimistic_save_does_not_overwrite_a_newer_draft(self):
        initial=self.draft()
        self.assertEqual(self.save(revision=initial['revision']).status_code,200)
        changed=copy.deepcopy(self.document);changed['pages'][0]['title']='Устаревшая вкладка'
        response=self.save(changed,initial['revision'])
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.draft()['document']['pages'][0]['title'],'Главная')

    def test_saved_builder_version_opens_in_normal_lab_with_working_preview(self):
        version=self.version()
        response=self.client.get(reverse('lab',args=[self.startup.pk])+'?version='+str(version.pk))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Визуальный конструктор')
        self.assertContains(response,'sandbox="allow-same-origin"')
        preview=self.client.get(reverse('lab_preview',args=[self.startup.pk,version.pk]))
        self.assertEqual(preview['X-Frame-Options'],'SAMEORIGIN')

    def test_snapshot_is_immutable_and_idempotent(self):
        version=self.version()
        response=self.post('snapshot',{'revision':self.draft()['revision']})
        self.assertEqual(response.json()['version'],str(version.pk))
        self.document['pages'][0]['blocks'][1]['props']['title']='Новая версия'
        changed=self.version()
        self.assertNotEqual(changed.pk,version.pk)
        self.assertEqual(changed.source_id,version.pk)
        version.refresh_from_db()
        self.assertNotIn('Новая версия',version.html)
        self.assertIn('Новая версия',changed.html)
        self.assertFalse(HostedSite.objects.exists())

    def test_script_markup_is_text_and_unsafe_links_rejected(self):
        self.document['pages'][0]['blocks'][1]['props']['title']='<script>alert(1)</script>'
        response=self.post('preview',{'document':self.document})
        self.assertEqual(response.status_code,200)
        self.assertIn('&lt;script&gt;',response.json()['html'])
        self.document['pages'][0]['blocks'][1]['props']['link']='javascript:alert(1)'
        self.assertEqual(self.save().status_code,400)

    def test_malformed_schema_returns_validation_error(self):
        mutations=[lambda d:d.update(theme={**d['theme'],'font':[]}),lambda d:d['pages'][0]['blocks'][0].update(type=[]),lambda d:d['pages'][0]['blocks'][0]['style'].update(surface=[]),lambda d:d['pages'][0]['blocks'][1]['props'].update(image={})]
        for change in mutations:
            with self.subTest(change=change):
                document=copy.deepcopy(self.document);change(document)
                self.assertEqual(self.save(document).status_code,400)

    def test_page_slugs_duplicates_and_size_are_bounded(self):
        self.add_page(self.document)
        self.document['pages'][1]['slug']='../admin'
        self.assertEqual(self.save().status_code,400)
        self.document['pages'][1]['slug']=''
        self.assertEqual(self.save().status_code,400)
        self.document['pages'][1]['slug']='assets'
        self.assertEqual(self.save().status_code,400)
        self.document['pages'][1]['slug']='about'
        self.document['pages'][1]['blocks'][0]['id']=self.document['pages'][0]['blocks'][0]['id']
        self.assertEqual(self.save().status_code,400)

    def test_private_asset_is_decoded_reencoded_and_deduplicated(self):
        first=self.upload();self.assertEqual(first.status_code,201,first.content)
        asset=LabBuilderAsset.objects.get()
        self.assertEqual((asset.width,asset.height),(120,90))
        response=self.client.get(first.json()['url'])
        self.assertEqual(response['Content-Type'],'image/webp')
        with Image.open(io.BytesIO(response.content)) as image:self.assertEqual(image.format,'WEBP')
        second=self.upload(name='same-image.png')
        self.assertEqual(second.status_code,200)
        self.assertEqual(second.json()['id'],first.json()['id'])
        self.assertEqual(Client().get(first.json()['url']).status_code,302)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(first.json()['url']).status_code,404)

    def test_svg_and_disguised_files_are_rejected(self):
        for name,content in [('attack.png',b'<script>alert(1)</script>'),('image.svg',b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')]:
            response=self.client.post(self.url('upload'),{'image':SimpleUploadedFile(name,content)})
            self.assertEqual(response.status_code,400)
        self.assertFalse(LabBuilderAsset.objects.exists())

    def test_foreign_assets_cannot_be_added_to_document(self):
        response=self.upload();item=LabBuilderAsset.objects.get();item.startup=self.foreign;item.save()
        self.document['pages'][0]['blocks'][1]['props']['image']=response.json()['id']
        self.assertEqual(self.save().status_code,400)

    def test_viewer_and_other_user_cannot_edit(self):
        ProjectMember.objects.create(startup=self.startup,user=self.other,role=ProjectMember.Role.VIEWER,status=ProjectMember.Status.ACTIVE,invited_by=self.owner)
        self.client.force_login(self.other)
        for endpoint,method in [('', 'get'),('draft','get'),('preview','post'),('snapshot','post'),('upload','post'),('download','get')]:
            self.assertEqual(getattr(self.client,method)(self.url(endpoint)).status_code,404)
        self.assertEqual(self.client.get(reverse('lab_builder',args=[self.foreign.pk])).status_code,200)

    def test_deep_json_is_rejected_before_document_validation(self):
        response=self.client.post(self.url('draft'),'{"document":'+ '['*80+'0'+']'*80+'}',content_type='application/json')
        self.assertEqual(response.status_code,400)
        self.assertIn('JSON',response.json()['error'])

    def test_csrf_is_required_for_changes(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.owner)
        response=client.post(self.url('draft'),json.dumps({'document':self.document,'revision':1}),content_type='application/json')
        self.assertEqual(response.status_code,403)

    def test_multipage_publication_referenced_images_only_and_drafts_stay_private(self):
        first=self.upload();unused=self.upload(color='blue');key=first.json()['id']
        self.document['pages'][0]['blocks'][1]['props']['image']=key
        self.add_page(self.document)
        self.document['pages'][0]['blocks'][0]['props']['items']=[{'title':'О проекте','link':'/about'}]
        version=self.version()
        response=self.client.post(reverse('lab_hosting',args=[self.startup.pk]),{'version':version.pk},HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code,200,response.content)
        site=HostedSite.objects.get();host=site.slug+'.localhost:8000'
        guest=Client()
        root=guest.get('/',HTTP_HOST=host)
        self.assertContains(root,'href="/about/"')
        self.assertContains(root,'Сделано в BrunoLab')
        about=guest.get('/about/',HTTP_HOST=host)
        self.assertContains(about,'У каждой идеи')
        self.assertEqual(guest.get('/about/index.html',HTTP_HOST=host).status_code,200)
        canonical=guest.get('/about',HTTP_HOST=host)
        self.assertEqual(canonical.status_code,301)
        self.assertEqual(canonical['Location'],'/about/')
        self.assertEqual(guest.get('/unknown/',HTTP_HOST=host).status_code,404)
        img=guest.get(f'/assets/uploads/{key}.webp',HTTP_HOST=host)
        self.assertEqual(img.status_code,200)
        self.assertEqual(img['Content-Type'],'image/webp')
        self.assertFalse(img.cookies)
        self.assertEqual(guest.get(f'/assets/uploads/{unused.json()["id"]}.webp',HTTP_HOST=host).status_code,404)
        self.document['pages'][0]['blocks'][1]['props']['title']='НЕ ОПУБЛИКОВАНО'
        self.save()
        self.assertNotContains(guest.get('/',HTTP_HOST=host),'НЕ ОПУБЛИКОВАНО')
        head=guest.head('/',HTTP_HOST=host)
        self.assertEqual(head.content,b'')
        self.assertEqual(head['Content-Length'],root['Content-Length'])

    def test_zip_has_pages_portable_links_images_and_source_document(self):
        uploaded=self.upload();key=uploaded.json()['id'];self.add_page(self.document)
        self.document['pages'][1]['blocks'].append(builder.new_block('image'))
        self.document['pages'][1]['blocks'][-1]['props']['image']=key
        self.save()
        response=self.client.get(self.url('download'))
        self.assertEqual(response.status_code,200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIn('about/index.html',archive.namelist())
            self.assertIn(f'assets/{key}.webp',archive.namelist())
            self.assertIn(f'../assets/{key}.webp',archive.read('about/index.html').decode())
            self.assertEqual(json.loads(archive.read('bruno-builder.json'))['pages'][1]['slug'],'about')
            self.assertNotIn('app.py',archive.namelist())

    def test_lab_preview_shows_all_pages_with_private_images_and_no_scripts(self):
        self.add_page(self.document)
        version=self.version()
        url=reverse('lab_preview',args=[self.startup.pk,version.pk])
        response=self.client.get(url+'?page=about')
        self.assertEqual(response.status_code,200)
        self.assertIn("script-src 'none'",response['Content-Security-Policy'])
        self.assertIn('allow-same-origin',response['Content-Security-Policy'])
        self.assertEqual(response['X-Frame-Options'],'SAMEORIGIN')
        self.assertEqual(self.client.get(url+'?page=missing').status_code,404)

    def test_existing_ai_site_is_not_overwritten_when_builder_opens(self):
        version=LabSiteVersion.objects.create(startup=self.startup,prompt='AI',html='<h1>Работающий AI-сайт</h1>',model='test',kind='generated')
        response=self.client.get(self.url('')+'?version='+str(version.pk))
        self.assertEqual(response.status_code,200)
        self.assertEqual(LabSiteVersion.objects.count(),1)
        self.assertIsNone(self.startup.builder_draft.saved_version_id)
        version.refresh_from_db();self.assertEqual(version.html,'<h1>Работающий AI-сайт</h1>')

    def test_ai_edits_only_requested_block_and_keeps_document_editable(self):
        from founder.services.builder_ai import generate
        from founder.services.qwen import CodeResult
        version=self.version(self.add_page(self.document))
        target=version.builder_document['pages'][0]['blocks'][1]
        output=json.dumps({'summary':'Изменён заголовок первого экрана.','operations':[{'op':'update_block','id':target['id'],'props':{'title':'Точная правка'}}]})
        with patch('founder.services.builder_ai.generate_code',return_value=CodeResult(output,'openai/gpt-6-luna',200,50)),patch('founder.services.builder_ai.reject_output'):
            result=generate('Поменяй заголовок на Точная правка',version)
        self.assertIn('Точная правка',result.text)
        self.assertEqual(result.builder_document['pages'][1],version.builder_document['pages'][1])
        self.assertEqual(result.builder_document['pages'][0]['blocks'][0],version.builder_document['pages'][0]['blocks'][0])
        self.assertEqual(result.edit_method,'visual-ai')

    def test_ai_corrects_invalid_operation_without_losing_other_pages(self):
        from founder.services.builder_ai import generate
        from founder.services.qwen import CodeResult
        version=self.version(self.add_page(self.document));target=version.builder_document['pages'][0]['blocks'][1]
        invalid=json.dumps({'summary':'Правка','operations':[{'op':'update_block','id':'missing','props':{'title':'Hello'}}]})
        valid=json.dumps({'summary':'Правка','operations':[{'op':'update_block','id':target['id'],'props':{'title':'Hello'}}]})
        with patch('founder.services.builder_ai.generate_code',side_effect=[CodeResult(invalid,'test',100,20),CodeResult(valid,'test',120,30)]) as model,patch('founder.services.builder_ai.reject_output'):
            result=generate('Измени заголовок',version)
        self.assertEqual(model.call_count,2)
        self.assertEqual(result.input_tokens,220)
        self.assertEqual(result.output_tokens,50)
        self.assertIn('Блок с указанным id не найден',model.call_args.args[1][1]['content'])
        self.assertEqual(result.builder_document['pages'][1],version.builder_document['pages'][1])

    def test_ai_integration_syncs_clean_draft_and_never_overwrites_new_manual_changes(self):
        from founder.services.qwen import CodeResult
        version=self.version()
        changed=copy.deepcopy(version.builder_document);changed['pages'][0]['blocks'][1]['props']['title']='AI заголовок'
        result=CodeResult(builder.render_document(changed),'test',10,10,builder_document=changed,edit_method='visual-ai',report={'summary':'Готово','completed':['Изменён заголовок'],'not_done':[]})
        with patch('founder.services.builder_ai.generate',return_value=result),patch('founder.lab_views.validate_result') as generic:
            response=self.client.post(reverse('lab_generate',args=[self.startup.pk]),{'source_version':version.pk,'prompt':'Измени заголовок','message_mode':'edit'})
        self.assertEqual(response.status_code,302,response.content)
        generic.assert_not_called()
        current=LabBuilderDraft.objects.get(startup=self.startup)
        self.assertEqual(current.document,changed)
        self.assertEqual(current.saved_version.builder_document,changed)
        ai_version=current.saved_version
        manual=copy.deepcopy(changed);manual['pages'][0]['blocks'][1]['props']['text']='Новая ручная правка'
        self.save(manual)
        with patch('founder.services.builder_ai.generate',return_value=result):
            response=self.client.post(reverse('lab_generate',args=[self.startup.pk]),{'source_version':ai_version.pk,'prompt':'Измени цвет блока','message_mode':'edit'})
        self.assertEqual(response.status_code,302)
        current.refresh_from_db();self.assertEqual(current.document,manual)

    def test_community_preview_embeds_private_images_once_and_all_pages(self):
        from founder.services.builder_preview import render
        uploaded=self.upload();self.add_page(self.document)
        self.document['pages'][0]['blocks'][1]['props']['image']=uploaded.json()['id']
        self.document['pages'][0]['blocks'][0]['props']['items']=[{'title':'О проекте','link':'/about'}]
        version=self.version()
        html=render(version)
        self.assertIn('bruno-builder-pages',html)
        self.assertIn('data:image/webp;base64,',html)
        self.assertIn('#bruno-page=about',html)
        self.assertNotIn(uploaded.json()['url'],html)
        self.assertNotIn('private_uploads',html)
        # Template text must never close the JSON script element.
        changed=copy.deepcopy(self.document);changed['pages'][0]['blocks'][1]['props']['title']='</script><script>alert(1)</script>'
        safe=render(self.version(changed))
        self.assertNotIn('</script><script>alert(1)',safe)
