/* Built-in sections become editable elements before they reach the desk. */
(() => {
  'use strict';
  const clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
  const selector='h1,h2,h3,p,.bb-brand,.bb-button,.bb-nav-links a,.bb-footer-links a,.bb-photo,.bb-art,.bb-card,.bb-card-index,.bb-section-no,.bb-quote-mark,.bb-hero-rule,.bb-hero-rule>span,.bb-story,.bb-footer,.bb-faq,.bb-spark';
  async function probe(html,width){
    const iframe=document.createElement('iframe');iframe.setAttribute('sandbox','allow-same-origin');iframe.setAttribute('scrolling','no');iframe.setAttribute('aria-hidden','true');iframe.tabIndex=-1;
    iframe.style.cssText=`position:fixed;left:-12000px;top:0;width:${width}px;height:6000px;visibility:hidden;pointer-events:none;border:0`;
    try{
      await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Не удалось подготовить шаблон. Повторите попытку.')),7000);iframe.onload=()=>{clearTimeout(timer);resolve();};iframe.srcdoc=html;document.body.append(iframe);});
      iframe.contentDocument.querySelectorAll('.bb-faq').forEach(n=>n.open=true);
      return iframe;
    }catch(error){iframe.remove();throw error;}
  }
  function measure(section,width,limit){
    const document=section.ownerDocument,view=document.defaultView,bounds=section.getBoundingClientRect(),ratio=width/bounds.width;
    const height=Math.max(40,Math.ceil(bounds.height*ratio));
    if(height>limit)throw new Error('Секция слишком высокая для свободного размещения. Разделите её на части.');
    const bodyFont=view.getComputedStyle(document.body).fontFamily;
    const canvas=document.createElement('canvas');canvas.width=canvas.height=1;const context=canvas.getContext('2d');
    const rgba=value=>{context.clearRect(0,0,1,1);context.fillStyle=value;context.fillRect(0,0,1,1);return [...context.getImageData(0,0,1,1).data];};
    let base=rgba(view.getComputedStyle(section).backgroundColor);if(!base[3])base=rgba(view.getComputedStyle(document.body).backgroundColor);
    const hex=(value,fallback,blend=false)=>{const c=rgba(value);if(!c[3])return fallback;const alpha=blend?c[3]/255:1;return '#'+c.slice(0,3).map((n,i)=>Math.round(n*alpha+base[i]*(1-alpha)).toString(16).padStart(2,'0')).join('');};
    const records=new Map();
    [...section.querySelectorAll(selector)].filter(n=>!n.closest('.vb-block-tools')&&!n.parentElement.closest('.bb-art,.bb-faq')).forEach((node,index)=>{
      let rect=node.getBoundingClientRect();if(rect.width<1||rect.height<1)return;
      const css=view.getComputedStyle(node);
      let kind=node.matches('img')?'image':node.matches('.bb-button,a')?'button':node.matches('.bb-art')?'art':node.matches('.bb-faq')?'faq':node.matches('.bb-card')?'shape':node.matches('.bb-story,.bb-footer,.bb-hero-rule>span')?'line':'text';
      let text=node.innerText.replace(/↗\s*$/,'').trim(),alt='';
      if(node.matches('.bb-story,.bb-footer'))rect={...rect.toJSON(),height:Math.max(1,parseFloat(css.borderTopWidth)||1)};
      if(node.matches('.bb-hero-rule')){
        const textNode=[...node.childNodes].find(n=>n.nodeType===3&&n.textContent.trim());if(!textNode)return;
        const range=document.createRange();range.selectNode(textNode);const textRect=range.getBoundingClientRect();rect={...rect.toJSON(),left:textRect.left,width:textRect.width};text=textNode.textContent.trim();
      }
      if(kind==='image')alt=node.getAttribute('alt')||'';
      if(kind==='art')text=node.querySelector('.bb-art-note')?.textContent||'';
      if(kind==='shape'||kind==='line')text='';
      if(kind==='faq'){text=node.querySelector('summary')?.childNodes[0]?.textContent.trim()||'';alt=node.querySelector('p')?.innerText||'';}
      const fontSize=Number.parseFloat(css.fontSize),w=Math.min(width,Math.max(8,Math.ceil(rect.width*ratio))),h=Math.min(height,Math.max(4,Math.ceil(rect.height*ratio)));
      const box={x:Math.round(clamp((rect.left-bounds.left)*ratio,0,width-w)),y:Math.round(clamp((rect.top-bounds.top)*ratio,0,height-h)),width:w,height:h,fontSize:clamp(Math.round(fontSize*ratio),8,240),hidden:false};
      let opacity=1;for(let parent=node;parent&&parent!==section;parent=parent.parentElement)opacity*=Number(view.getComputedStyle(parent).opacity);
      const family=css.fontFamily===bodyFont?'inherit':/Georgia|serif/i.test(css.fontFamily)&&!/sans-serif/i.test(css.fontFamily)?'serif':/monospace/i.test(css.fontFamily)?'mono':'modern';
      const style={fontFamily:family,color:hex(css.color,'#122338'),background:hex(css.backgroundColor,'transparent',true),borderColor:hex(css.borderTopColor,'#147d73',true),borderWidth:Math.min(20,Math.round(parseFloat(css.borderTopWidth)||0)),radius:Math.min(999,Math.round(parseFloat(css.borderRadius)||0)),fontWeight:['400','500','600','700','800'].includes(css.fontWeight)?css.fontWeight:'500',textAlign:css.textAlign==='end'?'right':['left','center','right'].includes(css.textAlign)?css.textAlign:'left',opacity:Math.round(opacity*100),rotation:0,objectFit:css.objectFit==='contain'?'contain':'cover',lineHeight:clamp(Math.round((parseFloat(css.lineHeight)/fontSize||1.2)*1000),500,4000),letterSpacing:clamp(Math.round((parseFloat(css.letterSpacing)||0)/fontSize*1000),-200,500)};
      if(kind==='button')Object.assign(style,{paddingX:Math.round(parseFloat(css.paddingLeft)||0),paddingY:Math.round(parseFloat(css.paddingTop)||0),trailingIcon:node.matches('.bb-button')&&!!node.querySelector('span[aria-hidden]'),plainLink:!node.matches('.bb-button')});
      if(kind==='line'){style.background=hex(node.matches('.bb-story,.bb-footer')?css.borderTopColor:css.backgroundColor,'#147d73',true);style.borderWidth=0;}
      if(kind==='art'||kind==='faq'){style.background='transparent';style.borderWidth=0;}
      records.set(index,{kind,box,style,text,alt,link:node.getAttribute('href')||'#',src:node.getAttribute('src'),locked:kind==='shape'||kind==='line'});
    });
    return {height,records};
  }
  async function prepare(html,blocks,{uid,assets}){
    const frames=[];
    try{
      for(const width of [1120,390])frames.push(await probe(html,width));
      return blocks.map(b=>{
        if(b.type==='canvas')return b;
        const d=measure(frames[0].contentDocument.querySelector(`[data-block-id="${b.id}"]`),1120,4000),m=measure(frames[1].contentDocument.querySelector(`[data-block-id="${b.id}"]`),390,6000);
        const elements=[],indices=new Set([...d.records.keys(),...m.records.keys()]);
        if(indices.size>60)throw new Error('В шаблоне слишком много элементов. Разделите секцию на части.');
        for(const index of indices){
          const desktop=d.records.get(index),mobile=m.records.get(index),r=desktop||mobile;
          const style={...r.style};if(r.kind==='button')Object.assign(style,{mobilePaddingX:mobile?.style.paddingX||0,mobilePaddingY:mobile?.style.paddingY||0});
          const content={text:r.text,alt:r.alt,image:'',link:r.link};
          if(r.kind==='image'){const asset=assets.find(a=>r.src===a.url);if(!asset)throw new Error('Изображение шаблона не найдено в библиотеке проекта.');content.image=asset.id;content.alt=r.alt||'';}
          elements.push({id:uid(),type:r.kind,content,desktop:desktop?.box||{x:0,y:0,width:8,height:4,fontSize:16,hidden:true},mobile:mobile?.box||{x:0,y:0,width:8,height:4,fontSize:16,hidden:true},style,locked:r.locked});
        }
        return {...b,type:'canvas',props:{height:d.height,mobileHeight:m.height,elements,template:b.type}};
      });
    }finally{frames.forEach(f=>f.remove());}
  }
  window.BrunoBuilderTemplates={prepare};
})();
