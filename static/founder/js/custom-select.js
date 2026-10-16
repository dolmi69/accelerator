// Keep the original form control for submission, validation and existing change handlers.
// Enhance only this document: generated sites and Django admin have their own UI.
(() => {
  const controls = new Map();
  let sequence = 0;
  let opened = null;
  let search = '';
  let searchTimer;

  const close = (control, restoreFocus = false) => {
    if (!control || opened !== control) return;
    if (control.menu.matches(':popover-open')) control.menu.hidePopover();
    control.menu.hidden = true;
    control.trigger.setAttribute('aria-expanded', 'false');
    control.trigger.removeAttribute('aria-activedescendant');
    opened = null;
    search = '';
    if (restoreFocus) control.trigger.focus();
  };

  function position(control) {
    const rect = control.trigger.getBoundingClientRect();
    const viewport = window.visualViewport;
    const width = viewport?.width || innerWidth;
    const height = viewport?.height || innerHeight;
    const left = viewport?.offsetLeft || 0;
    const top = viewport?.offsetTop || 0;
    const below = top + height - rect.bottom - 12;
    const above = rect.top - top - 12;
    const upwards = below < 180 && above > below;
    const available = Math.max(50, upwards ? above : below);
    control.menu.style.width = `${Math.min(Math.max(rect.width, 220), width - 24)}px`;
    control.menu.style.maxHeight = `${Math.min(340, available)}px`;
    const menuWidth = control.menu.getBoundingClientRect().width;
    control.menu.style.left = `${Math.max(left + 12, Math.min(rect.left, left + width - menuWidth - 12))}px`;
    const menuHeight = control.menu.getBoundingClientRect().height;
    control.menu.style.top = `${upwards ? rect.top - menuHeight - 6 : rect.bottom + 6}px`;
  }

  function activate(control, index, scroll = true) {
    const option = control.options[index];
    if (!option || option.disabled) return;
    control.active = index;
    control.options.forEach((item, i) => item.node.classList.toggle('is-active', i === index));
    control.trigger.setAttribute('aria-activedescendant', option.node.id);
    if (scroll) option.node.scrollIntoView({block: 'nearest'});
  }

  function refresh(control) {
    const { select, trigger, label } = control;
    label.textContent = Array.from(select.selectedOptions, option => option.label).join(', ') || 'Выберите';
    trigger.disabled = select.matches(':disabled');
    trigger.setAttribute('aria-required', String(select.required));
    const invalid = (control.validationShown && !select.validity.valid) ? 'true' : select.getAttribute('aria-invalid');
    if (invalid) trigger.setAttribute('aria-invalid', invalid);
    else trigger.removeAttribute('aria-invalid');
    if (select.validity.valid) trigger.removeAttribute('title');
    if (control.error) {
      control.error.hidden = !control.validationShown || select.validity.valid;
      control.error.textContent = control.error.hidden ? '' : select.validationMessage;
    }
    control.options.forEach(item => item.node.setAttribute('aria-selected', String(item.option.selected)));
    if (trigger.disabled) close(control);
  }

  function build(control) {
    control.menu.replaceChildren();
    control.options = [];
    Array.from(control.select.children).forEach(child => {
      const group = child.tagName === 'OPTGROUP';
      if (child.hidden) return;
      if (group) {
        const heading = document.createElement('div');
        heading.className = 'custom-select-group';
        heading.setAttribute('role', 'presentation');
        heading.textContent = child.label;
        control.menu.append(heading);
      }
      const options = group ? Array.from(child.children) : [child];
      options.forEach(option => {
        if (option.tagName !== 'OPTION' || option.hidden) return;
        const node = document.createElement('div');
        const index = control.options.length;
        const disabled = option.disabled || (group && child.disabled);
        node.id = `${control.menu.id}-${index}`;
        node.className = 'custom-select-option';
        node.setAttribute('role', 'option');
        node.setAttribute('aria-disabled', String(Boolean(disabled)));
        node.textContent = option.label;
        const item = { node, option, disabled: Boolean(disabled) };
        control.options.push(item);
        // Keep focus on the combobox, including when the select is inside a label.
        node.addEventListener('pointerdown', event => event.preventDefault());
        node.addEventListener('click', event => {
          event.preventDefault();
          if (!item.disabled) choose(control, index);
        });
        control.menu.append(node);
      });
    });
    refresh(control);
  }

  function choose(control, index) {
    const item = control.options[index];
    if (!item || item.disabled || control.select.matches(':disabled')) return;
    const before = Array.from(control.select.selectedOptions, option => option.index).join(',');
    if (control.select.multiple) item.option.selected = !item.option.selected;
    else control.select.selectedIndex = item.option.index;
    refresh(control);
    const after = Array.from(control.select.selectedOptions, option => option.index).join(',');
    if (!control.select.multiple) close(control, true);
    else activate(control, index, false);
    if (before !== after) {
      control.select.dispatchEvent(new Event('input', { bubbles: true }));
      control.select.dispatchEvent(new Event('change', { bubbles: true }));
      refresh(control);
    }
  }

  function open(control) {
    if (control.select.matches(':disabled')) return;
    close(opened);
    build(control);
    opened = control;
    control.menu.hidden = false;
    control.trigger.setAttribute('aria-expanded', 'true');
    if (typeof control.menu.showPopover === 'function') control.menu.showPopover();
    position(control);
    let index = control.options.findIndex(item => item.option.selected && !item.disabled);
    if (index < 0) index = control.options.findIndex(item => !item.disabled);
    activate(control, index);
  }

  function enhance(select) {
    if (controls.has(select)) return;
    const wrapper = document.createElement('span');
    wrapper.className = 'custom-select';
    // Preserve inline filter/assignment rows instead of pushing their submit button down.
    const layout = getComputedStyle(select.parentElement);
    if (layout.display.includes('flex') && layout.flexDirection.startsWith('row')) {
      const original = getComputedStyle(select);
      wrapper.style.width = 'auto';
      wrapper.style.flex = original.flex;
      wrapper.style.minWidth = original.minWidth;
    }
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'custom-select-trigger';
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-autocomplete', 'none');
    const label = document.createElement('span');
    label.className = 'custom-select-value';
    const chevron = document.createElement('span');
    chevron.className = 'custom-select-chevron';
    chevron.setAttribute('aria-hidden', 'true');
    trigger.append(label, chevron);
    const menu = document.createElement('div');
    menu.id = `custom-select-menu-${++sequence}`;
    menu.className = 'custom-select-menu';
    menu.setAttribute('role', 'listbox');
    menu.setAttribute('popover', 'manual');
    menu.hidden = true;
    trigger.setAttribute('aria-controls', menu.id);
    if (select.multiple) menu.setAttribute('aria-multiselectable', 'true');
    const labelledBy = select.getAttribute('aria-labelledby');
    const labels = Array.from(select.labels || [], element => {
      const clone = element.cloneNode(true);
      clone.querySelectorAll('select, input, button, textarea').forEach(node => node.remove());
      return clone.textContent.trim();
    }).filter(Boolean).join(' ');
    if (labelledBy) trigger.setAttribute('aria-labelledby', labelledBy);
    else trigger.setAttribute('aria-label', select.getAttribute('aria-label') || labels || select.name || 'Выбор');
    menu.setAttribute('aria-label', select.getAttribute('aria-label') || labels || 'Варианты');
    if (select.hasAttribute('aria-describedby')) trigger.setAttribute('aria-describedby', select.getAttribute('aria-describedby'));
    select.before(wrapper);
    const error = document.createElement('span');
    error.id = `${menu.id}-error`;
    error.className = 'custom-select-error';
    error.setAttribute('role', 'alert');
    error.hidden = true;
    trigger.setAttribute('aria-describedby', [trigger.getAttribute('aria-describedby'), error.id].filter(Boolean).join(' '));
    wrapper.append(select, trigger, menu, error);
    select.classList.add('custom-select-native');
    select.tabIndex = -1;
    select.setAttribute('aria-hidden', 'true');
    select.labels?.forEach(element => element.addEventListener('click', event => {
      if (event.target.closest('button, a, input, select, textarea, [role="option"]')) return;
      event.preventDefault();
      trigger.focus();
    }));
    const control = { select, wrapper, trigger, label, menu, error, validationShown: false, options: [], active: -1 };
    controls.set(select, control);
    trigger.addEventListener('click', () => opened === control ? close(control) : open(control));
    trigger.addEventListener('keydown', event => {
      if (event.key === 'Tab') { close(control); return; }
      if (event.key === 'Escape') { if (opened === control) { event.preventDefault(); close(control, true); } return; }
      if (event.key === 'Enter' || event.key === ' ') {
        if (search && event.key === ' ') { search += ' '; event.preventDefault(); return; }
        event.preventDefault();
        if (opened === control) choose(control, control.active);
        else open(control);
        return;
      }
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        if (opened !== control) { open(control); if (event.key.startsWith('Arrow')) return; }
        const enabled = control.options.map((item, i) => item.disabled ? -1 : i).filter(i => i >= 0);
        let offset = enabled.indexOf(control.active);
        if (event.key === 'Home') offset = 0;
        else if (event.key === 'End') offset = enabled.length - 1;
        else offset = Math.max(0, Math.min(enabled.length - 1, offset + (event.key === 'ArrowDown' ? 1 : -1)));
        activate(control, enabled[offset]);
        return;
      }
      if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        if (opened !== control) open(control);
        clearTimeout(searchTimer);
        search += event.key.toLocaleLowerCase();
        const index = control.options.findIndex(item => !item.disabled && item.option.label.toLocaleLowerCase().startsWith(search));
        if (index >= 0) activate(control, index);
        searchTimer = setTimeout(() => { search = ''; }, 700);
      }
    });
    select.addEventListener('change', () => refresh(control));
    select.addEventListener('focus', () => trigger.focus());
    select.addEventListener('invalid', event => {
      event.preventDefault();
      control.validationShown = true;
      trigger.title = select.validationMessage;
      // Focus the first invalid select, without jumping between multiple required fields.
      const first = select.form?.querySelector(':invalid');
      if (!first || first === select) { trigger.focus(); open(control); }
      refresh(control);
    });
    build(control);
  }

  function init() {
    document.querySelectorAll('select').forEach(enhance);
    new MutationObserver(records => {
      for (const [select, control] of controls) {
        if (!select.isConnected) { close(control); controls.delete(select); continue; }
        if (records.some(record => record.target === select || select.contains(record.target))) build(control);
        else if (records.some(record => record.type === 'attributes' && record.attributeName === 'disabled' && record.target.contains(select))) refresh(control);
      }
      if (records.some(record => record.type === 'childList' && Array.from(record.addedNodes).some(node => node.nodeType === 1 && (node.matches('select') || node.querySelector('select'))))) {
        document.querySelectorAll('select').forEach(enhance);
      }
    }).observe(document.body, {subtree: true, childList: true, characterData: true, attributes: true,
      attributeFilter: ['disabled', 'selected', 'label', 'hidden', 'required', 'aria-invalid']});
    document.addEventListener('pointerdown', event => {
      if (opened && !opened.wrapper.contains(event.target)) close(opened);
    });
    document.addEventListener('focusin', event => {
      if (opened && !opened.wrapper.contains(event.target)) close(opened);
    });
    document.addEventListener('reset', event => {
      requestAnimationFrame(() => controls.forEach(control => { if (control.select.form === event.target) { close(control); control.validationShown = false; refresh(control); } }));
    });
    document.addEventListener('toggle', () => {
      if (opened && !opened.trigger.getClientRects().length) close(opened);
    }, true);
    window.addEventListener('resize', () => close(opened));
    window.addEventListener('scroll', event => {
      if (opened && !opened.menu.contains(event.target)) close(opened);
    }, true);
    window.addEventListener('pageshow', () => controls.forEach(refresh));
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
