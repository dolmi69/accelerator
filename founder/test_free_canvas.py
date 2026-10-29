import copy
import io
import json
import zipfile
from django.test import TestCase, override_settings
from django.urls import reverse
from founder.models import User,StartupProfile,LabSiteVersion
from founder.services import visual_builder as builder,free_canvas as free
from founder.services.builder_ai import apply_operations


class FreeCanvasSchemaTests(TestCase):
    def setUp(self):
        self.startup=StartupProfile(name='Свобода')
        self.document=builder.default_document(self.startup)
        self.canvas=builder.new_block('canvas');self.document['pages'][0]['blocks']=[self.canvas]
        self.text=free.new_element('text');self.canvas['props']['elements']=[self.text]

    def test_legacy_and_free_layout_both_roundtrip_without_schema_migration(self):
        legacy=builder.default_document(self.startup)
        self.assertEqual(builder.validate_document(legacy),legacy)
        self.assertEqual(builder.validate_document(self.document),self.document)

    def test_render_keeps_independent_mobile_geometry_and_escapes_text(self):
        self.text['content']['text']='<script>unsafe</script> & текст'
        self.text['desktop']['x']=120;self.text['mobile']['x']=24
        html=builder.render_document(builder.validate_document(self.document),editor=True)
        self.assertIn('data-free-id="'+self.text['id']+'"',html)
        self.assertIn('&lt;script&gt;',html);self.assertNotIn('<script>',html)
        self.assertIn('--d-x:10.714286%',html);self.assertIn('--m-x:6.153846%',html)
        self.assertIn('@media(max-width:650px)',html)
        public=builder.render_document(self.document)
        self.assertNotIn('data-free-',public)

    def test_bounds_css_injection_links_ids_and_unknown_fields_are_rejected(self):
        for change in [lambda e:e['desktop'].update(x=1110),lambda e:e['mobile'].update(width=391),lambda e:e['style'].update(background='url(https://evil.test)'),lambda e:e['content'].update(link='javascript:alert(1)'),lambda e:e.update(locked='true'),lambda e:e['desktop'].update(x=True),lambda e:e['style'].update(position='fixed')]:
            document=copy.deepcopy(self.document);change(document['pages'][0]['blocks'][0]['props']['elements'][0])
            with self.subTest(change=change),self.assertRaises(builder.DocumentError):builder.validate_document(document)
        self.canvas['props']['elements'].append(copy.deepcopy(self.text))
        with self.assertRaises(builder.DocumentError):builder.validate_document(self.document)

    def test_all_element_types_and_layer_order_render(self):
        self.canvas['props']['elements']=[free.new_element(kind)for kind in sorted(free.TYPES)]
        for e in self.canvas['props']['elements']:
            if e['type']=='image':e['content']['image']='owned'
        doc=builder.validate_document(self.document,['owned'])
        html=builder.render_document(doc,image_url=lambda key:'/test/image.webp')
        self.assertIn('/test/image.webp',html);self.assertEqual(builder.asset_ids(doc),{'owned'})
        self.assertIn('z-index:5',html)
        with self.assertRaises(builder.DocumentError):builder.validate_document(self.document,['foreign'])

    def test_ai_can_edit_free_content_and_position_without_changing_other_device(self):
        initial=copy.deepcopy(self.document)
        result=apply_operations(self.document,[{'op':'update_element','id':self.text['id'],'content':{'text':'Бруно'},'desktop':{'x':140}}],[])
        actual=result['pages'][0]['blocks'][0]['props']['elements'][0]
        self.assertEqual(actual['desktop']['x'],140);self.assertEqual(actual['mobile'],self.text['mobile'])
        self.assertEqual(self.document,initial)
        result=apply_operations(result,[{'op':'add_element','block':self.canvas['id'],'type':'button','content':{'text':'Открыть','link':'/about/'}}],[])
        elements=result['pages'][0]['blocks'][0]['props']['elements'];self.assertEqual(len(elements),2)
        deleted=apply_operations(result,[{'op':'delete_element','id':elements[1]['id']}],[])
        self.assertEqual(len(deleted['pages'][0]['blocks'][0]['props']['elements']),1)

    def test_element_limit_and_missing_asset(self):
        self.canvas['props']['elements']=[free.new_element('shape')for _ in range(61)]
        with self.assertRaises(builder.DocumentError):builder.validate_document(self.document)

    def test_converted_typography_links_and_short_navigation_roundtrip(self):
        self.canvas['props']['height']=98
        self.canvas['props']['elements']=[]
        link=free.new_element('button')
        link['content'].update(text='Главная',link='/')
        link['desktop'].update(x=450,y=35,width=56,height=24,fontSize=13)
        link['mobile'].update(x=20,y=84,width=56,height=24,fontSize=13)
        link['style'].update(background='transparent',color='#182b2a',fontFamily='inherit',fontWeight='400',
            lineHeight=1650,letterSpacing=-40,paddingX=0,paddingY=0,mobilePaddingX=0,mobilePaddingY=0,plainLink=True,trailingIcon=False)
        self.canvas['props']['elements']=[link]
        document=builder.validate_document(self.document)
        self.assertEqual(document,self.document)
        html=builder.render_document(document)
        self.assertIn('line-height:1.65',html)
        self.assertIn('letter-spacing:-0.04em',html)
        self.assertIn('--d-px:0.000000cqw',html)
        self.assertIn('bb-free-link',html)
        changed=apply_operations(document,[{'op':'update_element','id':link['id'],'content':{'text':'Начало'}}],[])
        self.assertEqual(changed['pages'][0]['blocks'][0]['props']['elements'][0]['style'],link['style'])
        link['style']['trailingIcon']=True
        self.assertIn('bb-free-icon',builder.render_document(builder.validate_document(self.document)))
        for key,value in [('lineHeight','url(evil)'),('letterSpacing',True),('paddingX',999),('plainLink','true')]:
            bad=copy.deepcopy(self.document);bad['pages'][0]['blocks'][0]['props']['elements'][0]['style'][key]=value
            with self.subTest(key=key),self.assertRaises(builder.DocumentError):builder.validate_document(bad)

    def test_template_metadata_and_faq_survive_save_ai_and_public_render(self):
        self.canvas['props']['template']='faq'
        faq=free.new_element('faq');faq['content'].update(text='Вопрос <img onerror=alert(1)>',alt='Ответ & подробности\nЕщё строка')
        self.canvas['props']['elements']=[faq]
        clean=builder.validate_document(self.document)
        self.assertEqual(clean,self.document)
        html=builder.render_document(clean)
        self.assertIn('<details class="bb-faq bb-free-faq" open>',html)
        self.assertIn('&lt;img onerror=alert(1)&gt;',html)
        self.assertIn('Ответ &amp; подробности<br>Ещё строка',html)
        self.assertNotIn('data-free-id',html)
        edited=apply_operations(clean,[{'op':'update_element','id':faq['id'],'content':{'text':'Другой вопрос'}}],[])
        props=edited['pages'][0]['blocks'][0]['props']
        self.assertEqual(props['template'],'faq');self.assertEqual(props['elements'][0]['content']['alt'],faq['content']['alt'])
        for value in ['unknown',[],{},True]:
            bad=copy.deepcopy(clean);bad['pages'][0]['blocks'][0]['props']['template']=value
            with self.subTest(value=value),self.assertRaises(builder.DocumentError):builder.validate_document(bad)
        bad=copy.deepcopy(clean);bad['pages'][0]['blocks'][0]['props']['elements'][0]['content']['alt']='x'*5001
        with self.assertRaises(builder.DocumentError):builder.validate_document(bad)


