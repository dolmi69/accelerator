/* Version controls live in one keyboard-accessible modal beside the preview. */
(() => {
  const dialog=document.getElementById('lab-version-settings');
  const trigger=document.querySelector('[data-lab-settings-open]');
  if(!dialog||!trigger)return;
  trigger.addEventListener('click',()=>{if(!dialog.inert&&!dialog.open)dialog.showModal();});
  dialog.querySelector('[data-lab-settings-close]').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('keydown',event=>{if(event.key==='Escape')event.stopPropagation();});
  dialog.addEventListener('click',event=>{
    if(event.target!==dialog)return;
    const rect=dialog.getBoundingClientRect();
    if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close();
  });
  document.addEventListener('lab:job-state',event=>{trigger.disabled=Boolean(event.detail?.active&&event.detail.mode==='edit');});
})();
