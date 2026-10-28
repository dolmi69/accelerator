"""Authenticated visual builder; public pages are served only by BrunoHost."""
import hashlib
import io
import json
import warnings
import zipfile
from uuid import UUID
from functools import wraps
from pathlib import Path
from urllib.parse import urlencode

from PIL import Image, ImageOps, UnidentifiedImageError
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from founder.models import LabBuilderAsset, LabBuilderDraft, LabSiteVersion, StartupProfile
from founder.services.access import get_startup
from founder.services import visual_builder as builder
from founder.services.json_utils import bounded_json_loads


def api_errors(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except (builder.DocumentError, json.JSONDecodeError, UnicodeDecodeError) as error:
            return JsonResponse({'error': str(error) if isinstance(error, builder.DocumentError) else 'Некорректные данные запроса.'}, status=400)
    return wrapped


def payload(request):
    if request.content_type != 'application/json' or len(request.body) > builder.MAX_DOCUMENT_BYTES + 4000:
        raise builder.DocumentError('Передайте документ сайта в формате JSON, не больше 200 КБ.')
    try:
        value = bounded_json_loads(request.body.decode('utf-8'), max_depth=32)
    except ValueError:
        raise builder.DocumentError('Некорректный или слишком глубоко вложенный JSON.') from None
    if not isinstance(value, dict):
        raise builder.DocumentError('Некорректный запрос.')
    return value


def validate(startup, document):
    return builder.validate_document(document, startup.builder_assets.values_list('pk', flat=True))


def image_url(startup, key):
    return reverse('lab_builder_asset', args=[startup.pk, key])


def asset_data(asset):
    return {'id': str(asset.pk), 'name': asset.name, 'width': asset.width, 'height': asset.height,
            'size': asset.size, 'url': image_url(asset.startup, asset.pk)}


def draft_for(startup, source=None):
    document = source.builder_document if source and source.builder_document else builder.default_document(startup)
    draft, _ = LabBuilderDraft.objects.get_or_create(startup=startup, defaults={
        'document': document, 'saved_version': source if source and source.builder_document else None})
    return draft


@login_required
@require_GET
def editor(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    source = None
    if request.GET.get('version'):
        try:
            source_id = UUID(request.GET['version'])
        except ValueError:
            raise Http404 from None
        source = get_object_or_404(LabSiteVersion, startup=startup, pk=source_id)
    draft = draft_for(startup, source)
    from founder.hosting import context
    config = {'document': draft.document, 'revision': draft.revision, 'catalog': builder.CATALOG,
        'labels': builder.LABELS, 'assets': [asset_data(a) for a in startup.builder_assets.all()],
        'urls': {name: reverse('lab_builder_' + name, args=[startup.pk]) for name in ('draft', 'preview', 'snapshot', 'upload', 'download')},
        'hostingUrl': reverse('lab_hosting', args=[startup.pk]), 'labUrl': reverse('lab', args=[startup.pk]),
        'hostedUrl': context(startup, draft.saved_version)['url'],
        'newBlockSite': bool(source and not source.builder_document), 'sourceVersion': str(draft.saved_version_id or '')}
    return render(request, 'founder/builder.html', {'startup': startup, 'workspace_tab': 'lab', 'builder_config': config})


@login_required
@require_http_methods(['GET', 'POST'])
@api_errors
def draft(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    if request.method == 'GET':
        current = draft_for(startup)
        return JsonResponse({'document': current.document, 'revision': current.revision})
    data = payload(request)
    document = validate(startup, data.get('document'))
    with transaction.atomic():
        StartupProfile.objects.select_for_update().get(pk=startup.pk)
        current = draft_for(startup)
        if type(data.get('revision')) is not int or current.revision != data['revision']:
            return JsonResponse({'error': 'Черновик изменён в другой вкладке. Скачайте свою копию перед перезагрузкой.', 'revision': current.revision}, status=409)
        current.document = document
        current.revision += 1
        current.save(update_fields=['document', 'revision', 'updated_at'])
    return JsonResponse({'revision': current.revision, 'savedAt': current.updated_at.isoformat()})


@login_required
@require_POST
@api_errors
def preview(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    data = payload(request)
    document = validate(startup, data.get('document'))
    html = builder.render_document(document, data.get('page'), image_url=lambda key: image_url(startup, key), editor=True)
    return JsonResponse({'html': html})


def snapshot_version(startup, current):
    document = validate(startup, current.document)
    if not any(p['blocks'] for p in document['pages']):
        raise builder.DocumentError('Добавьте хотя бы один блок перед сохранением версии.')
    if current.saved_version and current.saved_version.builder_document == document:
        return current.saved_version
    version = LabSiteVersion.objects.create(startup=startup, source=current.saved_version,
        kind=LabSiteVersion.Kind.STATIC, prompt='Визуальный конструктор · ' + ', '.join(p['title'] for p in document['pages'])[:240],
        html=builder.render_document(document), builder_document=document,
        model='visual-builder', edit_method='visual', input_tokens=0, output_tokens=0,
        bruno_report={'title': 'Сохранено в конструкторе', 'items': ['Сохранены страницы, блоки, оформление и изображения.'], 'mood': 'confident', 'usage': 'Без AI · 0 токенов', 'more': []})
    current.saved_version = version
    current.save(update_fields=['saved_version', 'updated_at'])
    return version


@login_required
@require_POST
@api_errors
def snapshot(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    data = payload(request)
    with transaction.atomic():
        StartupProfile.objects.select_for_update().get(pk=startup.pk)
        current = draft_for(startup)
        if type(data.get('revision')) is not int or current.revision != data['revision']:
            return JsonResponse({'error': 'Сначала сохраните актуальный черновик.'}, status=409)
        version = snapshot_version(startup, current)
    return JsonResponse({'version': str(version.pk), 'url': reverse('lab', args=[startup.pk]) + '?version=' + str(version.pk)})


@login_required
@require_GET
def asset(request, startup_id, asset_id):
    startup = get_startup(request, startup_id)
    item = get_object_or_404(LabBuilderAsset, startup=startup, pk=asset_id)
    return asset_response(item)


def asset_response(item, head=False):
    try:
        with item.image.open('rb') as stream:
            content = stream.read(settings.MAX_UPLOAD_BYTES + 1)
    except (OSError, ValueError):
        raise Http404 from None
    if len(content) > settings.MAX_UPLOAD_BYTES:
        raise Http404
    response = HttpResponse(b'' if head else content, content_type='image/webp')
    response['Content-Length'] = len(content)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, max-age=3600'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response


@login_required
@require_POST
@api_errors
def upload(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    incoming = request.FILES.get('image')
    if not incoming or incoming.size > settings.MAX_UPLOAD_BYTES:
        raise builder.DocumentError('Выберите JPG, PNG или WebP размером до 5 МБ.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(incoming) as original:
                if original.format not in {'JPEG', 'PNG', 'WEBP'} or original.width * original.height > 20_000_000:
                    raise builder.DocumentError('Поддерживаются JPG, PNG и WebP до 20 мегапикселей.')
                original.seek(0)
                original.load()
                image = ImageOps.exif_transpose(original).convert('RGBA' if 'A' in original.getbands() else 'RGB')
                image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                image.save(buffer, format='WEBP', quality=88, method=4)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        if isinstance(error, builder.DocumentError):
            raise
        raise builder.DocumentError('Не удалось прочитать изображение. Попробуйте другой JPG, PNG или WebP.') from None
    content = buffer.getvalue()
    if len(content) > settings.MAX_UPLOAD_BYTES:
        raise builder.DocumentError('Изображение слишком большое. Уменьшите его разрешение.')
    digest = hashlib.sha256(content).hexdigest()
    item = None
    try:
        with transaction.atomic():
            StartupProfile.objects.select_for_update().get(pk=startup.pk)
            existing = startup.builder_assets.filter(digest=digest).first()
            if existing:
                return JsonResponse(asset_data(existing))
            total = startup.builder_assets.aggregate(size=Sum('size'))['size'] or 0
            if startup.builder_assets.count() >= 100 or total + len(content) > 30 * 1024 * 1024:
                raise builder.DocumentError('В библиотеке проекта может быть до 100 изображений и 30 МБ.')
            item = LabBuilderAsset(startup=startup, name=Path(incoming.name).name[:160], width=image.width, height=image.height, size=len(content), digest=digest)
            item.image.save(f'{startup.pk}/{item.pk}.webp', ContentFile(content), save=False)
            item.save(force_insert=True)
    except Exception:
        if item and item.image:
            item.image.delete(save=False)
        raise
    return JsonResponse(asset_data(item), status=201)


@login_required
@require_GET
def download(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    current = draft_for(startup)
    document = validate(startup, current.document)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        # Relative URLs make the export portable to subdirectories and file://.
        for page in document['pages']:
            prefix = '../' if page['slug'] else ''
            def page_url(target):
                return prefix + (target['slug'] + '/index.html' if target['slug'] else 'index.html')
            html = builder.render_document(document, page['id'], image_url=lambda key: prefix + f'assets/{key}.webp', page_url=page_url)
            archive.writestr((page['slug'] + '/' if page['slug'] else '') + 'index.html', html)
        for item in startup.builder_assets.filter(pk__in=builder.asset_ids(document)):
            with item.image.open('rb') as stream:
                archive.writestr(f'assets/{item.pk}.webp', stream.read(settings.MAX_UPLOAD_BYTES + 1))
        archive.writestr('bruno-builder.json', json.dumps(document, ensure_ascii=False, indent=2))
    response = HttpResponse(buffer.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = 'attachment; filename="brunolab-site.zip"'
    response['Cache-Control'] = 'no-store'
    return response


def version_preview(request, startup, version):
    document = validate(startup, version.builder_document)
    page = next((p for p in document['pages'] if p['slug'] == request.GET.get('page', '')), None)
    if not page:
        raise Http404
    url = reverse('lab_preview', args=[startup.pk, version.pk])
    html = builder.render_document(document, page['id'], image_url=lambda key: image_url(startup, key),
        page_url=lambda p: url + '?' + urlencode({'page': p['slug']}))
    response = HttpResponse(html)
    response['Content-Security-Policy'] = "sandbox allow-same-origin; default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; script-src 'none'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
    response['X-Content-Type-Options'] = 'nosniff'
    response['X-Frame-Options'] = 'SAMEORIGIN'
    response['Cache-Control'] = 'no-store'
    return response