@override_settings(REDIS_URL='',DEBUG=True,BRUNOHOST_BASE_URL='http://localhost:8000',BRUNOHOST_ENGINE='disabled')
class FreeCanvasLifecycleTests(TestCase):
    def setUp(self):
        user=User.objects.create_user(username='free-owner')
        self.startup=StartupProfile.objects.create(owner=user,name='Свободный сайт');self.client.force_login(user)
        self.document=builder.default_document(self.startup);self.canvas=builder.new_block('canvas')
        self.text=free.new_element('text');self.text['content']['text']='Свободное размещение'
        self.canvas['props']['elements']=[self.text];self.document['pages'][0]['blocks']=[self.canvas]

    def url(self,name):return reverse('lab_builder_'+name,args=[self.startup.pk])
    def post(self,name,data):return self.client.post(self.url(name),json.dumps(data),content_type='application/json')
    def test_autosave_snapshot_download_and_host_use_same_free_layout(self):
        response=self.post('draft',{'document':self.document,'revision':1});self.assertEqual(response.status_code,200,response.content)
        preview=self.post('preview',{'document':self.document,'page':self.document['pages'][0]['id']})
        self.assertContains(preview,'data-free-id')
        saved=self.post('snapshot',{'revision':2});self.assertEqual(saved.status_code,200,saved.content)
        version=LabSiteVersion.objects.get(pk=saved.json()['version']);self.assertEqual(version.builder_document,self.document)
        with zipfile.ZipFile(io.BytesIO(self.client.get(self.url('download')).content))as archive:
            html=archive.read('index.html').decode();self.assertIn('Свободное размещение',html);self.assertNotIn('data-free-id',html)
        result=self.client.post(reverse('lab_hosting',args=[self.startup.pk]),{'version':str(version.pk)},HTTP_ACCEPT='application/json')
        self.assertEqual(result.status_code,200,result.content)
        public=self.client.get('/',HTTP_HOST=self.startup.hosted_site.slug+'.localhost:8000')
        self.assertContains(public,'Свободное размещение');self.assertContains(public,'bb-free-canvas');self.assertNotContains(public,'data-free-id')
    def test_invalid_layout_preserves_saved_draft(self):
        self.post('draft',{'document':self.document,'revision':1})
        bad=copy.deepcopy(self.document);bad['pages'][0]['blocks'][0]['props']['elements'][0]['mobile']['y']=9000
        response=self.post('draft',{'document':bad,'revision':2});self.assertEqual(response.status_code,400)
        self.assertEqual(self.startup.builder_draft.document,self.document)
