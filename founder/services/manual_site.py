"""Source-preserving visual edits of AI HTML. Never serialize the source DOM.

Only explicit text ranges and the selected opening tags are patched. Script,
server and stylesheet files retain their original bytes. The editing preview is
separately sanitized, script-free and annotated; its markup is never saved.
"""
import base64
import hashlib
import io
import re
from dataclasses import dataclass, field
from functools import lru_cache
from html import escape, unescape
from html.parser import HTMLParser

from PIL import Image
from django.core.files.storage import default_storage
from founder.services.visual_builder import DocumentError, safe_link

MAX_CHANGES = 200
MAX_JSON = 180_000
VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
SKIP = {'script','style','head','title','textarea','select','option','template','noscript','svg','math'}
EDITABLE = {'body','header','footer','main','section','article','aside','nav','div','span','h1','h2','h3','h4','h5','h6','p','a','button','label','small','strong','em','b','i','li','ul','ol','figure','figcaption','img','svg','form','input','textarea','select','details','summary','table','tr','td','th','hr','pre','code','blockquote'}
DROP = {'script','iframe','object','embed','base','meta','template','noscript','applet','frame','frameset'}
COLORS = {'color':'color','backgroundColor':'background-color'}
NUMBERS = {'fontSize':('font-size',8,120), 'borderRadius':('border-radius',0,100), 'padding':('padding',0,160), 'gap':('gap',0,100)}
ENUMS = {'textAlign':('text-align',{'left','center','right'}),'fontWeight':('font-weight',{'400','500','600','700','800'}),'objectFit':('object-fit',{'cover','contain'}),'fontFamily':('font-family',{'modern','serif','mono'})}
STYLE_KEYS = set(COLORS)|set(NUMBERS)|set(ENUMS)
LABELS = {'body':'Страница','header':'Шапка','nav':'Навигация','main':'Основное','section':'Секция','article':'Карточка','div':'Контейнер','span':'Текст','h1':'Заголовок H1','h2':'Заголовок H2','h3':'Заголовок H3','p':'Абзац','a':'Ссылка','button':'Кнопка','img':'Изображение','svg':'Иллюстрация','footer':'Подвал','form':'Форма','input':'Поле','label':'Подпись','li':'Пункт списка'}
ATTR = re.compile(r'''([^\s=/>]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s>]+))?''')


@dataclass
class Node:
    key: str
    tag: str
    start: int
    open_end: int
    attrs: dict
    parent: str | None
    close_start: int = 0
    end: int = 0
    texts: list = field(default_factory=list)
    ignored: bool = False


