'use strict';
const definition = JSON.parse(document.getElementById('definition').textContent);
const main = document.querySelector('main');
const root = main.dataset.root;
const workspace = document.getElementById('workspace');
const nav = document.getElementById('tables');
const session = document.getElementById('session');
const notice = document.getElementById('notice');
let me;
let generation = 0;

function el(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
}
function action(text, run) {
    const button = el('button', text);
    button.type = 'button';
    button.onclick = () => guarded(run, button);
    return button;
}
async function guarded(run, button) {
    notice.textContent = '';
    if (button) button.disabled = true;
    try { await run(); } catch (e) { notice.textContent = e.message; }
    finally { if (button) button.disabled = false; }
}
async function api(path, method = 'GET', data) {
    const headers = {'X-CSRFToken': main.dataset.csrf};
    let body;
    if (data instanceof File) body = data;
    else if (data !== undefined) {
        headers['Content-Type'] = 'application/json';
        body = JSON.stringify(data);
    }
    const res = await fetch(root + 'api/' + path, {method, headers, body, credentials:'same-origin'});
    const result = await res.json().catch(() => ({}));
    if (!res.ok) {
        const err = new Error(result.error || 'Не удалось выполнить запрос.');
        err.status = res.status;
        throw err;
    }
    return result;
}
function control(parent, title, node) {
    const label = el('label', title);
    label.append(node);
    parent.append(label);
    return node;
}
function login() {
    generation++;
    nav.replaceChildren();
    session.replaceChildren();
    const box = el('section'), form = el('form');
    box.append(el('h2','Вход на сайт'), form);
    const username = control(form,'Логин',el('input'));
    const password = control(form,'Пароль',el('input'));
    username.required = password.required = true;
    username.autocomplete = 'username';
    password.type = 'password';
    password.autocomplete = 'current-password';
    const submit = el('button','Войти'); submit.type = 'submit';
    const credentials = () => ({username:username.value, password:password.value});
    form.append(submit, action('Создать аккаунт', async () => {
        await api('register','POST',credentials()); await start();
    }));
    form.onsubmit = e => { e.preventDefault(); guarded(async () => {
        await api('login','POST',credentials()); await start();
    }, submit); };
    workspace.replaceChildren(box);
}
function option(select, value, title) {
    const o = el('option', title); o.value = value; select.append(o);
}
function fieldsFor(table) {
    return (table.ui?.order || Object.keys(table.fields)).map(name => [name,table.fields[name]]);
}
function summary(row, table) {
    const key = fieldsFor(table).find(([name, field]) => field.type === 'text' && row.values[name]);
    const value = key && row.values[key[0]];
    return value || '#' + row.id;
}
async function editor(key, row, container, refresh) {
    const table = definition.tables[key], previous = row ? row.values : {};
    const form = el('form'), readers = [];
    for (const [name, field] of fieldsFor(table)) {
        if (field.write_roles && !field.write_roles.includes(me.role)) continue;
        const choices = ['choice','ref','member'].includes(field.type);
        const input = control(form, field.label, el(choices ? 'select' : 'input'));
        const old = previous[name];
        if (choices) {
            option(input, '', 'Выберите…');
            if (field.type === 'choice') field.options.forEach(v => option(input, v, v));
            if (field.type === 'member' || field.type === 'ref') {
                let after = '';
                const loadMore = async () => {
                    const members = field.type === 'member';
                    const route = members ? 'members' : 'records/' + field.target;
                    const result = await api(route + (after ? '?after=' + encodeURIComponent(after) : ''));
                    const page = members ? result.members : result.records;
                    for (const item of page) {
                        const title = members ? item.username : summary(item, definition.tables[field.target]);
                        const existing = Array.from(input.options).find(o => o.value === String(item.id));
                        if (existing) existing.textContent = title;
                        else option(input, item.id, title);
                    }
                    if (page.length) after = members ? page.at(-1).username : page.at(-1).id;
                    more.hidden = page.length < 100;
                };
                const more = action('Загрузить ещё варианты',loadMore);
                form.append(more);
                await loadMore();
            }
            if (old != null && !Array.from(input.options).some(o => o.value === String(old))) option(input, old, row.display?.[name] || old);
            input.value = old ?? '';
        } else {
            const types = {file:'file', boolean:'checkbox', datetime:'datetime-local', date:'date', number:'number', text:'text'};
            input.type = types[field.type];
            if (field.type === 'number') input.step = 'any';
            if (field.type === 'file') input.accept = 'image/jpeg,image/png,image/webp';
            else if (field.type === 'boolean') input.checked = !!old;
            else if (field.type === 'datetime' && old) {
                const d = new Date(old);
                input.value = new Date(d.getTime() - d.getTimezoneOffset()*60000).toISOString().slice(0,16);
            } else input.value = old ?? '';
        }
        input.required = !!field.required && field.type !== 'boolean' && !(field.type === 'file' && old);
        let changed = !row, uploaded;
        input.onchange = () => { changed = true; uploaded = null; };
        readers.push(async changes => {
            if (!changed) return;
            if (field.type === 'file') {
                const file = input.files[0];
                if (file) {
                    if (!uploaded) uploaded = await api('files?table=' + key + '&name=' + encodeURIComponent(file.name), 'POST', file);
                    changes[name] = uploaded.id;
                }
            } else if (field.type === 'boolean') changes[name] = input.checked;
            else if (!input.value) changes[name] = null;
            else if (field.type === 'number' || field.type === 'ref') changes[name] = Number(input.value);
            else if (field.type === 'datetime') changes[name] = new Date(input.value).toISOString();
            else changes[name] = input.value;
        });
    }
    const save = el('button', table.ui?.save || (table.booking && !row ? 'Забронировать' : 'Сохранить')); save.type = 'submit';
    form.append(save, action('Отмена', () => container.replaceChildren()));
    form.onsubmit = e => { e.preventDefault(); guarded(async () => {
        const changes = {};
        for (const read of readers) await read(changes);
        await api('records/' + key + (row ? '/' + row.id : ''), row ? 'PATCH' : 'POST', changes);
        await refresh();
    }, save); };
    container.replaceChildren(form);
}
async function openTable(key) {
    const current = ++generation, table = definition.tables[key], rights = table.access[me.role];
    const box = el('section'), edit = el('div'), list = el('div');
    box.append(el('h2', table.label));
    const refresh = () => openTable(key);
    if (rights.create) box.append(action(table.ui?.add || 'Добавить', () => editor(key, null, edit, refresh)));
    box.append(edit, list); workspace.replaceChildren(box);
    for (const b of nav.children) b.setAttribute('aria-current', String(b.dataset.key === key));
    if (rights.read === 'none') return;
    let after = 0;
    async function next() {
        const {records} = await api('records/' + key + '?after=' + after);
        if (current !== generation) return;
        if (!after && !records.length) list.append(el('p',table.ui?.empty || 'Записей пока нет.'));
        for (const row of records) {
            const card = el('article'), dl = el('dl');
            for (const [name, field] of fieldsFor(table)) {
                const val = row.values[name], dd = el('dd');
                if (field.type === 'file' && val) {
                    const img = el('img'); img.src = root + 'api/files/' + encodeURIComponent(val);
                    img.loading = 'lazy'; img.alt = field.label; dd.append(img);
                } else if (field.type === 'datetime' && val) dd.textContent = new Date(val).toLocaleString();
                else dd.textContent = row.display?.[name] ?? (val == null ? '—' : typeof val === 'boolean' ? (val ? 'Да':'Нет') : String(val));
                dl.append(el('dt',field.label), dd);
            }
            card.append(dl);
            if (rights.update === 'all' || (rights.update === 'own' && row.owner_id === me.id))
                card.append(action('Изменить', () => editor(key, row, edit, refresh)));
            list.append(card); after = row.id;
        }
        if (records.length === 100) {
            const more = action('Ещё записи', async () => { await next(); more.remove(); });
            list.append(more);
        }
    }
    await next();
}
async function start() {
    try { me = await api('me'); }
    catch (err) { if (err.status === 401) { login(); return; } throw err; }
    session.replaceChildren(el('span', me.username + ' · ' + definition.roles[me.role] + ' '),
        action('Выйти', async () => { await api('logout','POST'); login(); }));
    nav.replaceChildren();
    const keys = Object.keys(definition.tables).filter(k => {
        const r = definition.tables[k].access[me.role]; return r.read !== 'none' || r.create;
    });
    for (const key of keys) {
        const b = action(definition.tables[key].label, () => openTable(key));
        b.dataset.key = key; nav.append(b);
    }
    if (keys.length) await openTable(keys[0]);
    else workspace.replaceChildren(el('p','У вашей роли пока нет доступных разделов.'));
}
guarded(start);
