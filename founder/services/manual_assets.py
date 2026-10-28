"""Project-scoped images for manually edited AI sites, outside model code files."""
import base64
import re

UUID_PATTERN=r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
PATH=re.compile(r'/assets/bruno-images/('+UUID_PATTERN+r')\.webp')


def records(version):
    from founder.models import LabBuilderAsset
    metadata=(getattr(version,'presentation',None)or{}).get('manual_assets',[])
    if not isinstance(metadata,list)or not metadata:return []
    # The source itself and the project FK both constrain the release's files.
    contents=[version.html,*getattr(version,'backend_files',{}).values()]
    refs={key for content in contents if isinstance(content,str) for key in PATH.findall(content)}
    ids=[key for key in metadata if isinstance(key,str)and key in refs and re.fullmatch(UUID_PATTERN,key)]
    return list(LabBuilderAsset.objects.filter(startup_id=version.startup_id,pk__in=ids))


def presentation(startup,html,files,previous):
    from founder.models import LabBuilderAsset
    refs={key for content in [html,*files.values()]if isinstance(content,str)for key in PATH.findall(content)}
    owned=sorted(str(key)for key in LabBuilderAsset.objects.filter(startup=startup,pk__in=refs).values_list('pk',flat=True))
    result=dict(previous or {})
    if owned:result['manual_assets']=owned
    else:result.pop('manual_assets',None)
    return result


def binaries(version):
    from founder.services.manual_site import image_data
    return {f'assets/bruno-images/{asset.pk}.webp':base64.b64decode(image_data(asset.image.name,asset.digest).split(',',1)[1])for asset in records(version)}


def stage(version,root):
    for path,content in binaries(version).items():
        target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)


def inline(version,html):
    from founder.services.manual_site import image_data
    urls={str(a.pk):image_data(a.image.name,a.digest)for a in records(version)}
    return PATH.sub(lambda match:urls.get(match[1],match[0]),html)


def preview_urls(version,html):
    from django.urls import reverse
    urls={str(a.pk):reverse('lab_builder_asset',args=[version.startup_id,a.pk])for a in records(version)}
    return PATH.sub(lambda match:urls.get(match[1],match[0]),html)