class Source(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=False)
        self.html=html;self.nodes={};self.texts={};self.stack=[]
        self.lines=[0]+[m.end() for m in re.finditer('\n',html)]
        self.feed(html);self.close()
        for node in self.nodes.values():
            if not node.end:node.close_start=node.end=len(html)
        self.digest=hashlib.sha256(html.encode()).hexdigest()

    def pos(self):
        line,column=self.getpos();return self.lines[line-1]+column

    def handle_starttag(self, tag, attrs):
        if len(self.nodes)>=2500:raise DocumentError('Сайт содержит слишком много элементов для ручного редактора.')
        start=self.pos();parent=self.nodes[self.stack[-1]] if self.stack else None
        # HTML's common optional end tags; no source bytes are changed by this.
        if self.stack and tag in {'li','p','tr','td','th','option'} and self.nodes[self.stack[-1]].tag==tag:
            self._close(self.stack.pop(),start,start)
            parent=self.nodes[self.stack[-1]] if self.stack else None
        key=f'e{len(self.nodes)}'
        node=Node(key,tag,start,start+len(self.get_starttag_text()),dict(attrs),parent.key if parent else None,
                  ignored=bool(parent and (parent.ignored or parent.tag in SKIP)))
        self.nodes[key]=node
        if tag not in VOID:self.stack.append(key)
        else:node.close_start=node.end=node.open_end

    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in VOID and self.stack:
            key=self.stack.pop();self._close(key,self.nodes[key].open_end,self.nodes[key].open_end)

    def _close(self,key,start,end):
        node=self.nodes[key];node.close_start=start;node.end=end

    def handle_endtag(self,tag):
        found=next((i for i in range(len(self.stack)-1,-1,-1) if self.nodes[self.stack[i]].tag==tag),None)
        if found is None:return
        start=self.pos();end=self.html.find('>',start)+1
        for key in self.stack[found:]:self._close(key,start,end)
        del self.stack[found:]

    def text(self,start,end):
        if not self.stack:return
        node=self.nodes[self.stack[-1]]
        if node.ignored or node.tag in SKIP or node.tag not in EDITABLE:return
        raw=self.html[start:end]
        if node.texts and self.texts[node.texts[-1]]['end']==start:
            item=self.texts[node.texts[-1]];item['end']=end;item['text']+=unescape(raw)
        else:
            key=f't{len(self.texts)}'
            self.texts[key]={'id':key,'owner':node.key,'start':start,'end':end,'text':unescape(raw)};node.texts.append(key)

    def handle_data(self,data):self.text(self.pos(),self.pos()+len(data))
    def handle_entityref(self,name):
        prefix='&'+name;self.text(self.pos(),self.pos()+len(prefix)+(self.html[self.pos():].startswith(prefix+';')))
    def handle_charref(self,name):
        prefix='&#'+name;self.text(self.pos(),self.pos()+len(prefix)+(self.html[self.pos():].startswith(prefix+';')))

    def catalog(self):
        result=[]
        for node in self.nodes.values():
            if node.tag not in EDITABLE or node.ignored:continue
            texts=[self.texts[k] for k in node.texts if self.texts[k]['text'].strip()]
            label=' '.join(t['text'].strip() for t in texts)
            if not label:label=node.attrs.get('alt') or node.attrs.get('aria-label') or node.attrs.get('id') or ''
            result.append({'id':node.key,'tag':node.tag,'type':LABELS.get(node.tag,node.tag),
                'label':label[:100], 'parent':node.parent, 'texts':[{'id':t['id'],'value':t['text']} for t in texts],
                'image':node.tag in {'img','svg'},'link':node.attrs.get('href','') if node.tag=='a' else None,
                'alt':node.attrs.get('alt',''),'placeholder':node.attrs.get('placeholder') if node.tag in {'input','textarea'} else None})
        return result


def patch_attrs(raw,updates,remove=()):
    """Preserve every unrelated attribute literally, including event handlers."""
    prefix=re.match(r'<\s*[^\s/>]+',raw)
    if not prefix:return raw
    changes=[];seen=set();remove=set(remove)
    for match in ATTR.finditer(raw,prefix.end()):
        name=match[1].lower()
        if name in remove:changes.append((match.start(),match.end(),''))
        elif name in updates:
            value=updates[name]
            replacement='' if name in seen or value is None else f'{match[1]}="{escape(str(value),quote=True)}"'
            changes.append((match.start(),match.end(),replacement));seen.add(name)
    missing=''.join(f' {name}="{escape(str(value),quote=True)}"' for name,value in updates.items() if name not in seen and value is not None)
    if missing:
        at=len(raw)-2 if raw.endswith('/>') else len(raw)-1
        changes.append((at,at,missing))
    return apply_ranges(raw,changes)


def apply_ranges(html,changes):
    ordered=sorted(changes,key=lambda item:(item[0],item[1]))
    previous=-1
    for start,end,value in ordered:
        if start<previous or not 0<=start<=end<=len(html):raise DocumentError('Правки пересекаются. Перезагрузите исходную версию.')
        previous=end
    for start,end,value in reversed(ordered):html=html[:start]+value+html[end:]
    return html


