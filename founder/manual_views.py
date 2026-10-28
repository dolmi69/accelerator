"""Manual appearance/content edits of immutable generated website versions."""
import hashlib
import json
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse, FileResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from founder.models import LabSiteVersion, LabManualDraft, StartupProfile
from founder.services.access import get_startup
from founder.services import manual_site, manual_assets
from founder.services.visual_builder import DocumentError
from founder.services.json_utils import bounded_json_loads
from founder.services.lab_bruno import protect_service_secrets
from founder.services.qwen import QwenOutputError
from founder.services.generated_backend import validate_files, export_backend
from founder.builder_views import asset_data, image_url


def errors(view):
    @wraps(view)
    def wrapped(*args,**kwargs):
        try:return view(*args,**kwargs)
        except (DocumentError,QwenOutputError)as error:
            return JsonResponse({'error':str(error)},status=400)
    return wrapped


def context(request,startup_id,version_id):
    startup=get_startup(request,startup_id,edit=True)
    version=get_object_or_404(LabSiteVersion,startup=startup,pk=version_id,kind__in=['static','generated'])
    return startup,version


def source_for(version):
    if version.builder_document:raise DocumentError('Этот сайт уже редактируется в блочном конструкторе.')
    html=version.backend_files.get('index.html',version.html) if version.kind=='generated' else version.html
    if not isinstance(html,str)or not html.strip()or len(html.encode())>2_000_000:raise DocumentError('В версии нет поддерживаемого HTML-интерфейса.')
    return manual_site.Source(html)


def current_for(startup,version):
    current,_=LabManualDraft.objects.get_or_create(startup=startup,source=version,defaults={'changes':{'texts':{},'elements':{}}})
    return current


def payload(request):
    if request.content_type!='application/json' or len(request.body)>manual_site.MAX_JSON+4000:raise DocumentError('Передайте изменения в формате JSON до 180 КБ.')
    try:data=bounded_json_loads(request.body.decode('utf-8'),max_depth=20)
    except ValueError:raise DocumentError('Некорректный JSON.')from None
    if not isinstance(data,dict):raise DocumentError('Некорректный запрос.')
    return data


def validate(startup,source,raw):
    clean=manual_site.validate_changes(source,raw,startup.builder_assets.values_list('pk',flat=True))
    protect_service_secrets(json.dumps(clean,ensure_ascii=False))
    return clean


@login_required
@require_GET
def editor(request,startup_id,version_id):
    startup,version=context(request,startup_id,version_id)
    if version.builder_document:return redirect(reverse('lab_builder',args=[startup.pk])+'?version='+str(version.pk))
    source=source_for(version);current=current_for(startup,version)
    config={'source':str(version.pk),'digest':source.digest,'kind':version.kind,'changes':current.changes,'revision':current.revision,
        'elements':source.catalog(),'assets':[asset_data(a)for a in startup.builder_assets.all()],
        'urls':{name:reverse('lab_manual_'+name,args=[startup.pk,version.pk])for name in ('save','preview','snapshot','download')},
        'uploadUrl':reverse('lab_builder_upload',args=[startup.pk]),'hostingUrl':reverse('lab_hosting',args=[startup.pk]),
        'labUrl':reverse('lab',args=[startup.pk])+'?version='+str(version.pk),
        'blockUrl':reverse('lab_builder',args=[startup.pk])}
    return render(request,'founder/manual_editor.html',{'startup':startup,'workspace_tab':'lab','manual_config':config})


@login_required
@require_POST
@errors
def save(request,startup_id,version_id):
    startup,version=context(request,startup_id,version_id);source=source_for(version);data=payload(request)
    if data.get('digest')!=source.digest:return JsonResponse({'error':'Исходная версия изменилась. Перезагрузите редактор.'},status=409)
    changes=validate(startup,source,data.get('changes'))
    # Validate ranges and final file limits before accepting an autosaved draft.
    compiled(version,source,changes)
    with transaction.atomic():
        StartupProfile.objects.select_for_update().get(pk=startup.pk)
        current=current_for(startup,version)
        if type(data.get('revision'))is not int or data['revision']!=current.revision:
            return JsonResponse({'error':'Эту версию уже изменили в другой вкладке. Скачайте копию перед перезагрузкой.'},status=409)
        current.changes=changes;current.revision+=1;current.save(update_fields=['changes','revision','updated_at'])
    return JsonResponse({'revision':current.revision})


