"""Exercise real persistence, isolation, stock, schedule and payment invariants."""
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import tempfile
import uuid
from unittest.mock import patch
from PIL import Image
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Asset, Booking, Favorite, Invitation, Item, Lead, Membership, Notification, Order, Page, Payment, Profile, Review, SiteControl, Slot
from .services import checkout, reserve_slot, transition_order
from .payments import apply_provider


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ModuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner,cls.worker,cls.buyer,cls.eve = [get_user_model().objects.create_user(username=name,
            email=name+'@test.example',password='Forest-73-Lake!') for name in ('owner','worker','buyer','eve')]
        for user,role in [(cls.owner,'owner'),(cls.worker,'editor'),(cls.buyer,'client'),(cls.eve,'client')]:
            Membership.objects.create(user=user,role=role)
            Profile.objects.create(user=user,email_key=user.email)
        SiteControl.objects.create(pk=1,setup_claimed=True)
        cls.item = Item.objects.create(owner=cls.owner,title='Урок Python',description='Занятие для начинающих',category='Обучение',price=Decimal('500'),stock=3)

    def setUp(self):
        self.client.force_login(self.buyer)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.temp.name)
        self.settings_override.enable(); self.addCleanup(self.settings_override.disable)

    def post(self,name,*args,data=None):
        return self.client.post(reverse(name,args=args),data or {})

    def slot(self):
        return Slot.objects.create(item=self.item,starts_at=timezone.now()+timedelta(days=1),ends_at=timezone.now()+timedelta(days=1,hours=1))

    def test_disabled_modules_close_direct_routes(self):
        with override_settings(SITE={**settings.SITE,'modules':[]}):
            for path in ('/catalog/','/request/','/cart/','/pages/','/uploads/1/','/bookings/','/register/','/password-reset/','/password-reset/sent/','/people/','/messages/','/notifications/'):
                self.assertEqual(self.client.get(path).status_code,404,path)
            self.assertEqual(self.client.get('/profile/').status_code,200)
            self.assertEqual(self.client.get('/login/').status_code,200)
            self.assertNotContains(self.client.get('/login/'), 'Забыли пароль?')
            from .services import notify
            before = Notification.objects.count()
            self.assertIsNone(notify(self.buyer.pk, 'Disabled notification'))
            self.assertEqual(Notification.objects.count(), before)

    def test_profile_edits_username_and_email_without_escalating_role(self):
        response=self.post('profile',data={'username':'new_buyer','email':'new@test.example','bio':'<script>x</script>','role':'owner'})
        self.assertEqual(response.status_code,302)
        self.buyer.refresh_from_db()
        self.assertEqual(self.buyer.username,'new_buyer')
        self.assertEqual(self.buyer.site_membership.role,'client')
        self.assertContains(self.client.get(reverse('public_profile',args=[self.buyer.pk])),'&lt;script&gt;')
        self.post('profile',data={'username':'owner','email':'owner@test.example'})
        self.buyer.refresh_from_db(); self.assertEqual(self.buyer.username,'new_buyer')

    def test_password_reset_mail_and_single_use_token(self):
        self.client.logout()
        response=self.client.post('/password-reset/',{'email':self.buyer.email})
        self.assertEqual(response.status_code,302); self.assertEqual(len(mail.outbox),1)
        link=next(line.strip() for line in mail.outbox[0].body.splitlines() if line.startswith('http'))
        from urllib.parse import urlsplit
        url=urlsplit(link).path
        response=self.client.get(url)
        self.assertEqual(response.status_code,302)
        response=self.client.post(response.url,{'new_password1':'Changed-Tree-74!','new_password2':'Changed-Tree-74!'})
        self.assertEqual(response.status_code,302)
        self.buyer.refresh_from_db(); self.assertTrue(self.buyer.check_password('Changed-Tree-74!'))
        self.assertContains(self.client.get(url),'недействительна')
        self.client.post('/password-reset/',{'email':'unknown@test.example'})
        self.assertEqual(len(mail.outbox),1)

    def test_catalog_filters_pagination_and_drafts(self):
        Item.objects.create(owner=self.owner,title='Hidden secret',description='Draft',published=False,price=1)
        for n in range(21):
            Item.objects.create(owner=self.owner,title=f'Товар {n}',description='text',category='Книги',price=n)
        self.assertContains(self.client.get('/catalog/?q=Python&category=Обучение&min=400&max=600'),'Урок Python')
        self.assertNotContains(self.client.get('/catalog/'),'Hidden secret')
        self.assertEqual(len(self.client.get('/catalog/').context['page']),20)
        self.assertEqual(self.client.get('/catalog/?page=2').status_code,200)
        self.assertEqual(self.client.get('/catalog/?min=NaN&max=Infinity').status_code,200)

    def test_clients_cannot_edit_others_or_create_until_allowed(self):
        self.assertEqual(self.post('item_edit',self.item.pk,data={'title':'Hacked'}).status_code,404)
        self.assertEqual(self.post('item_create',data={}).status_code,403)
        SiteControl.objects.filter(pk=1).update(allow_listings=True)
        response=self.post('item_create',data={'title':'Моя карточка','description':'Текст','published':'on','owner':self.owner.pk})
        self.assertEqual(response.status_code,302)
        self.assertEqual(Item.objects.get(title='Моя карточка').owner,self.buyer)

    def test_worker_can_edit_but_cannot_manage_team(self):
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get('/team/').status_code,403)
        response=self.post('item_edit',self.item.pk,data={'title':'Изменено','description':'Текст','published':'on'})
        self.assertEqual(response.status_code,302)
        self.item.refresh_from_db(); self.assertEqual(self.item.title,'Изменено')

    def test_invite_is_bound_to_account_single_use_and_no_owner_escalation(self):
        self.client.force_login(self.owner)
        self.post('team',data={'username':'buyer','email':'buyer@test.example','role':'editor'})
        invitation=Invitation.objects.get()
        self.client.force_login(self.eve)
        self.assertEqual(self.post('accept_invite',invitation.token).status_code,403)
        self.client.force_login(self.buyer)
        self.assertEqual(self.post('accept_invite',invitation.token).status_code,302)
        self.assertEqual(Membership.objects.get(user=self.buyer).role,'editor')
        self.assertEqual(self.post('accept_invite',invitation.token).status_code,404)
        self.client.force_login(self.owner)
        self.post('team',data={'username':'eve','role':'owner'})
        self.assertEqual(Invitation.objects.count(),1)
        self.assertEqual(self.post('team',data={'action':'role','member':'not-an-id','role':'editor'}).status_code,400)

    def test_owner_setup_requires_secret_and_is_single_use(self):
        with override_settings(BASE_DIR=Path(self.temp.name)):
            (Path(self.temp.name)/'.owner-setup').write_text('test-owner-token')
            SiteControl.objects.filter(pk=1).update(setup_claimed=False)
            self.client.logout()
            self.assertEqual(self.client.get('/setup/wrong/').status_code,404)
            response=self.client.post('/setup/test-owner-token/',{'username':'real_owner','password1':'River-Forest-73!','password2':'River-Forest-73!'})
            self.assertEqual(response.status_code,302)
            self.assertTrue(get_user_model().objects.get(username='real_owner').is_superuser)
            self.assertEqual(self.client.get('/setup/test-owner-token/').status_code,404)

    def test_favorites_are_idempotent_and_private(self):
        for _ in range(2):self.post('favorite',self.item.pk,data={'save':'1'})
        self.assertEqual(Favorite.objects.count(),1)
        self.client.force_login(self.eve)
        self.assertNotContains(self.client.get('/favorites/'),'Урок Python')
        self.client.force_login(self.buyer)
        self.post('favorite',self.item.pk,data={'save':'0'})
        self.assertEqual(Favorite.objects.count(),0)

    def test_leads_persist_but_other_clients_cannot_see_or_change_them(self):
        self.post('item_lead',self.item.pk,data={'name':'Покупатель','email':'b@test.example','text':'private-contact'})
        lead=Lead.objects.get(); self.assertEqual(lead.user,self.buyer)
        self.client.force_login(self.eve)
        self.assertNotContains(self.client.get('/manage/'),'private-contact')
        self.assertEqual(self.post('lead_status',lead.pk,data={'status':'closed'}).status_code,404)
        self.client.force_login(self.worker)
        self.assertContains(self.client.get('/manage/'),'private-contact')
        self.post('lead_status',lead.pk,data={'status':'contacted'})
        lead.refresh_from_db(); self.assertEqual(lead.status,'contacted')

    def test_anonymous_lead_is_supported(self):
        self.client.logout()
        self.post('lead_create',data={'name':'Гость','email':'guest@test.example','text':'Позвоните мне'})
        self.assertIsNone(Lead.objects.get().user_id)
        self.assertEqual(self.client.get('/manage/').status_code,302)

    def test_uploads_reject_svg_and_spoofed_images(self):
        for filename,data in [('bad.svg',b'<svg/>'),('bad.jpg',b'<script/>')]:
            self.post('upload',data={'file':SimpleUploadedFile(filename,data)})
        self.assertFalse(Asset.objects.exists())

    def test_pdf_is_private_attachment_and_image_gallery_is_public(self):
        self.post('upload',data={'file':SimpleUploadedFile('private.pdf',b'%PDF-1.4 test')})
        asset=Asset.objects.get()
        response=self.client.get(reverse('asset',args=[asset.pk]))
        self.assertIn('attachment',response['Content-Disposition'])
        self.client.force_login(self.eve)
        self.assertEqual(self.client.get(reverse('asset',args=[asset.pk])).status_code,404)
        self.client.force_login(self.owner)
        image=BytesIO(); Image.new('RGB',(20,20)).save(image,'PNG')
        self.post('item_upload',self.item.pk,data={'file':SimpleUploadedFile('photo.png',image.getvalue())})
        picture=Asset.objects.get(image=True)
        self.client.logout()
        response=self.client.get(reverse('asset',args=[picture.pk]))
        self.assertEqual(response['Content-Type'],'image/jpeg')
        Item.objects.filter(pk=self.item.pk).update(published=False)
        self.assertEqual(self.client.get(reverse('asset',args=[picture.pk])).status_code,404)

    def test_booking_double_reservation_and_cancel_then_rebook(self):
        slot=self.slot()
        first=reserve_slot(self.buyer,slot.pk)
        with self.assertRaises(ValidationError): reserve_slot(self.eve,slot.pk)
        self.assertEqual(self.post('booking_cancel',first.pk).status_code,302)
        second=reserve_slot(self.eve,slot.pk)
        self.assertNotEqual(first.pk,second.pk)
        self.client.force_login(self.owner)
        self.assertEqual(self.post('slot_delete',slot.pk).status_code,400)

    def test_booking_privacy_and_overlap_checks(self):
        slot=self.slot(); booking=reserve_slot(self.buyer,slot.pk)
        self.client.force_login(self.eve)
        self.assertEqual(self.post('booking_cancel',booking.pk).status_code,404)
        self.assertEqual(len(self.client.get('/bookings/').context['page']),0)
        self.client.force_login(self.owner)
        response=self.post('slot_create',self.item.pk,data={'starts_at':slot.starts_at.isoformat(),'ends_at':slot.ends_at.isoformat()})
        self.assertContains(response,'пересекается')
        self.assertEqual(Slot.objects.count(),1)

    def test_checkout_uses_server_prices_and_retries_once(self):
        nonce=uuid.uuid4()
        order=checkout(self.buyer,{str(self.item.pk):2},nonce,'Контакт')
        again=checkout(self.buyer,{},nonce,'Повтор')
        self.assertEqual(order.pk,again.pk)
        self.assertEqual(order.total,Decimal('1000'))
        self.item.refresh_from_db(); self.assertEqual(self.item.stock,1)
        self.assertEqual(order.lines.count(),1)

    def test_stock_shortage_rolls_back_entire_order(self):
        with self.assertRaises(ValidationError): checkout(self.buyer,{str(self.item.pk):4},uuid.uuid4(),'Контакт')
        self.assertFalse(Order.objects.exists())
        self.item.refresh_from_db(); self.assertEqual(self.item.stock,3)

    def test_cancel_restock_once_and_status_access(self):
        order=checkout(self.buyer,{str(self.item.pk):2},uuid.uuid4(),'Контакт')
        self.assertEqual(self.post('order',order.pk,data={'status':'done'}).status_code,403)
        self.post('order',order.pk,data={'status':'canceled'})
        self.post('order',order.pk,data={'status':'canceled'})
        self.item.refresh_from_db(); self.assertEqual(self.item.stock,3)
        self.client.force_login(self.eve)
        self.assertEqual(self.client.get(reverse('order',args=[order.pk])).status_code,404)

    def test_cart_checkout_http_and_invalid_quantity(self):
        self.assertEqual(self.post('cart_update',self.item.pk,data={'quantity':'-5'}).status_code,400)
        self.post('cart_update',self.item.pk,data={'quantity':'2'})
        self.assertContains(self.client.get('/cart/'),'1000')
        self.post('checkout',data={'contact':'Позвоните','nonce':str(uuid.uuid4()),'total':'0.01'})
        self.assertEqual(Order.objects.get().total,Decimal('1000'))
        self.assertEqual(self.client.session['cart'],{})

    def test_reviews_are_moderated_and_edit_requires_reapproval(self):
        self.post('review',self.item.pk,data={'rating':'5','text':'Отлично <script>x</script>'})
        review=Review.objects.get()
        self.assertNotContains(self.client.get(reverse('item',args=[self.item.pk])),'Отлично')
        self.client.force_login(self.owner)
        self.post('review_moderate',review.pk,data={'action':'approve'})
        self.assertContains(self.client.get(reverse('item',args=[self.item.pk])),'&lt;script&gt;')
        self.client.force_login(self.buyer)
        self.post('review',self.item.pk,data={'rating':'4','text':'Исправленный отзыв'})
        review.refresh_from_db(); self.assertFalse(review.approved)
        self.post('review',self.item.pk,data={'rating':'99','text':'Некорректно'})
        review.refresh_from_db(); self.assertEqual(review.rating,4)

    def test_pages_drafts_escape_and_permissions(self):
        self.assertEqual(self.post('page_create',data={}).status_code,403)
        self.client.force_login(self.worker)
        self.post('page_create',data={'title':'О сервисе','slug':'about','text':'<script>x</script>'})
        page=Page.objects.get()
        self.client.force_login(self.eve)
        self.assertEqual(self.client.get('/pages/about/').status_code,404)
        Page.objects.filter(pk=page.pk).update(published=True)
        self.assertContains(self.client.get('/pages/about/'),'&lt;script&gt;')

    def test_notifications_cannot_be_read_by_another_user(self):
        row=Notification.objects.create(user=self.owner,text='private-notice')
        self.assertEqual(self.post('read_notification',row.pk).status_code,404)
        self.assertNotContains(self.client.get('/notifications/'),'private-notice')

    def test_payments_disabled_without_configuration(self):
        order=checkout(self.buyer,{str(self.item.pk):1},uuid.uuid4(),'Контакт')
        with patch('core.payments.provider') as provider:
            self.post('pay',order.pk)
            provider.assert_not_called()
        self.assertFalse(Payment.objects.exists())

    def test_payment_confirmation_rejects_wrong_amount_and_deduplicates(self):
        order=checkout(self.buyer,{str(self.item.pk):1},uuid.uuid4(),'Контакт')
        payment=Payment.objects.create(order=order,amount=order.total,provider_id='test-id')
        data={'id':'test-id','amount':{'value':'0.01','currency':'RUB'},'status':'succeeded','paid':True,'metadata':{'order_id':str(order.pk)}}
        with self.assertRaises(ValidationError):apply_provider(payment,data)
        data['amount']['value']=str(order.total)
        apply_provider(payment,data); apply_provider(payment,data)
        payment.refresh_from_db(); self.assertEqual(payment.status,'succeeded')
        self.assertEqual(Notification.objects.filter(dedupe_key__startswith='paid-').count(),2)
        with self.assertRaises(ValidationError):transition_order(order,'canceled')

    @override_settings(YOOKASSA_SHOP_ID='test',YOOKASSA_SECRET_KEY='fixture',PAYMENT_RETURN_BASE='https://example.test')
    def test_forged_webhook_uses_provider_lookup_not_payload_status(self):
        order=checkout(self.buyer,{str(self.item.pk):1},uuid.uuid4(),'Контакт')
        payment=Payment.objects.create(order=order,amount=order.total,provider_id='test-id')
        data={'id':'test-id','amount':{'value':str(order.total),'currency':'RUB'},'status':'pending','metadata':{'order_id':str(order.pk)}}
        with patch('core.payments.provider',return_value=data) as provider:
            response=self.client.post('/payments/webhook/',{'object':{'id':'test-id','status':'succeeded'}},content_type='application/json')
            self.assertEqual(response.status_code,200)
            provider.assert_called_once_with('GET','payments/test-id')
        payment.refresh_from_db(); self.assertEqual(payment.status,'pending')

    def test_mutation_requires_post_and_csrf(self):
        self.assertEqual(self.client.get(reverse('favorite',args=[self.item.pk])).status_code,405)
        client=Client(enforce_csrf_checks=True); client.force_login(self.buyer)
        self.assertEqual(client.post(reverse('favorite',args=[self.item.pk]),{'save':'1'}).status_code,403)

    @override_settings(YOOKASSA_SHOP_ID='test',YOOKASSA_SECRET_KEY='fixture',PAYMENT_RETURN_BASE='https://example.test')
    def test_payment_creation_and_repeat_reuse_same_provider_payment(self):
        order=checkout(self.buyer,{str(self.item.pk):1},uuid.uuid4(),'Контакт')
        url='https://yoomoney.ru/checkout/test'
        data={'id':'provider-test','amount':{'value':str(order.total),'currency':'RUB'},'status':'pending',
            'metadata':{'order_id':str(order.pk)},'confirmation':{'confirmation_url':url}}
        with patch('core.payments.provider',return_value=data) as provider:
            self.assertEqual(self.post('pay',order.pk).url,url)
            self.assertEqual(self.post('pay',order.pk).url,url)
        self.assertEqual(Payment.objects.count(),1)
        self.assertEqual(provider.call_args_list[0].args,('POST','payments'))
        self.assertEqual(provider.call_args_list[1].args,('GET','payments/provider-test'))

    @override_settings(YOOKASSA_SHOP_ID='test',YOOKASSA_SECRET_KEY='fixture',PAYMENT_RETURN_BASE='https://example.test')
    def test_canceled_payment_without_redirect_does_not_lock_order(self):
        order=checkout(self.buyer,{str(self.item.pk):1},uuid.uuid4(),'Контакт')
        data={'id':'canceled-test','amount':{'value':str(order.total),'currency':'RUB'},'status':'canceled','metadata':{'order_id':str(order.pk)}}
        with patch('core.payments.provider',return_value=data):
            self.post('pay',order.pk)
        self.assertEqual(Payment.objects.get().status,'canceled')
        transition_order(order,'canceled')
        self.item.refresh_from_db(); self.assertEqual(self.item.stock,3)