def validate_changes(source,raw,asset_ids):
    import json
    if not isinstance(raw,dict) or set(raw)-{'texts','elements','title'}:raise DocumentError('Некорректные ручные изменения.')
    if len(json.dumps(raw,ensure_ascii=False).encode())>MAX_JSON:raise DocumentError('Слишком большой объём изменений.')
    texts=raw.get('texts',{});elements=raw.get('elements',{})
    if not isinstance(texts,dict) or not isinstance(elements,dict) or len(texts)+len(elements)>MAX_CHANGES:raise DocumentError('За один черновик можно изменить до 200 элементов и текстов.')
    allowed={n['id'] for n in source.catalog()};assets=set(map(str,asset_ids));clean={'texts':{},'elements':{}}
    for key,value in texts.items():
        if key not in source.texts or not source.texts[key]['text'].strip() or not isinstance(value,str) or len(value)>6000 or '\0' in value:
            raise DocumentError('Текстовый фрагмент не найден или превышает 6000 символов.')
        if value!=source.texts[key]['text']:clean['texts'][key]=value
    for key,value in elements.items():
        if key not in allowed or not isinstance(value,dict) or set(value)-{'image','alt','href','placeholder','style','hidden','backgroundImage'}:raise DocumentError('Неизвестный элемент или параметр.')
        node=source.nodes[key];out={}
        for prop in ('image','backgroundImage'):
            if prop in value:
                asset=value[prop]
                if not isinstance(asset,str) or asset not in assets or (prop=='image' and node.tag not in {'img','svg'}):raise DocumentError('Выберите изображение из библиотеки этого проекта.')
                out[prop]=asset
        for prop in ('alt','placeholder'):
            if prop in value:
                if (prop=='alt' and node.tag not in {'img','svg'}) or (prop=='placeholder' and node.tag not in {'input','textarea'}) or not isinstance(value[prop],str) or len(value[prop])>300:raise DocumentError('Некорректная подпись элемента.')
                out[prop]=value[prop]
        if 'href' in value:
            if node.tag!='a':raise DocumentError('У этого элемента нет ссылки.')
            out['href']=safe_link(value['href'])
        if 'hidden' in value:
            if type(value['hidden'])is not bool or node.tag=='body':raise DocumentError('Нельзя скрыть всю страницу.')
            if value['hidden']:out['hidden']=True
        styles=value.get('style',{})
        if not isinstance(styles,dict) or set(styles)-STYLE_KEYS:raise DocumentError('Неизвестная настройка оформления.')
        cleanstyle={}
        for prop,item in styles.items():
            if prop in COLORS:
                if not isinstance(item,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',item):raise DocumentError('Цвет задаётся в формате #147d73.')
            elif prop in NUMBERS:
                _,low,high=NUMBERS[prop]
                if type(item)is not int or not low<=item<=high:raise DocumentError(f'Значение должно быть от {low} до {high}.')
            elif not isinstance(item,str) or item not in ENUMS[prop][1]:raise DocumentError('Недопустимая настройка оформления.')
            cleanstyle[prop]=item
        if cleanstyle:out['style']=cleanstyle
        if out:clean['elements'][key]=out
    if 'title' in raw:
        title=raw['title']
        if not isinstance(title,str) or not title.strip() or len(title)>160:raise DocumentError('Название вкладки: от 1 до 160 символов.')
        clean['title']=title
    return clean


@lru_cache(maxsize=64)
def image_data(name,digest):
    with default_storage.open(name,'rb')as stream,Image.open(stream)as original:
        image=original.copy();image.thumbnail((1600,1600),Image.Resampling.LANCZOS)
    output=io.BytesIO()
    for side,quality in ((1600,82),(1200,76),(900,68),(640,60)):
        image.thumbnail((side,side),Image.Resampling.LANCZOS);output=io.BytesIO();image.save(output,format='WEBP',quality=quality)
        if output.tell()<=220_000:break
    if output.tell()>250_000:raise DocumentError('Изображение слишком сложное. Выберите файл меньшего размера.')
    return 'data:image/webp;base64,'+base64.b64encode(output.getvalue()).decode('ascii')


def style_text(changes,image_url):
    result=[]
    for key,value in changes.get('style',{}).items():
        if key in COLORS:name=COLORS[key];css=value
        elif key in NUMBERS:name=NUMBERS[key][0];css=str(value)+'px'
        else:
            name=ENUMS[key][0];css=({'modern':'system-ui,sans-serif','serif':'Georgia,serif','mono':'ui-monospace,monospace'}[value] if key=='fontFamily' else value)
        result.append(f'{name}:{css}!important')
    if changes.get('hidden'):result.append('display:none!important')
    if changes.get('backgroundImage'):result.append(f'background-image:url("{image_url(changes["backgroundImage"])}")!important;background-size:cover!important;background-position:center!important')
    return ';'.join(result)


def render(source,changes,*,image_url,preview=False,files=None):
    files=files or {};patches=[];suppressed=[]
    replaced=[(n.open_end,n.close_start)for n in source.nodes.values()if n.tag=='svg'and changes['elements'].get(n.key,{}).get('image')]
    if preview:
        for node in source.nodes.values():
            if any(start<=node.start<end for start,end in replaced):continue
            if node.tag in DROP or (node.tag=='link' and (node.attrs.get('rel')or'').lower()!='stylesheet'):
                if not any(start<=node.start<end for start,end in suppressed):suppressed.append((node.start,node.end))
        patches.extend((start,end,'')for start,end in suppressed)
    def skipped(node):return any(start<=node.start<end for start,end in suppressed+replaced)
    for node in source.nodes.values():
        if skipped(node):continue
        raw=source.html[node.start:node.open_end];edits=changes['elements'].get(node.key,{})
        updates={};remove=set()
        for prop in ('alt','href','placeholder'):
            if prop in edits:
                updates['aria-label' if prop=='alt'and node.tag=='svg'else prop]=edits[prop]
                if prop=='alt'and node.tag=='svg'and edits[prop]:updates.update({'role':'img','aria-hidden':None})
        if edits.get('image'):
            url=image_url(edits['image'])
            if node.tag=='img':updates['src']=url;remove.update({'srcset','sizes'})
            else:
                inner=f'<image href="{escape(url,quote=True)}" x="0" y="0" width="100%" height="100%" preserveAspectRatio="xMidYMid meet"/>'
                if raw.endswith('/>'):
                    raw=raw[:-2]+'>';inner+='</svg>'
                patches.append((node.open_end,node.close_start,inner))
        addition=style_text(edits,image_url)
        if addition:updates['style']=(node.attrs.get('style')or'')+';'+addition
        if preview:
            remove.update(name for name in node.attrs if name.startswith('on')or name in {'srcdoc','contenteditable','autofocus','is','formaction','target','srcset'}or name.startswith('data-bm-'))
            for attr in ('href','xlink:href','src','action'):
                value=updates.get(attr,node.attrs.get(attr))
                if value and re.match(r'\s*(?:javascript|vbscript|data:text/html)\s*:',re.sub(r'[\x00-\x20]','',value),re.I):remove.add(attr);updates.pop(attr,None)
            if node.tag in EDITABLE and not node.ignored:updates['data-bm-element']=node.key
            if node.tag=='link':
                href=(node.attrs.get('href')or'').lstrip('/')
                if href in files and href.endswith('.css'):
                    css=re.sub(r'</style',r'<\\/style',files[href],flags=re.I)
                    patches.append((node.start,node.open_end,'<style>'+css+'</style>'));continue
                patches.append((node.start,node.open_end,''));continue
        patched=patch_attrs(raw,updates,remove)
        if patched!=source.html[node.start:node.open_end]:patches.append((node.start,node.open_end,patched))
    for key,slot in source.texts.items():
        node=source.nodes[slot['owner']]
        if skipped(node):continue
        if preview and slot['text'].strip():
            text=changes['texts'].get(key,slot['text'])
            patches.append((slot['start'],slot['end'],f'<bl-text data-bm-text="{key}" data-bm-owner="{node.key}">{escape(text).replace(chr(10),"<br>")}</bl-text>'))
        elif key in changes['texts']:
            patches.append((slot['start'],slot['end'],escape(changes['texts'][key]).replace('\n','<br>')))
    if 'title'in changes:
        title=next((n for n in source.nodes.values()if n.tag=='title'),None)
        if title:patches.append((title.open_end,title.close_start,escape(changes['title'])))
    html=apply_ranges(source.html,patches)
    if preview:
        csp="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'"
        head=f'<meta http-equiv="Content-Security-Policy" content="{csp}"><meta name="viewport" content="width=device-width,initial-scale=1"><style>bl-text{{display:inline}}[data-bm-element]{{scroll-margin-top:30px}}.bm-selected{{outline:2px solid #147d73!important;outline-offset:-2px!important}}bl-text[contenteditable]:focus{{outline:2px dashed #147d73;outline-offset:3px}}[data-bm-element]:hover{{outline:1px solid #147d7350;outline-offset:-1px}}</style>'
        html,count=re.subn(r'(<head\b[^>]*>)',lambda m:m[0]+head,html,count=1,flags=re.I)
        if not count:
            html=re.sub(r'(<html\b[^>]*>)',lambda m:m[0]+'<head>'+head+'</head>',html,count=1,flags=re.I) if re.search(r'<html\b',html,re.I) else '<head>'+head+'</head>'+html
    return html
