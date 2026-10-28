"""Portable community preview: no private asset URLs or platform-origin scripts.

Image thumbnails are embedded once, and page navigation stays inside the opaque
sandbox. Existing opt-in community event tracking can keep its document listeners.
"""
import base64
import io
from functools import lru_cache

from PIL import Image
from django.core.files.storage import default_storage
from django.utils.html import json_script
from founder.services import visual_builder as builder

PLACEHOLDER = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7#'


@lru_cache(maxsize=128)
def _thumbnail(name, digest):
    with default_storage.open(name, 'rb') as file, Image.open(file) as image:
        image.thumbnail((640,640),Image.Resampling.LANCZOS)
        output=io.BytesIO()
        image.save(output,format='WEBP',quality=65)
    return 'data:image/webp;base64,'+base64.b64encode(output.getvalue()).decode('ascii')


def render(version):
    assets=list(version.startup.builder_assets.filter(pk__in=builder.asset_ids(version.builder_document)))
    document=builder.validate_document(version.builder_document,[a.pk for a in assets])
    images={str(a.pk):_thumbnail(a.image.name,a.digest) for a in assets}
    pages={}
    for page in document['pages']:
        pages[page['slug']]={'title':page['title'],'html':builder.render_document(document,page['id'],
            image_url=lambda key:PLACEHOLDER+key,page_url=lambda p:'#bruno-page='+p['slug'])}
    html=pages['']['html']
    payload=str(json_script({'pages':pages,'images':images},'bruno-builder-pages'))
    return html.replace('</body>',payload+RUNTIME+'</body>')


RUNTIME='''<script>(()=>{
const data=JSON.parse(document.getElementById('bruno-builder-pages').textContent);
function hydrate(){document.querySelectorAll('img').forEach(image=>{const key=image.getAttribute('src').split('#').pop();if(data.images[key])image.src=data.images[key];});}
hydrate();
document.addEventListener('click',event=>{
 const link=event.target.closest('a');if(!link)return;
 const href=link.getAttribute('href');if(!href||!href.startsWith('#bruno-page='))return;
 event.preventDefault();const page=data.pages[href.slice(12)];if(!page)return;
 const parsed=new DOMParser().parseFromString(page.html,'text/html');document.body.innerHTML=parsed.body.innerHTML;
 document.title=page.title;hydrate();scrollTo(0,0);
});
})();</script>'''
