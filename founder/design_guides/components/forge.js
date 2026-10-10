(()=>{'use strict';
const ready=()=>{
 const style=getComputedStyle(document.documentElement);
 const primary=document.querySelector('.forge-button:not(.forge-button--outline):not(.forge-button--quiet)');
 if(primary&&!style.getPropertyValue('--forge-on-accent').trim()){
  const rgb=getComputedStyle(primary).backgroundColor.match(/^rgba?\(([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/);
  if(rgb){const values=rgb.slice(1,4).map(value=>{const x=Number(value)/255;return x<=.04045?x/12.92:Math.pow((x+.055)/1.055,2.4);});const luminance=.2126*values[0]+.7152*values[1]+.0722*values[2];document.documentElement.style.setProperty('--forge-on-accent',luminance>.179?'#111827':'#fff');}
 }
 document.querySelectorAll('[data-forge-collection]').forEach(root=>{
  const form=root.querySelector('[data-forge-filter]');if(!(form instanceof HTMLFormElement))return;
  const items=[...root.querySelectorAll('[data-forge-item]')];
  const update=()=>{const query=(form.elements.namedItem('q')?.value||'').trim().toLocaleLowerCase();const category=form.elements.namedItem('category')?.value||'';let visible=0;
   items.forEach(item=>{item.hidden=!!((query&&!item.textContent.toLocaleLowerCase().includes(query))||(category&&item.dataset.category!==category));if(!item.hidden)visible++;});
   const count=root.querySelector('[data-forge-count]');if(count)count.textContent='Найдено: '+visible;
   const empty=root.querySelector('[data-forge-empty]');if(empty)empty.hidden=visible>0;};
  form.addEventListener('submit',event=>{event.preventDefault();update();});form.addEventListener('input',update);form.addEventListener('change',update);update();
 });
 document.querySelectorAll('[data-forge-tabs]').forEach(root=>{
  const tabs=[...root.querySelectorAll('[data-forge-tab]')],panels=[...root.querySelectorAll('[role=tabpanel]')];
  const select=(tab,focus=false)=>{const id=tab.getAttribute('aria-controls');if(!panels.some(panel=>panel.id===id))return;
   tabs.forEach(button=>{const active=button===tab;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;});panels.forEach(panel=>{panel.hidden=panel.id!==id;});if(focus)tab.focus();};
  tabs.forEach((tab,index)=>{tab.addEventListener('click',()=>select(tab));tab.addEventListener('keydown',event=>{let next;
   if(event.key==='ArrowRight')next=(index+1)%tabs.length;else if(event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=tabs.length-1;else return;
   event.preventDefault();select(tabs[next],true);});});
  const active=tabs.find(tab=>tab.getAttribute('aria-selected')==='true')||tabs[0];if(active)select(active);
 });
};if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',ready,{once:true});else ready();})();