@login_required
@require_POST
@errors
def preview(request,startup_id,version_id):
    startup,version=context(request,startup_id,version_id);source=source_for(version);data=payload(request)
    if data.get('digest')!=source.digest:return JsonResponse({'error':'Исходная версия изменилась.'},status=409)
    changes=validate(startup,source,data.get('changes'))
    html=manual_site.render(source,changes,preview=True,files=version.backend_files,image_url=lambda key:image_url(startup,key))
    return JsonResponse({'html':manual_assets.preview_urls(version,html)})


def compiled(version,source,changes):
    keys={item[prop]for item in changes['elements'].values()for prop in ('image','backgroundImage')if item.get(prop)}
    assets={str(a.pk):a for a in version.startup.builder_assets.filter(pk__in=keys)}
    def url(key):
        if key not in assets:raise DocumentError('Изображение больше недоступно.')
        return f'/assets/bruno-images/{key}.webp'
    html=manual_site.render(source,changes,image_url=url)
    protect_service_secrets(html)
    if version.kind=='generated':
        files={**version.backend_files,'index.html':html};validate_files(files)
    else:
        files=dict(version.backend_files)
        if len(html.encode())>2_000_000:raise DocumentError('Готовая страница превышает 2 МБ. Уменьшите число изображений.')
    return html,files


def snapshot_version(startup,version,source,current):
    changes=validate(startup,source,current.changes)
    if not changes['texts']and not changes['elements']and'title'not in changes:return version
    digest=hashlib.sha256(('source-edits-v2-assets'+json.dumps(changes,ensure_ascii=False,sort_keys=True)).encode()).hexdigest()
    if current.saved_version and current.saved_version.bruno_report.get('manual',{}).get('digest')==digest:return current.saved_version
    html,files=compiled(version,source,changes)
    saved=LabSiteVersion.objects.create(startup=startup,source=current.saved_version or version,kind=version.kind,prompt='Ручное редактирование AI-сайта',
        html=html,backend_files=files,backend_modules=version.backend_modules,presentation=manual_assets.presentation(startup,html,files,version.presentation),
        model='manual-editor',edit_method='manual',input_tokens=0,output_tokens=0,
        bruno_report={'title':'Ручные изменения сохранены','items':['Обновлены выбранные тексты, изображения или оформление.',
            'Серверный код и остальные файлы сохранены без изменений.'],'mood':'confident','usage':'Без AI · 0 токенов','more':[],
            'manual':{'source':str(version.pk),'digest':digest}})
    current.saved_version=saved;current.save(update_fields=['saved_version','updated_at'])
    return saved


@login_required
@require_POST
@errors
def snapshot(request,startup_id,version_id):
    startup,version=context(request,startup_id,version_id);source=source_for(version);data=payload(request)
    with transaction.atomic():
        StartupProfile.objects.select_for_update().get(pk=startup.pk)
        current=current_for(startup,version)
        if type(data.get('revision'))is not int or current.revision!=data['revision']:
            return JsonResponse({'error':'Сначала сохраните актуальные изменения.'},status=409)
        saved=snapshot_version(startup,version,source,current)
    return JsonResponse({'version':str(saved.pk),'url':reverse('lab',args=[startup.pk])+'?version='+str(saved.pk)})


@login_required
@require_GET
@errors
def download(request,startup_id,version_id):
    startup,version=context(request,startup_id,version_id);source=source_for(version);current=current_for(startup,version)
    changes=validate(startup,source,current.changes);html,files=compiled(version,source,changes)
    if version.kind=='generated':
        from types import SimpleNamespace
        return FileResponse(export_backend(SimpleNamespace(backend_files=files,html=html,startup_id=startup.pk,presentation=manual_assets.presentation(startup,html,files,version.presentation))),as_attachment=True,filename='brunolab-site.zip',content_type='application/zip')
    from types import SimpleNamespace
    html=manual_assets.inline(SimpleNamespace(html=html,backend_files=files,startup_id=startup.pk,presentation=manual_assets.presentation(startup,html,files,version.presentation)),html)
    response=HttpResponse(html,content_type='text/html; charset=utf-8');response['Content-Disposition']='attachment; filename="index.html"';return response
