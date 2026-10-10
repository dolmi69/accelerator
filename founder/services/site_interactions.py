"""Small local fixes for common interactions blocked by the preview sandbox.

This does not enable browser modals or weaken the sandbox. Feedback is rendered
as text inside the prototype; no model text is interpolated into this script.
"""
import re

PREVIEW_INTERACTIONS = '''<script data-lab-interactions="1">
(()=>{
  const notify=message=>{
    if(!document.body){document.addEventListener('DOMContentLoaded',()=>notify(message),{once:true});return;}
    let box=document.getElementById('lab-demo-notice');
    if(!box){
      box=document.createElement('div');box.id='lab-demo-notice';
      box.setAttribute('role','status');box.setAttribute('aria-live','polite');
      box.style.cssText='position:fixed;bottom:20px;left:20px;right:20px;max-width:560px;margin:auto;z-index:2147483647;padding:18px;border:1px solid #d7ddd9;border-radius:14px;background:#fff;color:#25332f;box-shadow:0 10px 40px #0002;display:flex;gap:16px;align-items:center;font:16px/1.5 system-ui,sans-serif';
      const text=document.createElement('span');text.style.flex='1';box.append(text);
      const close=document.createElement('button');close.type='button';close.textContent='×';
      close.setAttribute('aria-label','Закрыть сообщение');
      close.style.cssText='border:0;background:transparent;color:inherit;font:24px system-ui;cursor:pointer;padding:8px';
      close.addEventListener('click',()=>box.remove());box.append(close);document.body.append(box);
    }
    box.firstElementChild.textContent='Демонстрация: '+String(message??'').slice(0,800);
  };
  window.alert=notify;
  document.addEventListener('click',event=>{
    const link=event.target instanceof Element?event.target.closest('a[href="#"]'):null;
    if(!link)return;
    event.preventDefault();event.stopImmediatePropagation();
    if(link.closest('header')){
      const home=document.getElementById('home');
      if(home)home.scrollIntoView();else window.scrollTo(0,0);
    }else{
      notify('Раздел «'+link.textContent.trim().slice(0,100)+'» представлен в макете. Доступные функции открываются через меню сайта.');
    }
  },true);
})();
</script>'''


def enhance_interactions(html):
    """Keep ordinary pages untouched; repair only alert and empty-anchor demos."""
    if 'data-lab-interactions="1"' in html:
        return html
    if not re.search(r'\balert\s*\(|href\s*=\s*[\"\']#[\"\']', html, re.I):
        return html
    return re.sub(r'(<head\b[^>]*>)', lambda match: match[0] + PREVIEW_INTERACTIONS,
                  html, count=1, flags=re.I)
