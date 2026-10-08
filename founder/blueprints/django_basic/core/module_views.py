from datetime import timedelta
import uuid
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .account_views import form_page
from .capabilities import can_create_item, editor, manages, module_required
from .forms import CheckoutForm, ItemForm, LeadForm, PageForm, ReviewForm, SlotForm, UploadForm
from .models import Asset, Booking, Favorite, Item, Lead, Order, Page, Review, SiteControl, Slot
from .services import cancel_booking, cart_rows, checkout, notify, notify_team, reserve_slot, transition_order


def paginate(queryset, request):
    return Paginator(queryset, 20).get_page(request.GET.get('page'))


def editable_item(request, item_id):
    item = get_object_or_404(Item, pk=item_id)
    if not manages(request.user, item):
        raise Http404()
    return item


@module_required('catalog')
@require_GET
def catalog(request):
    rows = Item.objects.filter(published=True)
    q, category = request.GET.get('q','').strip()[:100], request.GET.get('category','')[:60]
    if q:
        rows = rows.filter(Q(title__icontains=q)|Q(description__icontains=q))
    if category:
        rows = rows.filter(category=category)
    for key, lookup in [('min','price__gte'),('max','price__lte')]:
        try:
            from decimal import Decimal, InvalidOperation
            price = Decimal(request.GET.get(key,''))
            if price.is_finite() and 0 <= price <= 99_999_999:
                rows = rows.filter(**{lookup:price})
        except (InvalidOperation, ValueError):
            pass
    order = {'price':'price','-price':'-price','title':'title'}.get(request.GET.get('sort'), '-created_at')
    rows = rows.order_by(order,'pk')
    return render(request,'catalog.html',{'page':paginate(rows,request),'query':q,'category':category,
        'categories':Item.objects.filter(published=True).exclude(category='').values_list('category',flat=True).order_by('category').distinct()[:100],
        'can_create':can_create_item(request.user)})


@module_required('catalog')
@require_GET
def item_detail(request, item_id):
    item = get_object_or_404(Item.objects.select_related('owner'), pk=item_id)
    if not item.published and not manages(request.user,item):
        raise Http404()
    return render(request,'item.html',{'item':item,'can_edit':manages(request.user,item),
        'favorite':request.user.is_authenticated and Favorite.objects.filter(user=request.user,item=item).exists(),
        'slots':item.slots.filter(reserved=False,starts_at__gt=timezone.now())[:50],
        'reviews':item.reviews.filter(approved=True).select_related('user')[:100]})


@login_required
@module_required('catalog')
@require_http_methods(['GET','POST'])
def item_edit(request, item_id=None):
    item = editable_item(request,item_id) if item_id else None
    if not item and not can_create_item(request.user):
        return HttpResponse('Создавать карточки может владелец или сотрудник.',status=403)
    form = ItemForm(request.POST or None,instance=item)
    if request.method == 'POST' and form.is_valid():
        row = form.save(commit=False)
        if not item:
            row.owner = request.user
        row.save()
        return redirect('item',item_id=row.pk)
    return form_page(request,form,'Редактировать карточку' if item else 'Добавить карточку')


@login_required
@module_required('catalog')
@require_POST
def item_delete(request,item_id):
    editable_item(request,item_id).delete()
    return redirect('catalog')


@login_required
@module_required('favorites')
@require_POST
def favorite(request,item_id):
    item = get_object_or_404(Item,pk=item_id,published=True)
    # Explicit desired state, so retransmitted POSTs don't toggle unexpectedly.
    if request.POST.get('save') == '1':
        Favorite.objects.get_or_create(user=request.user,item=item)
    else:
        Favorite.objects.filter(user=request.user,item=item).delete()
    return redirect('item',item_id=item.pk)


@login_required
@module_required('favorites')
@require_GET
def favorites(request):
    return render(request,'catalog.html',{'page':paginate(Item.objects.filter(published=True,favorite__user=request.user),request), 'heading':'Избранное'})


