/* Read only a user-initiated paste. Clipboard HTML is never inserted into a site. */
(() => {
  'use strict';
  const MAX_TEXT=5000,MAX_IMAGE=5*1024*1024;
  function link(value){
    if(typeof value!=='string')return null;
    value=value.trim();if(!value||value.length>1000||/[\x00-\x20\\]/.test(value))return null;
    if(value.startsWith('#')||value.startsWith('/')&&!value.startsWith('//'))return {url:value,label:value};
    if(/^www\./i.test(value))value='https://'+value;
    try{const url=new URL(value);
      if(url.protocol==='https:'&&url.hostname&&!url.username&&!url.password)return {url:url.href,label:url.hostname.replace(/^www\./,'')};
      if(['mailto:','tel:'].includes(url.protocol)&&url.pathname&&!url.host)return {url:value,label:url.pathname};
    }catch(_){}return null;
  }
  function classify({text='',uri='',images=[],htmlLink=null,htmlText=''}={}){
    if(images.length){
      if(images.length>10)return {error:'За одну вставку можно добавить до 10 изображений.'};
      if(images.some(f=>f.size>MAX_IMAGE))return {error:'Изображение должно быть не больше 5 МБ.'};
      return {type:'image',files:images};
    }
    const original=String(text||'').replace(/\r\n?/g,'\n'),plain=original.trim();
    const uris=String(uri||'').split(/\r?\n/).filter(s=>s.trim()&&!s.startsWith('#'));
    const address=link(plain)||(uris.length===1&&(!plain||plain===uris[0].trim())?link(uris[0]):null);
    if(address)return {type:'button',url:address.url,text:address.label.slice(0,180)};
    if(htmlLink&&(!plain||plain===htmlLink.text?.trim())){
      const address=link(htmlLink.url);if(address)return {type:'button',url:address.url,text:(htmlLink.text?.trim()||address.label).slice(0,180)};
    }
    const content=(plain?original:String(htmlText||'')).trim();
    if(!content)return null;
    if(content.length>MAX_TEXT)return {error:'В один текстовый элемент можно вставить до 5000 символов. Разделите текст на части.'};
    if(content.includes('\0'))return {error:'Текст содержит неподдерживаемые символы.'};
    return {type:'text',text:content};
  }
  function editing(target){return !!target?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"]');}
  function inspect(data,document){
    if(!data)return null;
    const images=[...data.items||[]].filter(i=>i.kind==='file'&&/^image\/(png|jpeg|webp)$/.test(i.type)).map(i=>i.getAsFile()).filter(Boolean);
    if(!images.length)images.push(...[...data.files||[]].filter(f=>/^image\/(png|jpeg|webp)$/.test(f.type)));
    const text=data.getData('text/plain'),uri=data.getData('text/uri-list');
    // Real image bytes take priority over accompanying browser descriptions.
    if(images.length)return classify({text,uri,images});
    if([...data.items||[]].some(i=>i.kind==='file'&&i.type.startsWith('image/'))||[...data.files||[]].some(f=>f.type.startsWith('image/')))return {error:'Для вставки картинки используйте PNG, JPG или WebP.'};
    let htmlLink=null,htmlText='';const html=data.getData('text/html');
    if(html&&html.length<=8_000_000){
      // A template stays inert: scripts, event handlers, frames and remote images
      // are never connected to the document or fetched to generate a preview.
      const template=document.createElement('template');template.innerHTML=html;
      const content=template.content;content.querySelectorAll('script,style,iframe,object,embed,template').forEach(n=>n.remove());
      const anchors=[...content.querySelectorAll('a[href]')],image=content.querySelector('img');
      const encoded=image?.getAttribute('src')?.match(/^data:(image\/(?:png|jpeg|webp));base64,([a-zA-Z0-9+/=\s]+)$/);
      if(encoded&&encoded[2].length<MAX_IMAGE*1.38){
        try{const binary=atob(encoded[2].replace(/\s/g,'')),bytes=Uint8Array.from(binary,c=>c.charCodeAt(0));return classify({images:[new File([bytes],'clipboard.'+(encoded[1]==='image/jpeg'?'jpg':encoded[1].split('/')[1]),{type:encoded[1]})]});}catch(_){}
      }
      content.querySelectorAll('br').forEach(n=>n.replaceWith(document.createTextNode('\n')));
      content.querySelectorAll('p,div,li,h1,h2,h3,h4,h5,h6,tr').forEach(n=>n.append(document.createTextNode('\n')));
      htmlText=content.textContent||'';
      if(anchors.length===1&&htmlText.trim()===anchors[0].textContent.trim())htmlLink={url:anchors[0].getAttribute('href'),text:anchors[0].textContent};
    }
    return classify({text,uri,htmlLink,htmlText});
  }
  const api={link,classify,inspect,editing};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  if(typeof window!=='undefined')window.BrunoBuilderClipboard=api;
})();
