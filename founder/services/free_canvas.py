"""Bounded free-positioned elements shared by editor, AI, preview and hosting."""
import copy
import uuid
from html import escape

WIDTH = 1120
MOBILE_WIDTH = 390
MAX_ELEMENTS = 60
TYPES = {'text', 'image', 'button', 'shape', 'line', 'art', 'faq'}
DEFAULT_STYLE = {'color':'#122338','background':'transparent','borderColor':'#147d73','borderWidth':0,
    'radius':0,'fontWeight':'500','fontFamily':'inherit','textAlign':'left','opacity':100,'rotation':0,'objectFit':'cover'}
DEFAULT_PROPS = {'height':620,'mobileHeight':740,'elements':[]}
# Optional fields preserve measured typography during block conversion without
# invalidating existing schema-1 documents or accepting arbitrary CSS.
EXTRA_STYLE_NUMBERS = {'lineHeight':(500,4000),'letterSpacing':(-200,500),
    'paddingX':(0,200),'paddingY':(0,200),'mobilePaddingX':(0,200),'mobilePaddingY':(0,200)}
EXTRA_STYLE_FLAGS = {'trailingIcon','plainLink'}


def new_element(kind):
    if kind not in TYPES:
        raise ValueError('Unknown canvas element')
    sizes={'text':(450,120,48),'image':(340,280,20),'button':(220,56,16),'shape':(260,200,20),'line':(320,4,20),'art':(340,340,20),'faq':(560,180,16)}
    width,height,font=sizes[kind]
    mobile_width=min(width,330)
    style=copy.deepcopy(DEFAULT_STYLE)
    if kind in {'button','shape','line'}:style['background']='#147d73'
    if kind=='button':style.update(color='#ffffff',radius=8,textAlign='center',fontWeight='600')
    if kind=='shape':style['radius']=20
    return {'id':uuid.uuid4().hex,'type':kind,'content':{'text':'Ваш текст' if kind=='text' else 'Открыть' if kind=='button' else 'Ваш вопрос' if kind=='faq' else '',
        'image':'','alt':'Напишите ответ на вопрос.'if kind=='faq'else'','link':'#'},'desktop':{'x':80,'y':80,'width':width,'height':height,'fontSize':font,'hidden':False},
        'mobile':{'x':30,'y':60,'width':mobile_width,'height':height,'fontSize':min(font,34),'hidden':False},'style':style,'locked':False}