@module_required('leads')
@require_http_methods(['GET','POST'])
def lead_create(request,item_id=None):
    item = get_object_or_404(Item,pk=item_id,published=True) if item_id else None
    initial = {'name':request.user.get_full_name() or request.user.username,'email':request.user.email} if request.user.is_authenticated else {}
    form = LeadForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        row = form.save(commit=False)
        row.item = item
        row.user = request.user if request.user.is_authenticated else None
        row.save()
        notify_team('Новая заявка',reverse('manage_site'))
        if item and not editor(item.owner):
            notify(item.owner_id,'Новая заявка по вашей карточке',reverse('manage_site'))
        messages.success(request,'Заявка сохранена. Автор сайта увидит её в кабинете.')
        return redirect('item',item_id=item.pk) if item else redirect('home')
    return form_page(request,form,'Оставить заявку',note=item.title if item else '')


@login_required
@module_required('leads')
@require_POST
def lead_status(request,lead_id):
    row = get_object_or_404(Lead,pk=lead_id)
    if not editor(request.user) and not (row.item and row.item.owner_id == request.user.pk):
        raise Http404()
    status = request.POST.get('status')
    if status not in Lead.Status.values:
        return HttpResponse('Неизвестный статус.',status=400)
    row.status = status; row.save(update_fields=['status'])
    if row.user_id:
        notify(row.user_id,f'Ваша заявка: {row.get_status_display()}',reverse('manage_site'))
    return redirect('manage_site')


