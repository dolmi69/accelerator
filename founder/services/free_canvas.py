"""Bounded free-positioned elements shared by editor, AI, preview and hosting."""
import copy
import uuid
from html import escape

WIDTH = 1120
MOBILE_WIDTH = 390
MAX_ELEMENTS = 60
TYPES = {'text', 'image', 'button', 'shape', 'line', 'art'}
DEFAULT_STYLE = {'color':'#122338','background':'transparent','borderColor':'#147d73','borderWidth':0,
    'radius':0,'fontWeight':'500','fontFamily':'inherit','textAlign':'left','opacity':100,'rotation':0,'objectFit':'cover'}
DEFAULT_PROPS = {'height':620,'mobileHeight':740,'elements':[]}


def new_element(kind):
    if kind not in TYPES:
        raise ValueError('Unknown canvas element')
    sizes={'text':(450,120,48),'image':(340,280,20),'button':(220,56,16),'shape':(260,200,20),'line':(320,4,20),'art':(340,340,20)}
    width,height,font=sizes[kind]
    mobile_width=min(width,330)
    style=copy.deepcopy(DEFAULT_STYLE)
    if kind in {'button','shape','line'}:style['background']='#147d73'
    if kind=='button':style.update(color='#ffffff',radius=8,textAlign='center',fontWeight='600')
    if kind=='shape':style['radius']=20
    return {'id':uuid.uuid4().hex,'type':kind,'content':{'text':'Ваш текст' if kind=='text' else 'Открыть' if kind=='button' else '',
        'image':'','alt':'','link':'#'},'desktop':{'x':80,'y':80,'width':width,'height':height,'fontSize':font,'hidden':False},
        'mobile':{'x':30,'y':60,'width':mobile_width,'height':height,'fontSize':min(font,34),'hidden':False},'style':style,'locked':False}


def validate(raw,assets,identifier):
    from founder.services.visual_builder import DocumentError, safe_link, COLOR
    def number(value,low,high):
        if type(value)is not int or not low<=value<=high:
            raise DocumentError(f'Размер или положение свободного элемента: допустимо от {low} до {high}.')
        return value
    if not isinstance(raw,dict)or set(raw)!={'height','mobileHeight','elements'}:
        raise DocumentError('Некорректный свободный блок.')
    result={'height':number(raw['height'],160,4000),'mobileHeight':number(raw['mobileHeight'],160,6000),'elements':[]}
    if not isinstance(raw['elements'],list)or len(raw['elements'])>MAX_ELEMENTS:
        raise DocumentError('В свободном блоке может быть до 60 элементов.')
    for item in raw['elements']:
        if not isinstance(item,dict)or set(item)!={'id','type','content','desktop','mobile','style','locked'}or not isinstance(item['type'],str)or item['type']not in TYPES:
            raise DocumentError('Неизвестный свободный элемент.')
        content=item['content'];style=item['style']
        if not isinstance(content,dict)or set(content)!={'text','image','alt','link'}:
            raise DocumentError('Некорректное содержимое свободного элемента.')
        if any(not isinstance(content[k],str)or len(content[k])>limit or '\0'in content[k]for k,limit in [('text',5000),('alt',300)]):
            raise DocumentError('Проверьте текст и описание элемента.')
        if not isinstance(content['image'],str)or content['image']and content['image']not in assets:
            raise DocumentError('Изображение недоступно в этом проекте.')
        cleaned={'id':identifier(item['id']),'type':item['type'],'content':{**content,'link':safe_link(content['link'])}}
        if not isinstance(style,dict)or set(style)!=set(DEFAULT_STYLE):raise DocumentError('Некорректное оформление свободного элемента.')
        for key in ('color','background','borderColor'):
            if not isinstance(style[key],str)or not(COLOR.fullmatch(style[key])or key=='background'and style[key]=='transparent'):
                raise DocumentError('Выберите цвет в формате #147d73.')
        for key,choices in [('fontWeight',{'400','500','600','700','800'}),('fontFamily',{'inherit','modern','serif','mono'}),('textAlign',{'left','center','right'}),('objectFit',{'cover','contain'})]:
            if not isinstance(style[key],str)or style[key]not in choices:raise DocumentError('Неизвестная настройка элемента.')
        for key,low,high in [('borderWidth',0,20),('radius',0,999),('opacity',0,100),('rotation',-180,180)]:number(style[key],low,high)
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
        marker=f' data-free-id="{item["id"]}" data-free-type="{kind}" data-free-d-hidden="{str(item["desktop"]["hidden"]).lower()}" data-free-m-hidden="{str(item["mobile"]["hidden"]).lower()}"'if editor else ''
        text=escape(content['text']).replace('\n','<br>')
        if kind=='image':
            body=f'<img src="{val(image_url(content["image"]))}" alt="{val(content["alt"])}" draggable="false">'if content['image']else'<span class="bb-free-placeholder" aria-hidden="true">＋ Изображение</span>'
        elif kind=='art':body='<div class="bb-art" aria-hidden="true"><div class="bb-art-orbit"></div><div class="bb-art-disc"></div><div class="bb-art-tile">✳</div></div>'
        elif kind in {'text','button'}:body=f'<span class="bb-free-copy"'+(' data-free-text="true"'if editor else '')+'>'+text+'</span>'
        else:body=''
        tag='a'if kind=='button'else'div';link=f' href="{href(content["link"])}"'if tag=='a'else''
        parts.append(f'<{tag} id="{item["id"]}" class="bb-free-element bb-free-{kind}"{marker}{link} style="{val(";".join(variables))}">{body}</{tag}>')
    return f'<div class="bb-free-container"><div class="bb-free-canvas" style="--free-height:{props["height"]/WIDTH*100:.6f}cqw;--free-mobile-height:{props["mobileHeight"]/MOBILE_WIDTH*100:.6f}cqw">'+''.join(parts)+'</div></div>'


CSS='''
.bb-kind-canvas.bb-section{padding:0!important}.bb-kind-canvas>.bb-wrap{width:100%;max-width:none}.bb-free-container{width:min(1120px,100%);margin:auto;container-type:inline-size}.bb-free-canvas{position:relative;height:var(--free-height);isolation:isolate;overflow:clip}.bb-free-element{position:absolute;left:var(--d-x);top:var(--d-y);width:var(--d-w);height:var(--d-h);font-size:var(--d-fs);display:var(--d-display);line-height:1.2;box-sizing:border-box;min-width:0;overflow-wrap:anywhere;transform-origin:center}.bb-free-copy{width:100%;white-space:pre-wrap;line-height:inherit}.bb-free-text{align-items:flex-start}.bb-free-button{align-items:center;justify-content:center;text-decoration:none;padding:8px 12px}.bb-free-button:hover{text-decoration:none;filter:brightness(.96)}.bb-free-image{overflow:hidden}.bb-free-image img{display:block;width:100%;height:100%;object-fit:var(--free-fit)}.bb-free-placeholder{display:grid;place-items:center;width:100%;height:100%;background:color-mix(in srgb,var(--bb-accent) 8%,var(--bb-bg));color:var(--bb-accent);font:14px/1.4 system-ui,sans-serif;border:1px dashed currentColor}.bb-free-art>.bb-art{width:100%;height:100%;min-height:0;aspect-ratio:auto}.bb-free-shape,.bb-free-line{pointer-events:none}@media(max-width:650px){.bb-free-canvas{height:var(--free-mobile-height)}.bb-free-element{left:var(--m-x);top:var(--m-y);width:var(--m-w);height:var(--m-h);font-size:var(--m-fs);display:var(--m-display)}}
'''