def validate(raw,assets,identifier):
    from founder.services.visual_builder import DocumentError, safe_link, COLOR
    def number(value,low,high):
        if type(value)is not int or not low<=value<=high:
            raise DocumentError(f'Размер или положение свободного элемента: допустимо от {low} до {high}.')
        return value
    if not isinstance(raw,dict)or not {'height','mobileHeight','elements'}<=set(raw)or set(raw)-{'height','mobileHeight','elements','template'}:
        raise DocumentError('Некорректный свободный блок.')
    result={'height':number(raw['height'],40,4000),'mobileHeight':number(raw['mobileHeight'],40,6000),'elements':[]}
    if 'template'in raw:
        if not isinstance(raw['template'],str)or raw['template']not in {'nav','hero','features','text','image','gallery','quotes','pricing','faq','cta','footer'}:raise DocumentError('Неизвестный шаблон секции.')
        result['template']=raw['template']
    if not isinstance(raw['elements'],list)or len(raw['elements'])>MAX_ELEMENTS:
        raise DocumentError('В свободном блоке может быть до 60 элементов.')
    for item in raw['elements']:
        if not isinstance(item,dict)or set(item)!={'id','type','content','desktop','mobile','style','locked'}or not isinstance(item['type'],str)or item['type']not in TYPES:
            raise DocumentError('Неизвестный свободный элемент.')
        content=item['content'];style=item['style']
        if not isinstance(content,dict)or set(content)!={'text','image','alt','link'}:
            raise DocumentError('Некорректное содержимое свободного элемента.')
        if any(not isinstance(content[k],str)or len(content[k])>limit or '\0'in content[k]for k,limit in [('text',5000),('alt',5000 if item['type']=='faq'else 300)]):
            raise DocumentError('Проверьте текст и описание элемента.')
        if not isinstance(content['image'],str)or content['image']and content['image']not in assets:
            raise DocumentError('Изображение недоступно в этом проекте.')
        cleaned={'id':identifier(item['id']),'type':item['type'],'content':{**content,'link':safe_link(content['link'])}}
        if not isinstance(style,dict)or not set(DEFAULT_STYLE)<=set(style)or set(style)-set(DEFAULT_STYLE)-set(EXTRA_STYLE_NUMBERS)-EXTRA_STYLE_FLAGS:
            raise DocumentError('Некорректное оформление свободного элемента.')
        for key in ('color','background','borderColor'):
            if not isinstance(style[key],str)or not(COLOR.fullmatch(style[key])or key=='background'and style[key]=='transparent'):
                raise DocumentError('Выберите цвет в формате #147d73.')
        for key,choices in [('fontWeight',{'400','500','600','700','800'}),('fontFamily',{'inherit','modern','serif','mono'}),('textAlign',{'left','center','right'}),('objectFit',{'cover','contain'})]:
            if not isinstance(style[key],str)or style[key]not in choices:raise DocumentError('Неизвестная настройка элемента.')
        for key,low,high in [('borderWidth',0,20),('radius',0,999),('opacity',0,100),('rotation',-180,180)]:number(style[key],low,high)
        for key,(low,high)in EXTRA_STYLE_NUMBERS.items():
            if key in style:number(style[key],low,high)
        for key in EXTRA_STYLE_FLAGS:
            if key in style and type(style[key])is not bool:raise DocumentError('Некорректное оформление ссылки.')
        if type(item['locked'])is not bool:raise DocumentError('Некорректная блокировка элемента.')
        cleaned.update(style=dict(style),locked=item['locked'])
        for device,width,height in [('desktop',WIDTH,result['height']),('mobile',MOBILE_WIDTH,result['mobileHeight'])]:
            box=item[device]
            if not isinstance(box,dict)or set(box)!={'x','y','width','height','fontSize','hidden'}:raise DocumentError('Некорректные координаты элемента.')
            number(box['width'],8,width);number(box['height'],4,height);number(box['fontSize'],8,240)
            number(box['x'],0,width-box['width']);number(box['y'],0,height-box['height'])
            if type(box['hidden'])is not bool:raise DocumentError('Некорректная видимость элемента.')
            cleaned[device]=dict(box)
        result['elements'].append(cleaned)
    return result


def render(props,*,image_url,href,editor=False):
    parts=[]
    def val(value):return escape(str(value),quote=True)
    for index,item in enumerate(props['elements']):
        style=item['style'];content=item['content'];kind=item['type'];variables=[]
        for device,prefix,width,height in [('desktop','d',WIDTH,props['height']),('mobile','m',MOBILE_WIDTH,props['mobileHeight'])]:
            box=item[device]
            variables.extend([f'--{prefix}-x:{box["x"]/width*100:.6f}%',f'--{prefix}-y:{box["y"]/height*100:.6f}%',
                f'--{prefix}-w:{box["width"]/width*100:.6f}%',f'--{prefix}-h:{box["height"]/height*100:.6f}%',
                f'--{prefix}-fs:{box["fontSize"]/width*100:.6f}cqw',f'--{prefix}-display:{"none"if box["hidden"]else"flex"}'])
        font={'inherit':'var(--bb-font)','modern':'system-ui,sans-serif','serif':'Georgia,serif','mono':'ui-monospace,monospace'}[style['fontFamily']]
        variables.extend([f'color:{style["color"]}',f'background:{style["background"]}',f'border:{style["borderWidth"]}px solid {style["borderColor"]}',
            f'border-radius:{style["radius"]}px',f'font-family:{font}',f'font-weight:{style["fontWeight"]}',f'text-align:{style["textAlign"]}',
            f'opacity:{style["opacity"]/100}',f'transform:rotate({style["rotation"]}deg)',f'z-index:{index+1}',f'--free-fit:{style["objectFit"]}'])
        if 'lineHeight'in style:variables.append(f'line-height:{style["lineHeight"]/1000}')
        if 'letterSpacing'in style:variables.append(f'letter-spacing:{style["letterSpacing"]/1000}em')
        for device,prefix,width in [('desktop','d',WIDTH),('mobile','m',MOBILE_WIDTH)]:
            for axis in ('X','Y'):
                key=('mobilePadding'+axis)if device=='mobile'else('padding'+axis)
                if key in style:variables.append(f'--{prefix}-p{axis.lower()}:{style[key]/width*100:.6f}cqw')
        marker=f' data-free-id="{item["id"]}" data-free-type="{kind}" data-free-d-hidden="{str(item["desktop"]["hidden"]).lower()}" data-free-m-hidden="{str(item["mobile"]["hidden"]).lower()}"'if editor else ''
        text=escape(content['text']).replace('\n','<br>')
        if kind=='image':
            body=f'<img src="{val(image_url(content["image"]))}" alt="{val(content["alt"])}" draggable="false">'if content['image']else'<span class="bb-free-placeholder" aria-hidden="true">＋ Изображение</span>'
        elif kind=='art':body='<div class="bb-art" aria-hidden="true"><div class="bb-art-orbit"></div><div class="bb-art-disc"></div><div class="bb-art-tile">✳</div>'+('<span class="bb-art-note">'+text+'</span>'if text else'')+'</div>'
        elif kind=='faq':body='<details class="bb-faq bb-free-faq" open><summary>'+text+'<span>+</span></summary><p>'+escape(content['alt']).replace('\n','<br>')+'</p></details>'
        elif kind in {'text','button'}:body='<span class="bb-free-copy"'+(' data-free-text="true"'if editor else '')+'>'+text+'</span>'
        else:body=''
        if kind=='button'and style.get('trailingIcon'):body+='<span class="bb-free-icon" aria-hidden="true">↗</span>'
        tag='a'if kind=='button'else'div';link=f' href="{href(content["link"])}"'if tag=='a'else''
        classes=f'bb-free-element bb-free-{kind}'+(' bb-free-has-icon'if kind=='button'and style.get('trailingIcon')else'')+(' bb-free-link'if kind=='button'and style.get('plainLink')else'')
        parts.append(f'<{tag} id="{item["id"]}" class="{classes}"{marker}{link} style="{val(";".join(variables))}">{body}</{tag}>')
    return f'<div class="bb-free-container"><div class="bb-free-canvas" style="--free-height:{props["height"]/WIDTH*100:.6f}cqw;--free-mobile-height:{props["mobileHeight"]/MOBILE_WIDTH*100:.6f}cqw">'+''.join(parts)+'</div></div>'