@login_required
@module_required('uploads')
@require_http_methods(['GET','POST'])
def upload(request,item_id=None):
    item = editable_item(request,item_id) if item_id else None
    form = UploadForm(request.POST or None,request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        file,image = form.cleaned_data['file']
        Asset.objects.create(owner=request.user,item=item,file=file,image=image,
            name=request.FILES['file'].name[:150])
        return redirect('item',item_id=item.pk) if item else redirect('manage_site')
    return form_page(request,form,'Добавить файл',note='Фотографии опубликованных карточек видны всем. PDF и TXT доступны только автору и сотрудникам.')


@module_required('uploads')
@require_GET
def asset(request,asset_id):
    row = get_object_or_404(Asset.objects.select_related('item'),pk=asset_id)
    public = row.image and row.item_id and row.item.published
    allowed = editor(request.user) or (request.user.is_authenticated and (row.owner_id == request.user.pk or (row.item and row.item.owner_id == request.user.pk)))
    if not public and not allowed:
        raise Http404()
    response = FileResponse(row.file.open('rb'),as_attachment=not row.image,filename=row.name if not row.image else 'photo.jpg',
        content_type='image/jpeg' if row.image else 'application/octet-stream')
    response['Cache-Control'] = 'private, no-store' if not public else 'no-cache'
    return response


@login_required
@module_required('uploads')
@require_POST
def asset_delete(request,asset_id):
    row = get_object_or_404(Asset,pk=asset_id)
    if not editor(request.user) and row.owner_id != request.user.pk:
        raise Http404()
    storage,name = row.file.storage,row.file.name
    row.delete(); storage.delete(name)
    return redirect('manage_site')


@login_required
@module_required('booking')
@require_http_methods(['GET','POST'])
def slot_create(request,item_id):
    item = editable_item(request,item_id)
    form = SlotForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        # Serialize schedule edits; overlapping times across this provider's services are rejected.
        with transaction.atomic():
            SiteControl.objects.get_or_create(pk=1)
            SiteControl.objects.filter(pk=1).update(allow_listings=F('allow_listings'))
            start,end = form.cleaned_data['starts_at'],form.cleaned_data['ends_at']
            if Slot.objects.filter(item__owner=item.owner,starts_at__lt=end,ends_at__gt=start).exists():
                form.add_error(None,'Это время пересекается с другим интервалом этого автора.')
            else:
                row = form.save(commit=False); row.item = item; row.save()
                return redirect('item',item_id=item.pk)
    return form_page(request,form,'Добавить свободное время',note=item.title)


@login_required
@module_required('booking')
@require_POST
def slot_delete(request,slot_id):
    slot = get_object_or_404(Slot.objects.select_related('item'),pk=slot_id)
    editable_item(request,slot.item_id)
    with transaction.atomic():
        if not Slot.objects.filter(pk=slot.pk,reserved=False).update(reserved=False):
            return HttpResponse('Сначала отмените запись.',status=400)
        slot.delete()
    return redirect('item',item_id=slot.item_id)


@login_required
@module_required('booking')
@require_POST
def book(request,slot_id):
    get_object_or_404(Slot,pk=slot_id,item__published=True)
    try:
        reserve_slot(request.user,slot_id)
    except (ValidationError, OperationalError, IntegrityError) as exc:
        messages.error(request,' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Время сейчас бронируется. Проверьте записи и повторите позже.')
    return redirect('bookings')


@login_required
@module_required('booking')
@require_GET
def bookings(request):
    rows = Booking.objects.select_related('slot__item','user').order_by('-pk')
    if not editor(request.user):
        rows = rows.filter(Q(user=request.user)|Q(slot__item__owner=request.user))
    return render(request,'bookings.html',{'page':paginate(rows,request)})


@login_required
@module_required('booking')
@require_POST
def booking_cancel(request,booking_id):
    row = get_object_or_404(Booking.objects.select_related('slot__item'),pk=booking_id)
    if not editor(request.user) and row.user_id != request.user.pk and row.slot.item.owner_id != request.user.pk:
        raise Http404()
    cancel_booking(row)
    return redirect('bookings')


@module_required('orders')
@require_GET
def cart(request):
    rows = cart_rows(request.session.get('cart',{}))
    return render(request,'cart.html',{'rows':rows,'total':sum(row['subtotal'] for row in rows)})


@module_required('orders')
@require_POST
def cart_update(request,item_id):
    item = get_object_or_404(Item,pk=item_id,published=True,price__gt=0)
    try:
        quantity = int(request.POST.get('quantity','1'))
        if not 0 <= quantity <= 100:
            raise ValueError()
    except ValueError:
        return HttpResponse('Количество: от 0 до 100.',status=400)
    cart = request.session.get('cart',{})
    if quantity and len(cart) >= 50 and str(item.pk) not in cart:
        return HttpResponse('В корзине может быть до 50 товаров.',status=400)
    if quantity:
        cart[str(item.pk)] = quantity
    else:
        cart.pop(str(item.pk),None)
    request.session['cart'] = cart
    return redirect('cart')


@login_required
@module_required('orders')
@require_http_methods(['GET','POST'])
def checkout_view(request):
    form = CheckoutForm(request.POST or None,initial={'nonce':uuid.uuid4()})
    if request.method == 'POST' and form.is_valid():
        try:
            row = checkout(request.user,request.session.get('cart',{}),form.cleaned_data['nonce'],form.cleaned_data['contact'])
        except (ValidationError, IntegrityError, OperationalError) as exc:
            form.add_error(None,' '.join(exc.messages) if isinstance(exc,ValidationError) else 'Повторите оформление заказа.')
        else:
            request.session['cart'] = {}
            return redirect('order',order_id=row.pk)
    return form_page(request,form,'Оформить заказ',note='Стоимость рассчитывается по текущим ценам каталога на сервере.')


@login_required
@module_required('orders')
@require_GET
def orders(request):
    rows = Order.objects.select_related('user','seller').order_by('-pk')
    if not editor(request.user):
        rows = rows.filter(Q(user=request.user)|Q(seller=request.user))
    return render(request,'orders.html',{'page':paginate(rows,request)})


def accessible_order(request,order_id):
    row = get_object_or_404(Order,pk=order_id)
    if not editor(request.user) and request.user.pk not in {row.user_id,row.seller_id}:
        raise Http404()
    return row


@login_required
@module_required('orders')
@require_http_methods(['GET','POST'])
def order_detail(request,order_id):
    row = accessible_order(request,order_id)
    if request.method == 'POST':
        status = request.POST.get('status')
        if not editor(request.user) and row.seller_id != request.user.pk and status != 'canceled':
            return HttpResponse('Изменить статус может продавец.',status=403)
        try:
            transition_order(row,status)
        except ValidationError as exc:
            messages.error(request,' '.join(exc.messages))
        return redirect('order',order_id=row.pk)
    from .payments import configured
    return render(request,'order.html',{'order':row,'can_manage':editor(request.user) or row.seller_id == request.user.pk,
        'payment_ready':configured(), 'can_pay':row.user_id == request.user.pk})


@login_required
@module_required('reviews')
@require_http_methods(['GET','POST'])
def review_edit(request,item_id):
    item = get_object_or_404(Item,pk=item_id,published=True)
    if item.owner_id == request.user.pk:
        return HttpResponse('Нельзя оценивать свою карточку.',status=400)
    old = Review.objects.filter(user=request.user,item=item).first()
    form = ReviewForm(request.POST or None,instance=old)
    if request.method == 'POST' and form.is_valid():
        row = form.save(commit=False); row.item=item; row.user=request.user; row.approved=False
        try:
            row.save()
        except IntegrityError:
            form.add_error(None,'Отзыв уже существует. Обновите страницу.')
        else:
            notify(item.owner_id,'Новый отзыв ожидает проверки',reverse('manage_site'))
            messages.success(request,'Отзыв отправлен на проверку.')
            return redirect('item',item_id=item.pk)
    return form_page(request,form,'Оставить отзыв')


@login_required
@module_required('reviews')
@require_POST
def review_moderate(request,review_id):
    row = get_object_or_404(Review.objects.select_related('item'),pk=review_id)
    editable_item(request,row.item_id)
    if request.POST.get('action') == 'delete':
        row.delete()
    else:
        row.approved=True; row.save(update_fields=['approved'])
    return redirect('manage_site')


@module_required('pages')
@require_GET
def pages(request):
    return render(request,'pages.html',{'page':paginate(Page.objects.filter(published=True).order_by('-created_at'),request)})


@module_required('pages')
@require_GET
def page_detail(request,slug):
    row = get_object_or_404(Page,slug=slug)
    if not row.published and not editor(request.user):
        raise Http404()
    return render(request,'page.html',{'page':row})


@login_required
@module_required('pages')
@require_http_methods(['GET','POST'])
def page_edit(request,page_id=None):
    if not editor(request.user):
        return HttpResponse('Доступно владельцу и сотрудникам.',status=403)
    row = get_object_or_404(Page,pk=page_id) if page_id else None
    form = PageForm(request.POST or None,instance=row)
    if request.method == 'POST' and form.is_valid():
        page = form.save(commit=False)
        if not row:
            page.author=request.user
        page.save()
        return redirect('page',slug=page.slug)
    return form_page(request,form,'Редактор страницы')


@login_required
@module_required('pages')
@require_POST
def page_delete(request,page_id):
    if not editor(request.user):
        return HttpResponse(status=403)
    get_object_or_404(Page,pk=page_id).delete()
    return redirect('manage_site')


@login_required
@require_GET
def manage_site(request):
    is_editor = editor(request.user)
    items = Item.objects.all() if is_editor else Item.objects.filter(owner=request.user)
    leads = Lead.objects.all() if is_editor else Lead.objects.filter(Q(user=request.user)|Q(item__owner=request.user))
    reviews = Review.objects.filter(approved=False)
    if not is_editor:
        reviews = reviews.filter(item__owner=request.user)
    return render(request,'manage.html',{'is_editor':is_editor,'items':items.order_by('-pk')[:100],
        'leads':leads.select_related('item').order_by('-pk')[:100],
        'reviews':reviews.select_related('item','user')[:100],
        'assets':Asset.objects.all().order_by('-pk')[:100] if is_editor else Asset.objects.filter(owner=request.user).order_by('-pk')[:100],
        'pages':Page.objects.order_by('-pk')[:100] if is_editor else [],'can_create':can_create_item(request.user)})