CSS='''
.bb-kind-canvas.bb-section{padding:0!important}.bb-kind-canvas>.bb-wrap{width:100%;max-width:none}.bb-free-container{width:min(1120px,100%);margin:auto;container-type:inline-size}.bb-free-canvas{position:relative;height:var(--free-height);isolation:isolate;overflow:clip}.bb-free-element{position:absolute;left:var(--d-x);top:var(--d-y);width:var(--d-w);height:var(--d-h);font-size:var(--d-fs);display:var(--d-display);line-height:1.2;box-sizing:border-box;min-width:0;overflow-wrap:anywhere;transform-origin:center}.bb-free-copy{width:100%;white-space:pre-wrap;line-height:inherit}.bb-free-text{align-items:flex-start}.bb-free-button{align-items:center;justify-content:center;text-decoration:none;padding:8px 12px}.bb-free-button:hover{text-decoration:none;filter:brightness(.96)}.bb-free-image{overflow:hidden}.bb-free-image img{display:block;width:100%;height:100%;object-fit:var(--free-fit)}.bb-free-placeholder{display:grid;place-items:center;width:100%;height:100%;background:color-mix(in srgb,var(--bb-accent) 8%,var(--bb-bg));color:var(--bb-accent);font:14px/1.4 system-ui,sans-serif;border:1px dashed currentColor}.bb-free-art>.bb-art{width:100%;height:100%;min-height:0;aspect-ratio:auto}.bb-free-shape,.bb-free-line{pointer-events:none}@media(max-width:650px){.bb-free-canvas{height:var(--free-mobile-height)}.bb-free-element{left:var(--m-x);top:var(--m-y);width:var(--m-w);height:var(--m-h);font-size:var(--m-fs);display:var(--m-display)}}
'''

# Conversion-specific values are optional so old free blocks retain their look.
CSS += '''
.bb-free-faq{width:100%;margin:0}.bb-free-faq p{font-weight:400;font-size:.875em}
.bb-free-button{padding:var(--d-py,8px) var(--d-px,12px)}
.bb-free-has-icon{justify-content:space-between;gap:0}
.bb-free-has-icon .bb-free-copy{width:auto}
.bb-free-icon{font-size:20px;flex:none}
.bb-free-link:hover{text-decoration:underline;filter:none}
@media(max-width:650px){.bb-free-button{padding:var(--m-py,var(--d-py,8px)) var(--m-px,var(--d-px,12px))}}
'''
