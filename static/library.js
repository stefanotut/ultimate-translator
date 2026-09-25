/* =========================================================================
   ULTIMATE TRANSLATOR - Libreria
   Folders, automatic tags, colors, drag & drop, uploads (files and folders)
   ========================================================================= */
(() => {
'use strict';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const COLORS = [
    { key: 'red', name: 'Rosso', hex: '#F87171' },
    { key: 'orange', name: 'Arancione', hex: '#FB923C' },
    { key: 'yellow', name: 'Giallo', hex: '#FACC15' },
    { key: 'green', name: 'Verde', hex: '#4ADE80' },
    { key: 'teal', name: 'Turchese', hex: '#2DD4BF' },
    { key: 'blue', name: 'Blu', hex: '#60A5FA' },
    { key: 'indigo', name: 'Indaco', hex: '#818CF8' },
    { key: 'purple', name: 'Viola', hex: '#C084FC' },
    { key: 'pink', name: 'Rosa', hex: '#F472B6' },
    { key: 'brown', name: 'Marrone', hex: '#C08457' },
    { key: 'gray', name: 'Grigio', hex: '#94A3B8' },
];
const COLOR_BY_KEY = Object.fromEntries(COLORS.map(c => [c.key, c]));
const DEFAULT_FOLDER_COLOR = '#A78BFA';

const LANGUAGES = {
    English: ['Inglese', 'EN'], Italian: ['Italiano', 'IT'], French: ['Francese', 'FR'],
    German: ['Tedesco', 'DE'], Spanish: ['Spagnolo', 'ES'], Portuguese: ['Portoghese', 'PT'],
    Dutch: ['Olandese', 'NL'], Russian: ['Russo', 'RU'], Chinese: ['Cinese', 'ZH'],
    Japanese: ['Giapponese', 'JA'], Korean: ['Coreano', 'KO'], Arabic: ['Arabo', 'AR'],
    Polish: ['Polacco', 'PL'], Swedish: ['Svedese', 'SV'], Norwegian: ['Norvegese', 'NO'],
    Danish: ['Danese', 'DA'], Finnish: ['Finlandese', 'FI'], Czech: ['Ceco', 'CS'],
    Turkish: ['Turco', 'TR'], Hindi: ['Hindi', 'HI'], Greek: ['Greco', 'EL'],
    Romanian: ['Rumeno', 'RO'], Hungarian: ['Ungherese', 'HU'], Thai: ['Thailandese', 'TH'],
    Vietnamese: ['Vietnamita', 'VI'],
};

const SORTS = [
    { key: 'manual', label: 'Ordine personalizzato', short: 'Personalizzato' },
    { key: 'recent', label: 'Aggiunti di recente', short: 'Recenti' },
    { key: 'name', label: 'Nome (A-Z)', short: 'Nome' },
    { key: 'author', label: 'Autore', short: 'Autore' },
    { key: 'size', label: 'Dimensione', short: 'Dimensione' },
    { key: 'color', label: 'Colore', short: 'Colore' },
];

const VIEWS = {
    all: { label: 'Tutti i libri', icon: 'books' },
    folder: { label: 'Cartelle', icon: 'folder' },
    unfiled: { label: 'Senza cartella', icon: 'inbox' },
    favorites: { label: 'Preferiti', icon: 'star' },
    translated: { label: 'Tradotti', icon: 'globe' },
    trash: { label: 'Cestino', icon: 'trash' },
};

const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;
const UPLOAD_CONCURRENCY = 3;
const collator = new Intl.Collator('it', { numeric: true, sensitivity: 'base' });

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------
const FOLDER_PATH = '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>';
const ICONS = {
    folder: FOLDER_PATH,
    folderFill: '<path d="M3 7a2 2 0 0 1 2-2h4.2a2 2 0 0 1 1.4.6L12 7h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" fill="currentColor" stroke="none"/>',
    folderPlus: FOLDER_PATH + '<line x1="12" y1="10.5" x2="12" y2="15.5"/><line x1="9.5" y1="13" x2="14.5" y2="13"/>',
    folderUp: FOLDER_PATH + '<polyline points="9.5 12.5 12 10 14.5 12.5"/><line x1="12" y1="10" x2="12" y2="16"/>',
    folderOpen: '<path d="M3 8V6a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v1"/><path d="M3.5 19l2.2-8.1A2 2 0 0 1 7.6 9.4H21a1 1 0 0 1 1 1.3l-2 7.6a2 2 0 0 1-1.9 1.5H5a2 2 0 0 1-1.5-.8"/>',
    move: FOLDER_PATH + '<polyline points="12 10 15 13 12 16"/><line x1="8" y1="13" x2="15" y2="13"/>',
    books: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
    inbox: '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
    star: '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    starFill: '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" fill="currentColor"/>',
    globe: '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
    upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/>',
    list: '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
    sort: '<path d="M3 6h10"/><path d="M3 12h7"/><path d="M3 18h4"/><path d="M17 4v16"/><path d="M14 17l3 3 3-3"/>',
    filter: '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
    more: '<circle cx="5" cy="12" r="1.7" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.7" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.7" fill="currentColor" stroke="none"/>',
    x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    check: '<polyline points="20 6 9 17 4 12"/>',
    checkCircle: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
    chevronRight: '<polyline points="9 18 15 12 9 6"/>',
    chevronDown: '<polyline points="6 9 12 15 18 9"/>',
    key: '<circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3L21 2"/><path d="M16 7l3 3"/><path d="M19 4l2 2"/>',
    copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    sparkles: '<path d="M12 3l1.8 4.6L18.5 9l-4.7 1.4L12 15l-1.8-4.6L5.5 9l4.7-1.4z"/><path d="M19 14l.9 2.1L22 17l-2.1.9L19 20l-.9-2.1L16 17l2.1-.9z"/>',
    tag: '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/>',
    edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>',
    restore: '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
    refresh: '<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>',
    eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    zap: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    alert: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    info: '<circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>',
    plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    select: '<rect x="3" y="3" width="18" height="18" rx="4"/><polyline points="8 12 11 15 16 9"/>',
    palette: '<circle cx="13.5" cy="6.5" r="1.3" fill="currentColor"/><circle cx="17.5" cy="10.5" r="1.3" fill="currentColor"/><circle cx="8.5" cy="7.5" r="1.3" fill="currentColor"/><circle cx="6.5" cy="12.5" r="1.3" fill="currentColor"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.93 0 1.7-.77 1.7-1.7 0-.45-.17-.85-.44-1.15a1.7 1.7 0 0 1 1.27-2.85H16.5c3.04 0 5.5-2.46 5.5-5.5C22 6.03 17.52 2 12 2z"/>',
    spinner: '<circle cx="12" cy="12" r="9" stroke-dasharray="42 60" class="spin"/>',
};

const FOLDER_GLYPH = '<svg class="folder-glyph" viewBox="0 0 48 40" aria-hidden="true">'
    + '<path class="fg-back" d="M4 7a4 4 0 0 1 4-4h9.6a4 4 0 0 1 2.9 1.2L23.4 7H40a4 4 0 0 1 4 4v21a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4z"/>'
    + '<path class="fg-front" d="M2.2 15.4A4 4 0 0 1 6.2 11h35.6a4 4 0 0 1 4 4.4l-1.7 17A4 4 0 0 1 40.1 36H7.9a4 4 0 0 1-4-3.6z"/>'
    + '<path class="fg-shine" d="M6.2 11h35.6a4 4 0 0 1 3.9 3H2.3a4 4 0 0 1 3.9-3z"/></svg>';

function icon(name, size = 18, extraClass = '') {
    const spin = name === 'spinner' ? ' spinner-svg' : '';
    return `<svg class="ico${spin} ${extraClass}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, ch => ESCAPES[ch]);
const fold = (value) => String(value ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
const keyOf = (kind, id) => `${kind}:${id}`;
const parseKey = (key) => { const i = key.indexOf(':'); return { kind: key.slice(0, i), id: key.slice(i + 1) }; };

const store = {
    get(key, fallback) {
        try {
            const value = localStorage.getItem('ut-library:' + key);
            return value === null ? fallback : JSON.parse(value);
        } catch (_) { return fallback; }
    },
    set(key, value) {
        try { localStorage.setItem('ut-library:' + key, JSON.stringify(value)); } catch (_) { /* private mode */ }
    },
};

function push(map, key, value) {
    const list = map.get(key);
    if (list) list.push(value); else map.set(key, [value]);
}

function formatNumber(n) { return Number(n || 0).toLocaleString('it-IT'); }

function formatSize(bytes) {
    if (!bytes) return '0 B';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1).replace('.', ',')} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2).replace('.', ',')} GB`;
}

function formatDate(ts) {
    return new Date(ts * 1000).toLocaleDateString('it-IT', { day: 'numeric', month: 'short', year: 'numeric' });
}

const relativeTime = new Intl.RelativeTimeFormat('it', { numeric: 'auto' });
function timeAgo(ts) {
    const seconds = Math.round(ts - Date.now() / 1000);
    const steps = [[60, 'second'], [3600, 'minute'], [86400, 'hour'], [604800, 'day'], [2629800, 'week'], [31557600, 'month']];
    let unit = 'year';
    let divisor = 31557600;
    for (let i = 0; i < steps.length; i++) {
        if (Math.abs(seconds) < steps[i][0]) {
            unit = steps[i][1];
            divisor = i === 0 ? 1 : steps[i - 1][0];
            break;
        }
    }
    return relativeTime.format(Math.round(seconds / divisor), unit);
}

function plural(n, one, many) { return `${formatNumber(n)} ${n === 1 ? one : many}`; }
function langName(lang) { return LANGUAGES[lang] ? LANGUAGES[lang][0] : (lang || ''); }
function langCode(lang) { return LANGUAGES[lang] ? LANGUAGES[lang][1] : String(lang || '?').slice(0, 2).toUpperCase(); }
function colorHex(key) { return COLOR_BY_KEY[key] ? COLOR_BY_KEY[key].hex : null; }
function folderColor(folder) { return colorHex(folder && folder.color) || DEFAULT_FOLDER_COLOR; }
function tagColor(tag) { return colorHex(tag && tag.color) || '#C084FC'; }
function hashHue(text) {
    let h = 0;
    for (const ch of String(text)) h = (h * 31 + ch.codePointAt(0)) >>> 0;
    return h % 360;
}
function isTranslated(book) { return book.translations.some(t => t.status === 'completed'); }
function isTagging(book) { return book.tag_status === 'pending' || book.tag_status === 'running'; }
function taggingLabel() { return state.ai && state.ai.enabled ? 'Analisi AI in corso...' : 'Assegnazione tag...'; }

async function copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch (_) {
        const area = document.createElement('textarea');
        area.value = text;
        area.style.position = 'fixed';
        area.style.opacity = '0';
        document.body.appendChild(area);
        area.select();
        let ok = false;
        try { ok = document.execCommand('copy'); } catch (__) { ok = false; }
        area.remove();
        return ok;
    }
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------
async function api(method, url, body) {
    const options = { method, headers: {}, credentials: 'same-origin' };
    if (body !== undefined) {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    let data = null;
    try { data = await response.json(); } catch (_) { data = null; }
    if (!response.ok) {
        const error = new Error((data && data.error) || `Errore del server (${response.status})`);
        error.status = response.status;
        error.data = data;
        throw error;
    }
    return data;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
    loaded: false,
    folders: [],
    books: [],
    tags: [],
    trashCount: 0,
    ai: null,
    folderById: new Map(),
    bookById: new Map(),
    tagById: new Map(),
    childFolders: new Map(),
    booksIn: new Map(),
    subtreeBooks: new Map(),
    view: { type: 'folder', id: null },
    filters: { q: '', tags: [], format: null, color: null },
    sort: store.get('sort', 'manual'),
    layout: store.get('layout', 'grid'),
    expanded: new Set(store.get('expanded', [])),
    showAllTags: false,
    selection: new Set(),
    anchor: null,
    drawerId: null,
    renaming: null,
    trash: null,
    lastView: null,
    uploads: [],
    uploadNote: '',
};

const el = {
    content: $('#lib-content'),
    scroll: $('#lib-scroll'),
    views: $('#sb-views'),
    tree: $('#sb-tree'),
    tags: $('#sb-tags'),
    tagsGroup: $('#sb-tags-group'),
    footer: $('#sb-footer'),
    crumbs: $('#crumbs'),
    headerActions: $('#header-actions'),
    toolbarActions: $('#toolbar-actions'),
    viewChips: $('#view-chips'),
    filterChips: $('#filter-chips'),
    search: $('#search-input'),
    drawer: $('#drawer-root'),
    selectionBar: $('#selection-bar'),
    uploadPanel: $('#upload-panel'),
    toasts: $('#toast-stack'),
    modalRoot: $('#modal-root'),
    dropOverlay: $('#drop-overlay'),
    dropOverlayTitle: $('#drop-overlay-title'),
    fileInput: $('#file-input'),
    folderInput: $('#folder-input'),
};

function setData(data) {
    state.folders = data.folders || [];
    state.books = data.books || [];
    state.tags = data.tags || [];
    state.trashCount = data.trash_count || 0;
    state.ai = data.ai || null;
    state.loaded = true;
    reindex();
}

function reindex() {
    state.folderById = new Map(state.folders.map(f => [f.id, f]));
    state.bookById = new Map(state.books.map(b => [b.id, b]));
    state.tagById = new Map(state.tags.map(t => [t.id, t]));
    state.childFolders = new Map();
    for (const f of state.folders) push(state.childFolders, f.parent_id || '', f);
    state.booksIn = new Map();
    for (const b of state.books) push(state.booksIn, b.folder_id || '', b);

    const memo = new Map();
    const count = (id, depth) => {
        if (memo.has(id)) return memo.get(id);
        memo.set(id, 0);
        let n = (state.booksIn.get(id) || []).length;
        if (depth < 64) for (const child of state.childFolders.get(id) || []) n += count(child.id, depth + 1);
        memo.set(id, n);
        return n;
    };
    for (const f of state.folders) count(f.id, 0);
    state.subtreeBooks = memo;

    for (const b of state.books) {
        const tagNames = b.tags.map(t => (state.tagById.get(t.id) || {}).name || '');
        const path = b.folder_id ? folderPath(b.folder_id).map(f => f.name) : [];
        b._hay = fold([b.title, b.author, b.original_filename, b.summary, langName(b.language), ...tagNames, ...path].join(' '));
    }

    for (const key of [...state.selection]) {
        const { kind, id } = parseKey(key);
        if (!(kind === 'book' ? state.bookById.has(id) : state.folderById.has(id))) state.selection.delete(key);
    }
    state.filters.tags = state.filters.tags.filter(id => state.tagById.has(id));
    if (state.drawerId && !state.bookById.has(state.drawerId)) state.drawerId = null;
    if (state.view.type === 'folder' && state.view.id && !state.folderById.has(state.view.id)) {
        state.view = { type: 'folder', id: null };
        history.replaceState(null, '', hashFor(state.view));
    }
}

function folderPath(folderId) {
    const path = [];
    const seen = new Set();
    let current = folderId && state.folderById.get(folderId);
    while (current && !seen.has(current.id)) {
        seen.add(current.id);
        path.unshift(current);
        current = current.parent_id && state.folderById.get(current.parent_id);
    }
    return path;
}

function subtreeIds(folderId) {
    const out = new Set();
    const walk = (id) => {
        if (out.has(id)) return;
        out.add(id);
        for (const child of state.childFolders.get(id) || []) walk(child.id);
    };
    walk(folderId);
    return out;
}

function parentOf(item) {
    if (item.kind === 'book') return (state.bookById.get(item.id) || {}).folder_id || null;
    return (state.folderById.get(item.id) || {}).parent_id || null;
}

function itemName(item) {
    if (item.kind === 'book') return (state.bookById.get(item.id) || {}).title || '';
    return (state.folderById.get(item.id) || {}).name || '';
}

function describeItems(items) {
    if (items.length === 1) return `«${itemName(items[0])}»`;
    const books = items.filter(i => i.kind === 'book').length;
    const folders = items.length - books;
    return [books ? plural(books, 'libro', 'libri') : '', folders ? plural(folders, 'cartella', 'cartelle') : ''].filter(Boolean).join(' e ');
}

// ---------------------------------------------------------------------------
// Views, filters, sorting
// ---------------------------------------------------------------------------
function hashFor(view) {
    switch (view.type) {
        case 'all': return '#/tutti';
        case 'unfiled': return '#/senza-cartella';
        case 'favorites': return '#/preferiti';
        case 'translated': return '#/tradotti';
        case 'trash': return '#/cestino';
        default: return view.id ? `#/cartella/${view.id}` : '#/cartelle';
    }
}

function viewFromHash() {
    const [type, id] = decodeURIComponent(location.hash.replace(/^#\/?/, '')).split('/');
    switch (type) {
        case 'tutti': return { type: 'all' };
        case 'senza-cartella': return { type: 'unfiled' };
        case 'preferiti': return { type: 'favorites' };
        case 'tradotti': return { type: 'translated' };
        case 'cestino': return { type: 'trash' };
        case 'cartella': return { type: 'folder', id: id || null };
        case 'libro': return { type: 'book', id };
        default: return { type: 'folder', id: null };
    }
}

function go(view, options = {}) {
    const hash = hashFor(view);
    if (options.replace) history.replaceState(null, '', hash);
    else if (location.hash !== hash) history.pushState(null, '', hash);
    applyView(view, options);
}

function applyView(view, options = {}) {
    if (view.type === 'book') {
        const book = state.bookById.get(view.id);
        view = { type: 'folder', id: book ? book.folder_id : null };
        history.replaceState(null, '', hashFor(view));
        if (book) state.drawerId = book.id;
    }
    if (view.type === 'folder' && view.id && !state.folderById.has(view.id)) view = { type: 'folder', id: null };
    const changed = view.type !== state.view.type || (view.id || null) !== (state.view.id || null);
    state.view = view;
    if (changed && !options.keepFilters) {
        state.filters = { q: '', tags: [], format: null, color: null };
        el.search.value = '';
    }
    if (changed) {
        state.selection.clear();
        state.anchor = null;
        el.scroll.scrollTop = 0;
    }
    if (view.type === 'folder' && view.id) {
        for (const f of folderPath(view.id).slice(0, -1)) state.expanded.add(f.id);
    }
    if (view.type === 'trash') loadTrash();
    closeSidebar();
    render();
}

function isFiltering() {
    const f = state.filters;
    return Boolean(f.q.trim() || f.tags.length || f.format || f.color);
}

function colorRank(key) {
    const index = COLORS.findIndex(c => c.key === key);
    return index === -1 ? COLORS.length : index;
}

function folderPathKey(folderId) {
    return folderId ? folderPath(folderId).map(f => f.name).join('\u0001') : '';
}

function sortBooks(list, flat) {
    const books = [...list];
    const byTitle = (a, b) => collator.compare(a.title, b.title);
    switch (state.sort) {
        case 'recent': return books.sort((a, b) => b.created_at - a.created_at);
        case 'name': return books.sort(byTitle);
        case 'author': return books.sort((a, b) => (!a.author - !b.author) || collator.compare(a.author || '', b.author || '') || byTitle(a, b));
        case 'size': return books.sort((a, b) => b.file_size - a.file_size);
        case 'color': return books.sort((a, b) => colorRank(a.color) - colorRank(b.color) || a.position - b.position);
        default:
            if (flat) {
                return books.sort((a, b) => collator.compare(folderPathKey(a.folder_id), folderPathKey(b.folder_id))
                    || a.position - b.position || a.created_at - b.created_at);
            }
            return books.sort((a, b) => a.position - b.position || a.created_at - b.created_at);
    }
}

function sortFolders(list) {
    const folders = [...list];
    switch (state.sort) {
        case 'recent': return folders.sort((a, b) => b.created_at - a.created_at);
        case 'color': return folders.sort((a, b) => colorRank(a.color) - colorRank(b.color) || a.position - b.position);
        case 'manual': return folders.sort((a, b) => a.position - b.position || a.created_at - b.created_at);
        default: return folders.sort((a, b) => collator.compare(a.name, b.name));
    }
}

function bookMatches(book, filters, tokens) {
    if (filters.format && book.file_type !== filters.format) return false;
    if (filters.color && book.color !== filters.color) return false;
    if (filters.tags.length && !filters.tags.every(id => book.tags.some(t => t.id === id))) return false;
    if (tokens.length && !tokens.every(token => book._hay.includes(token))) return false;
    return true;
}

function computeView() {
    const view = state.view;
    const filters = state.filters;
    const filtering = isFiltering();
    const tokens = fold(filters.q).split(/\s+/).filter(Boolean);
    let folders = [];
    let books = [];
    let mode = 'flat';
    let container = null;

    switch (view.type) {
        case 'folder':
            if (!filtering) {
                mode = 'browse';
                container = view.id || null;
                folders = state.childFolders.get(view.id || '') || [];
                books = state.booksIn.get(view.id || '') || [];
            } else {
                const scope = view.id ? subtreeIds(view.id) : null;
                books = state.books.filter(b => !scope || (b.folder_id && scope.has(b.folder_id)));
                if (!filters.tags.length && !filters.format) {
                    folders = state.folders.filter(f => (!scope || (scope.has(f.id) && f.id !== view.id))
                        && (!filters.color || f.color === filters.color)
                        && tokens.every(token => fold(f.name).includes(token)));
                }
            }
            break;
        case 'all':
            books = state.books;
            break;
        case 'unfiled':
            books = state.booksIn.get('') || [];
            if (!filtering) mode = 'container';
            break;
        case 'favorites':
            books = state.books.filter(b => b.favorite);
            break;
        case 'translated':
            books = state.books.filter(isTranslated);
            break;
        default:
            break;
    }
    if (filtering) books = books.filter(b => bookMatches(b, filters, tokens));
    const flat = mode === 'flat';
    const result = {
        folders: sortFolders(folders),
        books: sortBooks(books, flat),
        mode,
        container,
        flat,
        filtering,
        reorderable: mode !== 'flat',
    };
    state.lastView = result;
    return result;
}

function currentFolderId() {
    return state.view.type === 'folder' ? (state.view.id || null) : null;
}

// ---------------------------------------------------------------------------
// Rendering helpers
// ---------------------------------------------------------------------------
function setHTML(target, html) {
    if (target._html === html) return false;
    const active = document.activeElement;
    let restore = null;
    if (active && target.contains(active) && active.dataset && active.dataset.focusKey) {
        restore = { key: active.dataset.focusKey, value: active.value, start: active.selectionStart, end: active.selectionEnd };
    }
    target.innerHTML = html;
    target._html = html;
    if (restore) {
        const next = target.querySelector(`[data-focus-key="${restore.key}"]`);
        if (next) {
            if ('value' in next) next.value = restore.value;
            next.focus();
            try { next.setSelectionRange(restore.start, restore.end); } catch (_) { /* not a text field */ }
        }
    }
    return true;
}

function invalidate() {
    for (const target of [el.content, el.views, el.tree, el.tags, el.footer, el.crumbs, el.headerActions,
        el.toolbarActions, el.viewChips, el.filterChips, el.drawer, el.selectionBar]) {
        target._html = null;
    }
}

let renderQueued = false;
function scheduleRender() {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(() => {
        renderQueued = false;
        render();
    });
}

function render() {
    if (!state.loaded) return;
    if (!state.renaming) {
        renderSidebar();
        if (!drag.active) renderContent();
    }
    renderHeader();
    renderChips();
    renderSelectionBar();
    renderDrawer();
    if (state.uploads.length) scheduleUploadRender();
}

function coverHTML(book, small = false) {
    if (book.cover_url) {
        return `<img class="cover-img" src="${esc(book.cover_url)}" alt="" loading="lazy" decoding="async" draggable="false">`;
    }
    const base = colorHex(book.color) || `hsl(${hashHue(book.title)} 55% 46%)`;
    return `<div class="cover-gen" style="--c1:${base}">`
        + `<div class="cover-gen-title">${small ? '' : esc(book.title)}</div>`
        + `<div class="cover-gen-author">${small ? '' : esc(book.author || '')}</div></div>`;
}

function translationsByLanguage(book) {
    const latest = new Map();
    for (const t of book.translations) {
        const previous = latest.get(t.target_lang);
        if (!previous || t.created_at > previous.created_at) latest.set(t.target_lang, t);
    }
    return [...latest.values()];
}

function translationBadges(book) {
    const items = translationsByLanguage(book);
    if (!items.length) return '';
    return `<div class="tr-badges">${items.map(t => {
        if (t.status === 'completed') return `<span class="tr-badge" title="Tradotto in ${esc(langName(t.target_lang))}">${icon('check', 10)}${esc(langCode(t.target_lang))}</span>`;
        if (t.status === 'error') return `<span class="tr-badge is-error" title="Traduzione non riuscita">${esc(langCode(t.target_lang))}</span>`;
        return `<span class="tr-badge is-running" title="Traduzione in corso">${esc(langCode(t.target_lang))} ${Math.round((t.progress || 0) * 100)}%</span>`;
    }).join('')}</div>`;
}

function tagChipsHTML(book, limit = 3) {
    if (isTagging(book) && !book.tags.length) {
        return `<span class="ai-pending">${icon('sparkles', 11)}${state.ai && state.ai.enabled ? 'Analisi AI...' : 'Tag in arrivo...'}</span>`;
    }
    const tags = book.tags.map(t => state.tagById.get(t.id)).filter(Boolean);
    const shown = tags.slice(0, limit).map(t => `<span class="tag-chip" style="--tag-color:${tagColor(t)}">${esc(t.name)}</span>`);
    if (tags.length > limit) shown.push(`<span class="tag-chip more">+${tags.length - limit}</span>`);
    return shown.join('');
}

function bookCardHTML(book, showFolder) {
    const selected = state.selection.has(keyOf('book', book.id));
    const color = colorHex(book.color);
    const folder = showFolder && book.folder_id ? state.folderById.get(book.folder_id) : null;
    const pages = book.num_pages ? `${formatNumber(book.num_pages)} pag.` : (book.num_chapters ? `${formatNumber(book.num_chapters)} cap.` : '');
    const done = translationsByLanguage(book).filter(t => t.status === 'completed').map(t => langCode(t.target_lang));
    return `<article class="card book-card${selected ? ' is-selected' : ''}" data-kind="book" data-id="${book.id}" tabindex="0"${color ? ` style="--item-color:${color}"` : ''} aria-label="${esc(book.title)}">
        <div class="book-cover-wrap">
            <div class="book-cover">
                ${coverHTML(book)}
                <span class="fmt-badge fmt-${book.file_type}">${book.file_type.toUpperCase()}</span>
                ${book.favorite ? `<span class="fav-badge" title="Preferito">${icon('starFill', 13)}</span>` : ''}
                ${translationBadges(book)}
            </div>
            ${color ? '<span class="color-ribbon" aria-hidden="true"></span>' : ''}
            <button class="card-check" data-action="toggle-select" aria-label="Seleziona" aria-pressed="${selected}">${icon('check', 14)}</button>
            <button class="card-more" data-action="item-menu" aria-label="Azioni per ${esc(book.title)}">${icon('more', 16)}</button>
        </div>
        <div class="book-info">
            <div class="book-main">
                <h3 class="book-title" data-rename title="${esc(book.title)}">${esc(book.title)}</h3>
                <div class="book-author">${esc(book.author || '')}</div>
                ${folder ? `<div class="book-folder" style="--item-color:${folderColor(folder)}">${icon('folderFill', 12)}<span>${esc(folderPath(folder.id).map(f => f.name).join(' › '))}</span></div>` : ''}
            </div>
            <div class="book-tags">${tagChipsHTML(book)}</div>
            <div class="book-meta">
                ${color ? '<span class="dot-color"></span>' : ''}
                ${book.favorite ? `<span class="star">${icon('starFill', 12)}</span>` : ''}
                ${done.length ? `<span class="tr-inline">${icon('globe', 12)}</span><span class="tr-inline">${esc(done.join(' '))}</span>` : ''}
                <span class="fmt ${book.file_type}">${book.file_type.toUpperCase()}</span>
                <span>${formatSize(book.file_size)}</span>
                ${pages ? `<span>${pages}</span>` : ''}
                <span class="date">${formatDate(book.created_at)}</span>
            </div>
        </div>
    </article>`;
}

function peekBooks(folderId, limit = 3) {
    const out = [];
    const queue = [folderId];
    const seen = new Set();
    while (queue.length && out.length < limit) {
        const id = queue.shift();
        if (seen.has(id)) continue;
        seen.add(id);
        const books = [...(state.booksIn.get(id) || [])].sort((a, b) => a.position - b.position);
        for (const book of books) {
            if (out.length >= limit) break;
            out.push(book);
        }
        for (const child of state.childFolders.get(id) || []) queue.push(child.id);
    }
    return out;
}

function folderCardHTML(folder) {
    const selected = state.selection.has(keyOf('folder', folder.id));
    const subfolders = (state.childFolders.get(folder.id) || []).length;
    const total = state.subtreeBooks.get(folder.id) || 0;
    const meta = [total ? plural(total, 'libro', 'libri') : 'Vuota', subfolders ? plural(subfolders, 'cartella', 'cartelle') : '']
        .filter(Boolean).join(' · ');
    const peek = peekBooks(folder.id);
    return `<article class="card folder-card${selected ? ' is-selected' : ''}" data-kind="folder" data-id="${folder.id}" data-drop="folder" data-folder-id="${folder.id}" tabindex="0" style="--item-color:${folderColor(folder)}" aria-label="Cartella ${esc(folder.name)}">
        ${FOLDER_GLYPH}
        <div class="folder-info">
            <h3 class="folder-name" data-rename title="${esc(folder.name)}">${esc(folder.name)}</h3>
            <div class="folder-meta">${meta}</div>
        </div>
        ${peek.length ? `<div class="folder-peek" aria-hidden="true">${peek.map(b => `<div class="folder-peek-item">${coverHTML(b, true)}</div>`).join('')}</div>` : ''}
        <button class="card-check" data-action="toggle-select" aria-label="Seleziona" aria-pressed="${selected}">${icon('check', 14)}</button>
        <button class="card-more" data-action="item-menu" aria-label="Azioni per ${esc(folder.name)}">${icon('more', 16)}</button>
    </article>`;
}

function sectionHTML(title, count, inner, hint = '') {
    return `<section class="lib-section">
        <div class="lib-section-head"><span>${title}</span><span class="count">${formatNumber(count)}</span>${hint ? `<span class="hint">${hint}</span>` : ''}</div>
        ${inner}
    </section>`;
}

const BOOKS_ILLUSTRATION = `<svg class="empty-illustration" width="120" height="96" viewBox="0 0 120 96" fill="none" aria-hidden="true">
    <rect x="14" y="22" width="22" height="64" rx="4" fill="currentColor" opacity="0.35"/>
    <rect x="40" y="12" width="24" height="74" rx="4" fill="currentColor" opacity="0.6"/>
    <rect x="68" y="26" width="20" height="60" rx="4" transform="rotate(-10 68 26)" fill="currentColor" opacity="0.45"/>
    <rect x="44" y="24" width="16" height="3" rx="1.5" fill="#fff" opacity="0.5"/>
    <rect x="18" y="34" width="14" height="3" rx="1.5" fill="#fff" opacity="0.4"/>
    <rect x="6" y="86" width="108" height="4" rx="2" fill="currentColor" opacity="0.5"/>
    <path d="M98 10l2.4 6.1 6.1 2.4-6.1 2.4L98 27l-2.4-6.1-6.1-2.4 6.1-2.4z" fill="#22D3EE"/>
    <path d="M108 32l1.3 3.2 3.2 1.3-3.2 1.3-1.3 3.2-1.3-3.2-3.2-1.3 3.2-1.3z" fill="#FBBF24"/>
</svg>`;

function emptyLibraryHTML() {
    return `<div class="empty-state">
        ${BOOKS_ILLUSTRATION}
        <div class="empty-title">La tua libreria è pronta</div>
        <p class="empty-text">Carica libri EPUB e PDF, oppure intere cartelle: ogni libro riceve in automatico tag come <em>marketing</em>, <em>vendita</em> o <em>profumi</em>, e le cartelle caricate diventano cartelle della libreria.</p>
        <div class="empty-actions">
            <button class="btn btn-primary" data-action="upload-files">${icon('upload')}Carica libri</button>
            <button class="btn" data-action="upload-folder">${icon('folderUp')}Carica una cartella</button>
            <button class="btn btn-ghost" data-action="new-folder">${icon('folderPlus')}Nuova cartella</button>
        </div>
        <div class="empty-hint">oppure trascina qui file e cartelle dal tuo computer</div>
        <div class="empty-features">
            <div class="empty-feature">${icon('sparkles', 22)}<strong>Tag automatici</strong>L'AI legge il libro e assegna gli argomenti</div>
            <div class="empty-feature">${icon('folder', 22)}<strong>Cartelle e colori</strong>Organizza con il drag &amp; drop, rinomina al volo</div>
            <div class="empty-feature">${icon('globe', 22)}<strong>Traduci con un clic</strong>Ogni traduzione resta collegata al suo libro</div>
        </div>
    </div>`;
}

function emptyViewHTML(view) {
    if (view.filtering) {
        return `<div class="empty-state is-compact">
            <div class="empty-title">Nessun risultato</div>
            <p class="empty-text">Nessun libro corrisponde ai filtri attivi.</p>
            <div class="empty-actions"><button class="btn" data-action="clear-filters">${icon('x')}Rimuovi filtri</button></div>
        </div>`;
    }
    switch (state.view.type) {
        case 'unfiled':
            return `<div class="empty-state is-compact"><div class="empty-title">Tutto in ordine</div><p class="empty-text">Tutti i tuoi libri sono dentro una cartella.</p></div>`;
        case 'favorites':
            return `<div class="empty-state is-compact"><div class="empty-title">Nessun preferito</div><p class="empty-text">Aggiungi un libro ai preferiti dal suo menu, oppure trascinalo su «Preferiti» nella barra laterale.</p></div>`;
        case 'translated':
            return `<div class="empty-state is-compact"><div class="empty-title">Nessun libro tradotto</div><p class="empty-text">Apri un libro e premi «Traduci»: la traduzione comparirà qui, collegata al libro originale.</p></div>`;
        default:
            return `<div class="empty-state is-compact">
                <div class="empty-title">Questa cartella è vuota</div>
                <p class="empty-text">Trascina qui dei libri, caricane di nuovi o crea una sottocartella.</p>
                <div class="empty-actions">
                    <button class="btn btn-primary" data-action="upload-files">${icon('upload')}Carica libri qui</button>
                    <button class="btn" data-action="new-folder">${icon('folderPlus')}Nuova sottocartella</button>
                </div>
            </div>`;
    }
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------
function treeHTML(parentKey, depth) {
    const children = sortFolders(state.childFolders.get(parentKey) || []);
    return children.map(folder => {
        const kids = state.childFolders.get(folder.id) || [];
        const open = state.expanded.has(folder.id);
        const active = state.view.type === 'folder' && state.view.id === folder.id;
        return `<div class="tree-node" role="none">
            <div class="tree-row${active ? ' is-active' : ''}" role="treeitem" tabindex="0" aria-expanded="${kids.length ? open : 'false'}"
                style="--depth:${depth};--item-color:${folderColor(folder)}" data-kind="folder" data-id="${folder.id}"
                data-drop="folder" data-folder-id="${folder.id}" data-nav="folder" title="${esc(folder.name)}">
                <button class="tree-toggle${kids.length ? '' : ' is-empty'}" data-action="toggle-tree" data-id="${folder.id}" tabindex="-1" aria-label="${open ? 'Comprimi' : 'Espandi'}">${kids.length ? icon(open ? 'chevronDown' : 'chevronRight', 14) : ''}</button>
                <span class="tree-icon">${icon('folderFill', 17)}</span>
                <span class="tree-label" data-rename>${esc(folder.name)}</span>
                <span class="sb-count">${formatNumber(state.subtreeBooks.get(folder.id) || 0)}</span>
            </div>
            ${kids.length && open ? `<div class="tree-children" role="group">${treeHTML(folder.id, depth + 1)}</div>` : ''}
        </div>`;
    }).join('');
}

function renderSidebar() {
    const view = state.view;
    const unfiled = (state.booksIn.get('') || []).length;
    const favorites = state.books.filter(b => b.favorite).length;
    const translated = state.books.filter(isTranslated).length;
    const item = (type, count, drop) => {
        const meta = VIEWS[type];
        const active = view.type === type && (type !== 'folder' || !view.id);
        const dropAttrs = drop ? ` data-drop="${drop.type}"${drop.folder !== undefined ? ` data-folder-id="${drop.folder}"` : ''}` : '';
        return `<button class="sb-item${active ? ' is-active' : ''}" data-nav="${type}"${dropAttrs}>
            ${icon(meta.icon)}<span class="sb-label">${meta.label}</span>${count !== null ? `<span class="sb-count">${formatNumber(count)}</span>` : ''}
        </button>`;
    };
    setHTML(el.views, [
        item('all', state.books.length),
        item('folder', state.folders.length, { type: 'folder', folder: '' }),
        item('unfiled', unfiled, { type: 'folder', folder: '' }),
        item('favorites', favorites, { type: 'favorite' }),
        item('translated', translated),
    ].join(''));

    setHTML(el.tree, state.folders.length
        ? `<div role="tree" aria-label="Cartelle">${treeHTML('', 0)}</div>`
        : '<div class="tree-empty">Nessuna cartella. Creane una con + oppure carica una cartella dal computer.</div>');

    const tags = state.tags.filter(t => t.count > 0 || state.filters.tags.includes(t.id));
    const limit = 14;
    const shown = state.showAllTags ? tags : tags.slice(0, limit);
    el.tagsGroup.hidden = !tags.length;
    setHTML(el.tags, shown.map(tag => {
        const active = state.filters.tags.includes(tag.id);
        return `<div class="sb-tag${active ? ' is-active' : ''}" role="button" tabindex="0" data-action="sidebar-tag" data-tag-id="${tag.id}" data-drop="tag" style="--tag-color:${tagColor(tag)}" title="Mostra i libri con il tag «${esc(tag.name)}»">
            <span class="tag-dot"></span><span class="sb-label">${esc(tag.name)}</span><span class="sb-count">${formatNumber(tag.count)}</span>
            <button class="sb-tag-more" data-action="tag-menu" data-tag-id="${tag.id}" aria-label="Opzioni tag ${esc(tag.name)}">${icon('more', 14)}</button>
        </div>`;
    }).join('') + (tags.length > limit
        ? `<button class="sb-tags-toggle" data-action="toggle-all-tags">${state.showAllTags ? 'Mostra meno' : `Mostra tutti (${tags.length})`}</button>`
        : ''));

    const ai = state.ai || {};
    setHTML(el.footer, `
        <button class="sb-item${view.type === 'trash' ? ' is-active' : ''}" data-nav="trash" data-drop="trash">${icon('trash')}<span class="sb-label">Cestino</span>${state.trashCount ? `<span class="sb-count">${formatNumber(state.trashCount)}</span>` : ''}</button>
        <button class="sb-item" data-action="open-key">${icon('key')}<span class="sb-label">Codice libreria</span></button>
        <div class="ai-status${ai.enabled ? ' is-on' : ''}">${icon('sparkles', 16)}<span>${ai.enabled
            ? `Tag automatici con <strong>${esc(ai.label)}</strong>`
            : 'Tag automatici per parole chiave. Configura una chiave API per i tag AI.'}</span></div>
    `);
}

function openSidebar() { document.body.classList.add('sidebar-open'); }
function closeSidebar() { document.body.classList.remove('sidebar-open'); }

function saveExpanded() { store.set('expanded', [...state.expanded].filter(id => state.folderById.has(id))); }

// ---------------------------------------------------------------------------
// Header, toolbar, chips
// ---------------------------------------------------------------------------
function renderHeader() {
    const view = state.view;
    let crumbs;
    if (view.type === 'folder') {
        const path = view.id ? folderPath(view.id) : [];
        const parts = [`<button class="crumb${path.length ? '' : ' is-current'}${path.length === 1 ? ' is-parent' : ''}" data-nav="folder" data-drop="folder" data-folder-id="">${icon('folder', 18)}<span>Cartelle</span>${path.length ? '' : `<span class="crumb-count">${formatNumber(state.books.length)} libri</span>`}</button>`];
        path.forEach((folder, index) => {
            const last = index === path.length - 1;
            const parent = index === path.length - 2;
            parts.push(`<span class="crumb-sep${parent || (last && path.length === 1) ? ' is-parent' : ''}">${icon('chevronRight', 16)}</span>`);
            parts.push(`<button class="crumb${last ? ' is-current' : ''}${parent ? ' is-parent' : ''}" data-nav="folder" data-id="${folder.id}" data-drop="folder" data-folder-id="${folder.id}" style="--item-color:${folderColor(folder)}" title="${esc(folder.name)}">${last ? icon('folderFill', 20) : ''}<span>${esc(folder.name)}</span></button>`);
        });
        crumbs = parts.join('');
    } else {
        const meta = VIEWS[view.type];
        crumbs = `<span class="crumb is-current">${icon(meta.icon, 20)}<span>${meta.label}</span></span>`;
    }
    setHTML(el.crumbs, crumbs);

    if (view.type === 'trash') {
        const hasItems = state.trash && state.trash.items && state.trash.items.length;
        setHTML(el.headerActions, hasItems ? `<button class="btn btn-danger-ghost" data-action="empty-trash">${icon('trash')}<span class="btn-text">Svuota cestino</span></button>` : '');
    } else {
        setHTML(el.headerActions, `
            <button class="btn btn-ghost" data-action="new-folder" title="Nuova cartella">${icon('folderPlus')}<span class="btn-text">Nuova cartella</span></button>
            <div class="split-btn">
                <button class="btn btn-primary" data-action="upload-files" title="Carica libri EPUB o PDF">${icon('upload')}<span class="btn-text">Carica libri</span></button>
                <button class="btn btn-primary" data-action="upload-menu" aria-label="Altre opzioni di caricamento">${icon('chevronDown', 16)}</button>
            </div>`);
    }

    const sort = SORTS.find(s => s.key === state.sort) || SORTS[0];
    const filtersOn = (state.filters.format ? 1 : 0) + (state.filters.color ? 1 : 0);
    setHTML(el.toolbarActions, view.type === 'trash' ? '' : `
        <button class="btn toolbar-btn" data-action="filter-menu" aria-label="Filtri">${icon('filter', 16)}<span class="btn-text">Filtri${filtersOn ? ` (${filtersOn})` : ''}</span></button>
        <button class="btn toolbar-btn" data-action="sort-menu" aria-label="Ordina">${icon('sort', 16)}<span class="btn-text"><span class="btn-label-muted">Ordina:</span> ${sort.short}</span></button>
        <button class="icon-btn layout-toggle" data-action="layout" data-layout="${state.layout === 'grid' ? 'list' : 'grid'}" aria-label="${state.layout === 'grid' ? 'Vista elenco' : 'Vista griglia'}">${icon(state.layout === 'grid' ? 'list' : 'grid', 17)}</button>
        <div class="segmented" role="group" aria-label="Vista">
            <button class="icon-btn${state.layout === 'grid' ? ' is-active' : ''}" data-action="layout" data-layout="grid" aria-label="Griglia" title="Griglia">${icon('grid', 17)}</button>
            <button class="icon-btn${state.layout === 'list' ? ' is-active' : ''}" data-action="layout" data-layout="list" aria-label="Elenco" title="Elenco">${icon('list', 17)}</button>
        </div>`);
}

function renderChips() {
    const view = state.view;
    const counts = {
        all: state.books.length,
        folder: state.folders.length,
        unfiled: (state.booksIn.get('') || []).length,
        favorites: state.books.filter(b => b.favorite).length,
        translated: state.books.filter(isTranslated).length,
    };
    setHTML(el.viewChips, ['all', 'folder', 'unfiled', 'favorites', 'translated'].map(type => {
        const active = view.type === type;
        return `<button class="chip${active ? ' is-active' : ''}" role="tab" aria-selected="${active}" data-action="view-chip" data-view="${type}">
            ${icon(VIEWS[type].icon, 15)}${VIEWS[type].label}<span class="chip-count">${formatNumber(counts[type])}</span>
        </button>`;
    }).join(''));

    if (view.type === 'trash') {
        setHTML(el.filterChips, '');
        return;
    }
    const filters = state.filters;
    const parts = [];
    if (filters.format) {
        parts.push(`<button class="chip filter-pill" data-action="remove-filter" data-filter="format">${icon('books', 13)}${filters.format.toUpperCase()}${icon('x', 13)}</button>`);
    }
    if (filters.color) {
        parts.push(`<button class="chip filter-pill" data-action="remove-filter" data-filter="color"><span class="tag-dot" style="--tag-color:${colorHex(filters.color)}"></span>${COLOR_BY_KEY[filters.color].name}${icon('x', 13)}</button>`);
    }
    const tags = state.tags.filter(t => t.count > 0 || filters.tags.includes(t.id));
    if (tags.length) {
        parts.push(`<span class="chips-label">${icon('tag', 14)}</span>`);
        for (const tag of tags) {
            const active = filters.tags.includes(tag.id);
            parts.push(`<button class="chip tag-filter${active ? ' is-active' : ''}" data-action="filter-tag" data-tag-id="${tag.id}" style="--tag-color:${tagColor(tag)}" aria-pressed="${active}"><span class="tag-dot"></span>${esc(tag.name)}<span class="chip-count">${formatNumber(tag.count)}</span></button>`);
        }
    }
    if (isFiltering()) parts.push(`<button class="chip chip-clear" data-action="clear-filters">${icon('x', 13)}Rimuovi filtri</button>`);
    setHTML(el.filterChips, parts.join(''));
}

// ---------------------------------------------------------------------------
// Content
// ---------------------------------------------------------------------------
function keyBannerHTML() {
    if (state.books.length < 3 || store.get('keySeen', false) || store.get('keyBannerDismissed', false)) return '';
    return `<div class="key-banner">${icon('key', 20)}
        <p><strong>La libreria è salvata in modo privato in questo browser.</strong> Salva il suo codice per aprirla anche da telefono o da altri computer.</p>
        <button class="btn btn-sm" data-action="open-key">Mostra codice</button>
        <button class="icon-btn" data-action="dismiss-key-banner" aria-label="Chiudi">${icon('x', 16)}</button>
    </div>`;
}

function renderContent() {
    el.content.classList.toggle('has-selection', state.selection.size > 0);
    if (state.view.type === 'trash') {
        renderTrash();
        return;
    }
    if (!state.books.length && !state.folders.length) {
        setHTML(el.content, emptyLibraryHTML());
        return;
    }
    const view = computeView();
    let html = keyBannerHTML();
    if (view.filtering) {
        const where = state.view.type === 'folder'
            ? (state.view.id ? `in «${esc(state.folderById.get(state.view.id).name)}» e sottocartelle` : 'in tutta la libreria')
            : `in «${VIEWS[state.view.type].label}»`;
        html += `<div class="result-summary"><span><strong>${plural(view.books.length, 'libro', 'libri')}</strong>${view.folders.length ? ` e <strong>${plural(view.folders.length, 'cartella', 'cartelle')}</strong>` : ''} ${where}</span><button class="link-btn" data-action="clear-filters">Rimuovi filtri</button></div>`;
    }
    const manualHint = view.reorderable && state.layout === 'grid' ? 'Trascina per riordinare o spostare in una cartella' : '';
    if (view.folders.length) {
        html += sectionHTML('Cartelle', view.folders.length, `<div class="folder-grid">${view.folders.map(folderCardHTML).join('')}</div>`);
    }
    if (view.books.length) {
        html += sectionHTML(view.filtering ? 'Risultati' : 'Libri', view.books.length,
            `<div class="book-grid">${view.books.map(b => bookCardHTML(b, view.flat)).join('')}</div>`, manualHint);
    }
    if (!view.folders.length && !view.books.length) html += emptyViewHTML(view);
    setHTML(el.content, `<div class="content-inner layout-${state.layout}">${html}</div>`);
}

async function loadTrash() {
    state.trash = null;
    render();
    try {
        state.trash = await api('GET', '/api/library/trash');
    } catch (error) {
        state.trash = { items: [], retention_days: 30, error: error.message };
    }
    render();
}

function renderTrash() {
    const trash = state.trash;
    if (!trash) {
        setHTML(el.content, `<div class="lib-loading">${icon('spinner', 24)}<span>Caricamento cestino...</span></div>`);
        return;
    }
    if (!trash.items.length) {
        setHTML(el.content, `<div class="empty-state is-compact">${icon('trash', 40)}<div class="empty-title" style="margin-top:12px">Il cestino è vuoto</div>
            <p class="empty-text">Gli elementi eliminati restano qui per ${trash.retention_days} giorni, così puoi sempre ripristinarli.</p></div>`);
        return;
    }
    const tokens = fold(state.filters.q).split(/\s+/).filter(Boolean);
    const items = trash.items.filter(item => tokens.every(t => fold(`${item.name} ${item.author || ''} ${item.location.join(' ')}`).includes(t)));
    if (!items.length) {
        setHTML(el.content, `<div class="empty-state is-compact"><div class="empty-title">Nessun risultato nel cestino</div></div>`);
        return;
    }
    const rows = items.map(item => {
        const thumb = item.kind === 'folder'
            ? `<div class="trash-thumb is-folder" style="--item-color:${colorHex(item.color) || DEFAULT_FOLDER_COLOR}">${FOLDER_GLYPH}</div>`
            : `<div class="trash-thumb">${coverHTML({ cover_url: item.cover_url, title: item.name, author: item.author }, true)}</div>`;
        const where = item.location.length ? item.location.join(' › ') : 'Cartelle';
        const detail = item.kind === 'folder' ? ` · ${plural(item.book_count, 'libro', 'libri')}` : ` · ${esc(item.file_type.toUpperCase())}`;
        return `<div class="trash-row">
            ${thumb}
            <div class="trash-main">
                <div class="trash-name">${esc(item.name)}</div>
                <div class="trash-meta">Da ${esc(where)}${detail} · eliminato ${esc(timeAgo(item.deleted_at))}</div>
            </div>
            <div class="trash-actions">
                <button class="btn btn-sm" data-action="trash-restore" data-trash-kind="${item.kind}" data-trash-id="${item.id}">${icon('restore', 15)}<span class="btn-text">Ripristina</span></button>
                <button class="btn btn-sm btn-danger-ghost" data-action="trash-purge" data-trash-kind="${item.kind}" data-trash-id="${item.id}" aria-label="Elimina definitivamente">${icon('x', 15)}<span class="btn-text">Elimina</span></button>
            </div>
        </div>`;
    }).join('');
    setHTML(el.content, `
        <div class="trash-head"><span>${icon('info', 16)} Gli elementi nel cestino vengono eliminati definitivamente dopo ${trash.retention_days} giorni.</span></div>
        <div class="trash-list">${rows}</div>`);
}

// ---------------------------------------------------------------------------
// Selection
// ---------------------------------------------------------------------------
function visibleKeys() {
    const view = state.lastView;
    if (!view) return [];
    return [...view.folders.map(f => keyOf('folder', f.id)), ...view.books.map(b => keyOf('book', b.id))];
}

function updateSelectionUI() {
    for (const card of $$('#lib-content [data-kind][data-id]')) {
        const selected = state.selection.has(keyOf(card.dataset.kind, card.dataset.id));
        card.classList.toggle('is-selected', selected);
        const check = card.querySelector('.card-check');
        if (check) check.setAttribute('aria-pressed', String(selected));
    }
    el.content.classList.toggle('has-selection', state.selection.size > 0);
    el.content._html = null;
    renderSelectionBar();
}

function toggleSelect(key) {
    if (state.selection.has(key)) state.selection.delete(key);
    else state.selection.add(key);
    state.anchor = key;
    updateSelectionUI();
}

function selectRange(toKey) {
    const keys = visibleKeys();
    const a = keys.indexOf(state.anchor);
    const b = keys.indexOf(toKey);
    if (a === -1 || b === -1) {
        toggleSelect(toKey);
        return;
    }
    const [from, to] = a < b ? [a, b] : [b, a];
    for (let i = from; i <= to; i++) state.selection.add(keys[i]);
    updateSelectionUI();
}

function selectAllVisible() {
    for (const key of visibleKeys()) state.selection.add(key);
    updateSelectionUI();
}

function clearSelection() {
    if (!state.selection.size) return;
    state.selection.clear();
    state.anchor = null;
    updateSelectionUI();
}

function selectedItems() { return [...state.selection].map(parseKey); }

function renderSelectionBar() {
    const items = selectedItems();
    const visible = items.length > 0 && state.view.type !== 'trash';
    el.selectionBar.classList.toggle('is-visible', visible);
    if (!visible) {
        setHTML(el.selectionBar, '');
        return;
    }
    const books = items.filter(i => i.kind === 'book');
    const allFavorite = books.length && books.every(i => (state.bookById.get(i.id) || {}).favorite);
    setHTML(el.selectionBar, `
        <span class="selection-count">${plural(items.length, 'selezionato', 'selezionati')}</span>
        <button class="btn" data-action="sel-move" title="Sposta in una cartella">${icon('move', 17)}<span class="btn-text">Sposta</span></button>
        <button class="btn" data-action="sel-color" title="Colore">${icon('palette', 17)}<span class="btn-text">Colore</span></button>
        ${books.length ? `<button class="btn" data-action="sel-tags" title="Tag">${icon('tag', 17)}<span class="btn-text">Tag</span></button>
        <button class="btn" data-action="sel-favorite" title="Preferiti">${icon(allFavorite ? 'starFill' : 'star', 17)}<span class="btn-text">${allFavorite ? 'Togli preferiti' : 'Preferiti'}</span></button>` : ''}
        ${books.length === 1 && items.length === 1 ? `<button class="btn" data-action="sel-download" title="Scarica">${icon('download', 17)}<span class="btn-text">Scarica</span></button>` : ''}
        <button class="btn danger" data-action="sel-trash" title="Sposta nel cestino">${icon('trash', 17)}<span class="btn-text">Elimina</span></button>
        <span class="sep"></span>
        <button class="icon-btn" data-action="sel-clear" aria-label="Annulla selezione" title="Annulla selezione (Esc)">${icon('x', 18)}</button>
    `);
}

// ---------------------------------------------------------------------------
// Toasts
// ---------------------------------------------------------------------------
function toast(message, options = {}) {
    const type = options.type || 'info';
    const node = document.createElement('div');
    node.className = `toast ${type}`;
    node.setAttribute('role', type === 'error' ? 'alert' : 'status');
    const iconName = type === 'error' ? 'alert' : type === 'success' ? 'checkCircle' : 'info';
    node.innerHTML = `${icon(iconName, 18)}<span class="toast-msg">${esc(message)}</span>${options.action ? `<button class="toast-action">${esc(options.action)}</button>` : ''}`;
    el.toasts.appendChild(node);
    const remove = () => {
        node.classList.add('is-leaving');
        setTimeout(() => node.remove(), 200);
    };
    const timer = setTimeout(remove, options.duration || (options.action ? 7000 : 3800));
    if (options.action) {
        node.querySelector('.toast-action').addEventListener('click', () => {
            clearTimeout(timer);
            remove();
            if (options.onAction) options.onAction();
        });
    }
    while (el.toasts.children.length > 3) el.toasts.firstElementChild.remove();
}

async function run(task) {
    try {
        return await task();
    } catch (error) {
        console.error(error);
        toast(error.message || 'Si è verificato un errore', { type: 'error' });
        refresh();
        return undefined;
    }
}

// ---------------------------------------------------------------------------
// Data refresh & polling
// ---------------------------------------------------------------------------
let refreshPromise = null;
let refreshAgain = false;

function refresh() {
    if (refreshPromise) {
        refreshAgain = true;
        return refreshPromise;
    }
    refreshPromise = (async () => {
        try {
            do {
                refreshAgain = false;
                setData(await api('GET', '/api/library'));
            } while (refreshAgain);
            render();
            schedulePoll();
        } finally {
            refreshPromise = null;
        }
    })();
    return refreshPromise;
}

let pollTimer = null;
function schedulePoll(delay) {
    clearTimeout(pollTimer);
    const tagging = state.books.some(isTagging);
    const translating = state.books.some(b => b.translations.some(t => t.status === 'pending' || t.status === 'processing'));
    if (!tagging && !translating) return;
    pollTimer = setTimeout(poll, delay || (tagging ? 2500 : 5000));
}

async function poll() {
    if (document.hidden) {
        schedulePoll(5000);
        return;
    }
    try {
        const activity = await api('GET', '/api/library/activity');
        const tagging = new Set(activity.tagging);
        let changed = state.books.some(b => isTagging(b) && !tagging.has(b.id));
        const running = new Map(activity.translations.map(t => [t.id, t]));
        for (const book of state.books) {
            for (const t of book.translations) {
                if (t.status !== 'pending' && t.status !== 'processing') continue;
                const live = running.get(t.id);
                if (!live || live.status !== t.status) {
                    changed = true;
                    continue;
                }
                t.progress = live.progress;
                t.status_text = live.status_text;
            }
        }
        if (changed) {
            await refresh();
            return;
        }
        render();
    } catch (_) {
        /* temporary network issue: try again later */
    }
    schedulePoll();
}

document.addEventListener('visibilitychange', () => {
    if (!document.hidden && state.loaded) refresh().catch(() => {});
});

function mergeBook(book) {
    const index = state.books.findIndex(b => b.id === book.id);
    if (index === -1) state.books.push(book);
    else state.books[index] = book;
    reindex();
    scheduleRender();
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------
function uniqueFolderName(parentId, base = 'Nuova cartella') {
    const names = new Set((state.childFolders.get(parentId || '') || []).map(f => f.name.toLowerCase()));
    if (!names.has(base.toLowerCase())) return base;
    for (let i = 2; i < 1000; i++) {
        const name = `${base} ${i}`;
        if (!names.has(name.toLowerCase())) return name;
    }
    return base;
}

async function createFolder(parentId) {
    parentId = parentId || null;
    const data = await run(() => api('POST', '/api/library/folders', { name: uniqueFolderName(parentId), parent_id: parentId }));
    if (!data) return;
    if (parentId) {
        state.expanded.add(parentId);
        saveExpanded();
    }
    await refresh();
    const inPlace = state.view.type === 'folder' && (state.view.id || null) === parentId && !isFiltering();
    if (!inPlace) go({ type: 'folder', id: parentId });
    requestAnimationFrame(() => startRename('folder', data.folder.id));
}

function startRename(kind, id) {
    closeMenu();
    const selector = `[data-kind="${kind}"][data-id="${id}"]`;
    const host = $(`#lib-content ${selector} [data-rename]`) || $(`#sb-tree ${selector} [data-rename]`);
    const item = kind === 'book' ? state.bookById.get(id) : state.folderById.get(id);
    if (!item) return;
    if (!host) {
        if (kind === 'book') {
            openDrawer(id);
            requestAnimationFrame(() => {
                const title = $('#drawer-root [data-focus-key="title"]');
                if (title) {
                    title.focus();
                    title.select();
                }
            });
        }
        return;
    }
    const current = kind === 'book' ? item.title : item.name;
    state.renaming = { kind, id };
    host.innerHTML = `<input class="rename-input" type="text" maxlength="${kind === 'book' ? 300 : 120}" aria-label="Nuovo nome">`;
    const input = host.querySelector('input');
    input.value = current;
    input.focus();
    input.select();
    const card = host.closest('[data-kind]');
    if (card) card.scrollIntoView({ block: 'nearest' });

    let finished = false;
    const finish = async (save) => {
        if (finished) return;
        finished = true;
        state.renaming = null;
        const value = input.value.replace(/\s+/g, ' ').trim();
        invalidate();
        if (!save || !value || value === current) {
            render();
            return;
        }
        if (kind === 'book') item.title = value;
        else item.name = value;
        reindex();
        render();
        const url = kind === 'book' ? `/api/library/books/${id}` : `/api/library/folders/${id}`;
        const done = await run(() => api('PATCH', url, kind === 'book' ? { title: value } : { name: value }));
        if (done) toast('Nome aggiornato', { type: 'success', duration: 2200 });
    };
    input.addEventListener('keydown', (event) => {
        event.stopPropagation();
        if (event.key === 'Enter') {
            event.preventDefault();
            finish(true);
        } else if (event.key === 'Escape') {
            event.preventDefault();
            finish(false);
        }
    });
    input.addEventListener('blur', () => finish(true));
    for (const type of ['click', 'pointerdown', 'dblclick']) input.addEventListener(type, e => e.stopPropagation());
}

async function moveItems(items, targetId, options = {}) {
    targetId = targetId || null;
    const moving = items.filter(item => parentOf(item) !== targetId);
    if (!moving.length) return;
    const previous = moving.map(item => ({ ...item, parent: parentOf(item) }));
    for (const item of moving) {
        if (item.kind === 'book') {
            const book = state.bookById.get(item.id);
            if (book) book.folder_id = targetId;
        } else {
            const folder = state.folderById.get(item.id);
            if (folder) folder.parent_id = targetId;
        }
    }
    reindex();
    render();
    const ok = await run(() => api('POST', '/api/library/move', {
        book_ids: moving.filter(i => i.kind === 'book').map(i => i.id),
        folder_ids: moving.filter(i => i.kind === 'folder').map(i => i.id),
        target_id: targetId,
    }));
    if (!ok) return;
    clearSelection();
    await refresh();
    if (options.silent) return;
    const destination = targetId ? `«${(state.folderById.get(targetId) || {}).name || ''}»` : 'Cartelle (fuori dalle cartelle)';
    toast(`${describeItems(moving)} → ${destination}`, {
        action: 'Annulla',
        onAction: () => undoMove(previous),
    });
}

async function undoMove(previous) {
    const groups = new Map();
    for (const item of previous) push(groups, item.parent || '', item);
    for (const [parent, list] of groups) {
        await run(() => api('POST', '/api/library/move', {
            book_ids: list.filter(i => i.kind === 'book').map(i => i.id),
            folder_ids: list.filter(i => i.kind === 'folder').map(i => i.id),
            target_id: parent || null,
        }));
    }
    await refresh();
    toast('Spostamento annullato', { type: 'success', duration: 2200 });
}

function setSort(key) {
    state.sort = key;
    store.set('sort', key);
    render();
}

async function reorderItems(items, target) {
    const view = state.lastView;
    if (!view) return;
    const kind = target.kind;
    const list = (kind === 'book' ? view.books : view.folders).map(x => x.id);
    const movingSet = new Set(items.filter(i => i.kind === kind).map(i => i.id));
    const moving = list.filter(id => movingSet.has(id));
    if (!moving.length) return;
    const index = target.id === null ? list.length : list.indexOf(target.id) + (target.after ? 1 : 0);
    const order = [
        ...list.slice(0, index).filter(id => !movingSet.has(id)),
        ...moving,
        ...list.slice(index).filter(id => !movingSet.has(id)),
    ];
    if (order.every((id, i) => id === list[i])) return;
    const map = kind === 'book' ? state.bookById : state.folderById;
    order.forEach((id, i) => {
        const obj = map.get(id);
        if (obj) obj.position = (i + 1) * 1024;
    });
    if (state.sort !== 'manual') {
        state.sort = 'manual';
        store.set('sort', 'manual');
        toast('Ordine personalizzato attivato: ora puoi disporre gli elementi come vuoi');
    }
    reindex();
    render();
    await run(() => api('POST', '/api/library/reorder', { kind, parent_id: view.container, ids: order }));
}

async function setColor(items, color) {
    const books = items.filter(i => i.kind === 'book').map(i => i.id);
    const folders = items.filter(i => i.kind === 'folder').map(i => i.id);
    for (const id of books) {
        const book = state.bookById.get(id);
        if (book) book.color = color;
    }
    for (const id of folders) {
        const folder = state.folderById.get(id);
        if (folder) folder.color = color;
    }
    reindex();
    render();
    await run(async () => {
        if (books.length) await api('POST', '/api/library/books/update', { book_ids: books, color });
        await Promise.all(folders.map(id => api('PATCH', `/api/library/folders/${id}`, { color })));
    });
}

async function setFavorite(bookIds, favorite) {
    if (!bookIds.length) return;
    for (const id of bookIds) {
        const book = state.bookById.get(id);
        if (book) book.favorite = favorite;
    }
    reindex();
    render();
    const ok = await run(() => api('POST', '/api/library/books/update', { book_ids: bookIds, favorite }));
    if (ok) toast(favorite ? `${plural(bookIds.length, 'libro aggiunto', 'libri aggiunti')} ai preferiti` : 'Rimosso dai preferiti', { type: 'success', duration: 2400 });
}

async function addTags(bookIds, names) {
    names = names.map(n => n.replace(/\s+/g, ' ').trim()).filter(Boolean);
    if (!bookIds.length || !names.length) return false;
    const ok = await run(() => api('POST', '/api/library/books/tags', { book_ids: bookIds, add: names }));
    if (ok) await refresh();
    return Boolean(ok);
}

async function removeTag(bookIds, tagId) {
    for (const id of bookIds) {
        const book = state.bookById.get(id);
        if (book) book.tags = book.tags.filter(t => t.id !== tagId);
    }
    reindex();
    render();
    const ok = await run(() => api('POST', '/api/library/books/tags', { book_ids: bookIds, remove: [tagId] }));
    if (ok) await refresh();
}

async function retag(bookId) {
    const book = state.bookById.get(bookId);
    if (!book) return;
    const ok = await run(() => api('POST', `/api/library/books/${bookId}/retag`));
    if (!ok) return;
    book.tag_status = 'pending';
    render();
    toast('Rianalisi del libro in corso: i tag automatici verranno aggiornati');
    schedulePoll(1500);
}

async function trashItems(items) {
    const book_ids = items.filter(i => i.kind === 'book').map(i => i.id);
    const folder_ids = items.filter(i => i.kind === 'folder').map(i => i.id);
    if (!book_ids.length && !folder_ids.length) return;
    const label = describeItems(items);
    const result = await run(() => api('POST', '/api/library/trash', { book_ids, folder_ids }));
    if (!result) return;
    state.selection.clear();
    if (state.drawerId && book_ids.includes(state.drawerId)) state.drawerId = null;
    await refresh();
    toast(`${label} ${items.length === 1 ? 'spostato' : 'spostati'} nel cestino`, {
        action: 'Annulla',
        onAction: async () => {
            const ok = await run(() => api('POST', '/api/library/trash/restore', { trash_id: result.trash_id }));
            if (ok) {
                await refresh();
                toast('Ripristinato', { type: 'success', duration: 2200 });
            }
        },
    });
}

function downloadBook(id) {
    const link = document.createElement('a');
    link.href = `/api/library/books/${id}/download`;
    link.download = '';
    document.body.appendChild(link);
    link.click();
    link.remove();
}

function translateBook(id) {
    window.location.href = `/?book=${encodeURIComponent(id)}`;
}

function toggleTagFilter(tagId) {
    const tags = state.filters.tags;
    state.filters.tags = tags.includes(tagId) ? tags.filter(id => id !== tagId) : [...tags, tagId];
    render();
}

function clearFilters() {
    state.filters = { q: '', tags: [], format: null, color: null };
    el.search.value = '';
    render();
}

// ---------------------------------------------------------------------------
// Menus
// ---------------------------------------------------------------------------
let openMenuEl = null;
let menuOpenedAt = 0;

function colorSwatchesHTML(current, allowNone = true, noneLabel = 'Nessun colore') {
    return `<div class="swatches">
        ${allowNone ? `<button class="swatch none${!current ? ' is-active' : ''}" data-color="" title="${esc(noneLabel)}" aria-label="${esc(noneLabel)}"></button>` : ''}
        ${COLORS.map(c => `<button class="swatch${current === c.key ? ' is-active' : ''}" data-color="${c.key}" style="--sw:${c.hex}" title="${c.name}" aria-label="${c.name}"></button>`).join('')}
    </div>`;
}

function openMenu(entries, anchor) {
    closeMenu();
    const menu = document.createElement('div');
    menu.className = 'ctx-menu';
    menu.setAttribute('role', 'menu');
    const actions = [];
    menu.innerHTML = entries.filter(Boolean).map(entry => {
        if (entry.separator) return '<div class="ctx-sep" role="separator"></div>';
        if (entry.heading) return `<div class="ctx-heading">${esc(entry.heading)}</div>`;
        if (entry.colors) {
            actions.push(entry.onPick);
            return `<div class="ctx-colors" data-colors="${actions.length - 1}">${colorSwatchesHTML(entry.current, true, entry.noneLabel)}</div>`;
        }
        actions.push(entry.action);
        return `<button class="ctx-item${entry.danger ? ' danger' : ''}" role="menuitem" data-index="${actions.length - 1}"${entry.disabled ? ' disabled' : ''}>
            ${entry.icon ? icon(entry.icon, 17) : ''}<span>${esc(entry.label)}</span>
            ${entry.checked ? `<span class="check">${icon('check', 15)}</span>` : ''}${entry.hint ? `<span class="hint">${esc(entry.hint)}</span>` : ''}
        </button>`;
    }).join('');
    document.body.appendChild(menu);

    const rect = menu.getBoundingClientRect();
    let x;
    let y;
    if (anchor instanceof Element) {
        const r = anchor.getBoundingClientRect();
        x = r.right - rect.width;
        y = r.bottom + 6;
        if (x < 8) x = r.left;
        if (y + rect.height > window.innerHeight - 8) y = r.top - rect.height - 6;
    } else {
        // Never open right under the pointer: a finger lifting after a long-press must not hit an item
        const gap = 6;
        x = anchor.x + gap;
        y = anchor.y + gap;
        if (x + rect.width > window.innerWidth - 8) x = anchor.x - rect.width - gap;
        if (y + rect.height > window.innerHeight - 8) y = anchor.y - rect.height - gap;
    }
    x = Math.max(8, Math.min(x, window.innerWidth - rect.width - 8));
    y = Math.max(8, Math.min(y, window.innerHeight - rect.height - 8));
    if (!(anchor instanceof Element) && anchor.x >= x && anchor.x <= x + rect.width && anchor.y >= y && anchor.y <= y + rect.height) {
        // Clamping put the menu under the pointer (small screens): move it fully above or below
        y = anchor.y > window.innerHeight / 2 ? Math.max(8, anchor.y - rect.height - 6) : anchor.y + 6;
    }
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;

    menu.addEventListener('click', (event) => {
        const swatch = event.target.closest('.swatch');
        if (swatch) {
            const group = swatch.closest('[data-colors]');
            const pick = actions[Number(group.dataset.colors)];
            closeMenu();
            pick(swatch.dataset.color || null);
            return;
        }
        const item = event.target.closest('.ctx-item');
        if (item && !item.disabled) {
            const action = actions[Number(item.dataset.index)];
            closeMenu();
            if (action) action();
        }
    });
    openMenuEl = menu;
    menuOpenedAt = performance.now();
    const first = menu.querySelector('.ctx-item:not(:disabled)');
    if (first && !(anchor instanceof Element && anchor.matches('[data-kind]'))) first.focus({ preventScroll: true });
}

function closeMenu() {
    if (openMenuEl) {
        openMenuEl.remove();
        openMenuEl = null;
    }
}

function menuKeydown(event) {
    const items = $$('.ctx-item:not(:disabled)', openMenuEl);
    const index = items.indexOf(document.activeElement);
    if (event.key === 'Escape') {
        event.preventDefault();
        closeMenu();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const step = event.key === 'ArrowDown' ? 1 : -1;
        const next = items[(index + step + items.length) % items.length];
        if (next) next.focus();
    } else if (event.key === 'Tab') {
        closeMenu();
    }
}

function openItemMenu(kind, id, anchor) {
    const key = keyOf(kind, id);
    if (state.selection.size > 1 && state.selection.has(key)) {
        openSelectionMenu(anchor);
        return;
    }
    if (kind === 'book') openBookMenu(id, anchor);
    else openFolderMenu(id, anchor);
}

function openBookMenu(id, anchor) {
    const book = state.bookById.get(id);
    if (!book) return;
    const item = [{ kind: 'book', id }];
    const selected = state.selection.has(keyOf('book', id));
    openMenu([
        { label: 'Apri dettagli', icon: 'eye', action: () => openDrawer(id) },
        { label: selected ? 'Deseleziona' : 'Seleziona', icon: 'select', action: () => toggleSelect(keyOf('book', id)) },
        { label: 'Traduci', icon: 'zap', action: () => translateBook(id) },
        { label: 'Scarica', icon: 'download', action: () => downloadBook(id) },
        { separator: true },
        { label: 'Rinomina', icon: 'edit', hint: 'F2', action: () => startRename('book', id) },
        { label: 'Sposta in...', icon: 'move', action: () => openMovePicker(item) },
        { label: book.favorite ? 'Togli dai preferiti' : 'Aggiungi ai preferiti', icon: book.favorite ? 'starFill' : 'star', action: () => setFavorite([id], !book.favorite) },
        { label: 'Gestisci tag...', icon: 'tag', action: () => openDrawer(id, true) },
        { heading: 'Colore' },
        { colors: true, current: book.color, onPick: color => setColor(item, color) },
        { separator: true },
        { label: 'Rigenera tag automatici', icon: 'sparkles', action: () => retag(id) },
        { label: 'Sposta nel cestino', icon: 'trash', danger: true, hint: 'Canc', action: () => trashItems(item) },
    ], anchor);
}

function openFolderMenu(id, anchor) {
    const folder = state.folderById.get(id);
    if (!folder) return;
    const item = [{ kind: 'folder', id }];
    const selected = state.selection.has(keyOf('folder', id));
    openMenu([
        { label: 'Apri', icon: 'folderOpen', action: () => go({ type: 'folder', id }) },
        { label: selected ? 'Deseleziona' : 'Seleziona', icon: 'select', action: () => toggleSelect(keyOf('folder', id)) },
        { label: 'Rinomina', icon: 'edit', hint: 'F2', action: () => startRename('folder', id) },
        { label: 'Nuova sottocartella', icon: 'folderPlus', action: () => createFolder(id) },
        { label: 'Carica libri qui', icon: 'upload', action: () => pickFiles(id) },
        { label: 'Carica una cartella qui', icon: 'folderUp', action: () => pickFolder(id) },
        { label: 'Sposta in...', icon: 'move', action: () => openMovePicker(item) },
        { heading: 'Colore' },
        { colors: true, current: folder.color, onPick: color => setColor(item, color) },
        { separator: true },
        { label: 'Sposta nel cestino', icon: 'trash', danger: true, hint: 'Canc', action: () => trashItems(item) },
    ], anchor);
}

function openSelectionMenu(anchor) {
    const items = selectedItems();
    const bookIds = items.filter(i => i.kind === 'book').map(i => i.id);
    const allFavorite = bookIds.length && bookIds.every(id => (state.bookById.get(id) || {}).favorite);
    openMenu([
        { heading: `${plural(items.length, 'elemento selezionato', 'elementi selezionati')}` },
        { label: 'Sposta in...', icon: 'move', action: () => openMovePicker(items) },
        bookIds.length ? { label: allFavorite ? 'Togli dai preferiti' : 'Aggiungi ai preferiti', icon: 'star', action: () => setFavorite(bookIds, !allFavorite) } : null,
        bookIds.length ? { label: 'Gestisci tag...', icon: 'tag', action: () => openTagModal(bookIds) } : null,
        { heading: 'Colore' },
        { colors: true, current: null, onPick: color => setColor(items, color) },
        { separator: true },
        { label: 'Sposta nel cestino', icon: 'trash', danger: true, hint: 'Canc', action: () => trashItems(items) },
    ], anchor);
}

function openContentMenu(point) {
    const parent = currentFolderId();
    openMenu([
        { label: 'Nuova cartella', icon: 'folderPlus', action: () => createFolder(parent) },
        { label: 'Carica libri', icon: 'upload', action: () => pickFiles(parent) },
        { label: 'Carica una cartella', icon: 'folderUp', action: () => pickFolder(parent) },
        { separator: true },
        { label: 'Seleziona tutto', icon: 'select', hint: 'Ctrl+A', action: selectAllVisible },
    ], point);
}

function openTagMenu(tagId, anchor) {
    const tag = state.tagById.get(tagId);
    if (!tag) return;
    openMenu([
        { label: 'Mostra i libri con questo tag', icon: 'books', action: () => showTag(tagId) },
        { label: 'Rinomina tag...', icon: 'edit', action: () => renameTag(tagId) },
        { heading: 'Colore' },
        { colors: true, current: tag.color, onPick: color => recolorTag(tagId, color) },
        { separator: true },
        { label: 'Elimina tag', icon: 'trash', danger: true, action: () => deleteTag(tagId) },
    ], anchor);
}

function showTag(tagId) {
    go({ type: 'all' });
    state.filters.tags = [tagId];
    render();
}

async function renameTag(tagId) {
    const tag = state.tagById.get(tagId);
    const name = await promptDialog({ title: 'Rinomina tag', label: 'Nome del tag', value: tag.name, confirm: 'Rinomina' });
    if (!name || name === tag.name) return;
    const data = await run(() => api('PATCH', `/api/library/tags/${tagId}`, { name }));
    if (!data) return;
    if (data.tag.merged) {
        state.filters.tags = state.filters.tags.map(id => (id === tagId ? data.tag.id : id));
        toast(`Tag unito a «${data.tag.name}»`, { type: 'success' });
    }
    await refresh();
}

async function recolorTag(tagId, color) {
    const tag = state.tagById.get(tagId);
    if (tag && color) tag.color = color;
    render();
    await run(() => api('PATCH', `/api/library/tags/${tagId}`, { color }));
    await refresh();
}

async function deleteTag(tagId) {
    const tag = state.tagById.get(tagId);
    const ok = await confirmDialog({
        title: 'Eliminare il tag?',
        message: `Il tag «${tag.name}» verrà tolto da ${plural(tag.count, 'libro', 'libri')}. I libri non verranno eliminati.`,
        confirm: 'Elimina tag',
        danger: true,
    });
    if (!ok) return;
    const done = await run(() => api('DELETE', `/api/library/tags/${tagId}`));
    if (done) await refresh();
}

function openUploadMenu(anchor) {
    const target = currentFolderId();
    openMenu([
        { label: 'Carica libri (EPUB, PDF)', icon: 'upload', action: () => pickFiles(target) },
        { label: 'Carica una cartella', icon: 'folderUp', action: () => pickFolder(target) },
        { separator: true },
        { label: 'Nuova cartella', icon: 'folderPlus', action: () => createFolder(target) },
    ], anchor);
}

function openSortMenu(anchor) {
    openMenu([
        { heading: 'Ordina per' },
        ...SORTS.map(sort => ({ label: sort.label, checked: state.sort === sort.key, action: () => setSort(sort.key) })),
    ], anchor);
}

function openFilterMenu(anchor) {
    const filters = state.filters;
    const setFilter = (key, value) => {
        state.filters[key] = value;
        render();
    };
    openMenu([
        { heading: 'Formato' },
        { label: 'Tutti i formati', checked: !filters.format, action: () => setFilter('format', null) },
        { label: 'Solo EPUB', checked: filters.format === 'epub', action: () => setFilter('format', 'epub') },
        { label: 'Solo PDF', checked: filters.format === 'pdf', action: () => setFilter('format', 'pdf') },
        { heading: 'Colore' },
        { colors: true, current: filters.color, noneLabel: 'Qualsiasi colore', onPick: color => setFilter('color', color) },
    ], anchor);
}

// ---------------------------------------------------------------------------
// Modals
// ---------------------------------------------------------------------------
const modalStack = [];

function openModal({ title, body, actions = [], wide = false, onOpen, onClose }) {
    closeMenu();
    const wrap = document.createElement('div');
    wrap.className = 'modal-backdrop';
    wrap.innerHTML = `<div class="modal${wide ? ' is-wide' : ''}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
        <div class="modal-head"><h2>${esc(title)}</h2><button class="icon-btn" data-modal-close aria-label="Chiudi">${icon('x')}</button></div>
        <div class="modal-body">${body}</div>
        ${actions.length ? `<div class="modal-foot">${actions.map((a, i) => `<button class="btn ${a.primary ? 'btn-primary' : a.danger ? 'btn-danger' : 'btn-ghost'}" data-modal-action="${i}">${esc(a.label)}</button>`).join('')}</div>` : ''}
    </div>`;
    el.modalRoot.appendChild(wrap);
    let closed = false;
    const modal = {
        el: wrap,
        close(result) {
            if (closed) return;
            closed = true;
            const index = modalStack.indexOf(modal);
            if (index !== -1) modalStack.splice(index, 1);
            wrap.classList.add('is-closing');
            setTimeout(() => wrap.remove(), 160);
            if (onClose) onClose(result);
        },
    };
    modalStack.push(modal);
    wrap.addEventListener('pointerdown', (event) => { wrap._downOnBackdrop = event.target === wrap; });
    wrap.addEventListener('click', async (event) => {
        if ((event.target === wrap && wrap._downOnBackdrop) || event.target.closest('[data-modal-close]')) {
            modal.close();
            return;
        }
        const button = event.target.closest('[data-modal-action]');
        if (button) {
            const action = actions[Number(button.dataset.modalAction)];
            const result = action.action ? await action.action(modal) : undefined;
            if (result !== false) modal.close(action.value);
        }
    });
    if (onOpen) onOpen(modal);
    const focusTarget = wrap.querySelector('input, textarea') || wrap.querySelector('.btn-primary, .btn-danger') || wrap.querySelector('button');
    if (focusTarget) setTimeout(() => focusTarget.focus(), 30);
    return modal;
}

function confirmDialog({ title, message, confirm = 'Conferma', danger = false }) {
    return new Promise((resolve) => {
        openModal({
            title,
            body: `<p>${esc(message)}</p>`,
            actions: [
                { label: 'Annulla', value: false },
                { label: confirm, primary: !danger, danger, value: true },
            ],
            onClose: result => resolve(Boolean(result)),
        });
    });
}

function promptDialog({ title, label, value = '', confirm = 'Salva', placeholder = '' }) {
    return new Promise((resolve) => {
        let input;
        openModal({
            title,
            body: `<label class="field-label" for="prompt-input">${esc(label)}</label><input class="text-input" id="prompt-input" maxlength="120" placeholder="${esc(placeholder)}">`,
            actions: [
                { label: 'Annulla', value: null },
                { label: confirm, primary: true, action: (modal) => { modal.close(input.value.trim() || null); return false; } },
            ],
            onOpen: (modal) => {
                input = modal.el.querySelector('#prompt-input');
                input.value = value;
                setTimeout(() => input.select(), 40);
                input.addEventListener('keydown', (event) => {
                    if (event.key === 'Enter') {
                        event.preventDefault();
                        modal.close(input.value.trim() || null);
                    }
                });
            },
            onClose: result => resolve(result || null),
        });
    });
}

function openMovePicker(items) {
    items = items.filter(i => (i.kind === 'book' ? state.bookById.has(i.id) : state.folderById.has(i.id)));
    if (!items.length) return;
    const blocked = new Set();
    for (const item of items) if (item.kind === 'folder') for (const id of subtreeIds(item.id)) blocked.add(id);
    const parents = new Set(items.map(parentOf));
    const currentParent = parents.size === 1 ? [...parents][0] : undefined;
    let selected = currentParent === null ? null : (currentParent || null);
    let query = '';
    let modalRef = null;

    const rowHTML = (folder, depth) => {
        const disabled = blocked.has(folder.id);
        const here = currentParent === folder.id;
        return `<button class="picker-row${selected === folder.id ? ' is-selected' : ''}" data-pick="${folder.id}" style="--depth:${depth};--item-color:${folderColor(folder)}"${disabled ? ' disabled' : ''}>
            ${icon('folderFill', 17)}<span>${esc(folder.name)}</span>${here ? '<span class="here">posizione attuale</span>' : ''}
        </button>`;
    };
    const matches = (folder) => !query || fold(folder.name).includes(query)
        || (state.childFolders.get(folder.id) || []).some(matches);
    const tree = (parentKey, depth) => sortFolders(state.childFolders.get(parentKey) || [])
        .filter(matches)
        .map(folder => rowHTML(folder, depth) + tree(folder.id, depth + 1))
        .join('');
    const targetName = () => (selected ? `«${state.folderById.get(selected).name}»` : 'Cartelle');
    const paint = () => {
        const root = modalRef.el;
        root.querySelector('.picker-tree').innerHTML = `
            <button class="picker-row${selected === null ? ' is-selected' : ''}" data-pick="">${icon('books', 17)}<span>Cartelle (nessuna cartella)</span>${currentParent === null ? '<span class="here">posizione attuale</span>' : ''}</button>
            ${tree('', 1) || '<div class="muted-note" style="padding:8px 10px">Nessuna cartella trovata</div>'}`;
        const confirm = root.querySelector('[data-modal-action="1"]');
        confirm.textContent = `Sposta in ${targetName()}`;
        confirm.disabled = selected === currentParent;
    };

    modalRef = openModal({
        title: `Sposta ${describeItems(items)}`,
        wide: true,
        body: `<div class="picker-search"><input class="text-input" id="picker-search" placeholder="Cerca una cartella..." autocomplete="off"></div>
            <div class="picker-tree"></div>
            <div class="row-actions"><button class="btn btn-sm btn-ghost" data-new-folder>${icon('folderPlus', 15)}Nuova cartella qui</button></div>`,
        actions: [
            { label: 'Annulla' },
            { label: 'Sposta', primary: true, action: () => { moveItems(items, selected); } },
        ],
        onOpen: (modal) => {
            modalRef = modal;
            const root = modal.el;
            root.querySelector('#picker-search').addEventListener('input', (event) => {
                query = fold(event.target.value.trim());
                paint();
            });
            root.querySelector('.picker-tree').addEventListener('click', (event) => {
                const row = event.target.closest('[data-pick]');
                if (!row || row.disabled) return;
                selected = row.dataset.pick || null;
                paint();
            });
            root.querySelector('.picker-tree').addEventListener('dblclick', (event) => {
                const row = event.target.closest('[data-pick]');
                if (!row || row.disabled) return;
                selected = row.dataset.pick || null;
                if (selected !== currentParent) {
                    modal.close();
                    moveItems(items, selected);
                }
            });
            root.querySelector('[data-new-folder]').addEventListener('click', async () => {
                const name = await promptDialog({ title: 'Nuova cartella', label: `Dentro ${targetName()}`, value: uniqueFolderName(selected), confirm: 'Crea' });
                if (!name) return;
                const data = await run(() => api('POST', '/api/library/folders', { name, parent_id: selected }));
                if (!data) return;
                await refresh();
                selected = data.folder.id;
                paint();
            });
            paint();
        },
    });
}

function openTagModal(bookIds) {
    let modalRef = null;
    const paint = () => {
        const counts = new Map();
        for (const id of bookIds) {
            for (const t of (state.bookById.get(id) || { tags: [] }).tags) counts.set(t.id, (counts.get(t.id) || 0) + 1);
        }
        const tags = [...state.tags].sort((a, b) => (counts.get(b.id) || 0) - (counts.get(a.id) || 0) || b.count - a.count || collator.compare(a.name, b.name));
        modalRef.el.querySelector('.bulk-tags').innerHTML = tags.length ? tags.map(tag => {
            const n = counts.get(tag.id) || 0;
            const stateClass = n === bookIds.length ? 'all' : n ? 'some' : '';
            return `<button class="bulk-tag-row" data-bulk-tag="${tag.id}">
                <span class="tri ${stateClass}">${stateClass === 'all' ? icon('check', 13) : stateClass === 'some' ? '&minus;' : ''}</span>
                <span class="tag-dot" style="--tag-color:${tagColor(tag)}"></span><span>${esc(tag.name)}</span>
                <span class="count">${n ? `${n}/${bookIds.length}` : ''}</span>
            </button>`;
        }).join('') : '<p class="muted-note">Non ci sono ancora tag: scrivine uno qui sopra.</p>';
    };
    modalRef = openModal({
        title: `Tag per ${plural(bookIds.length, 'libro', 'libri')}`,
        body: `<div class="input-row"><input class="text-input" id="bulk-tag-input" placeholder="Nuovo tag, es. marketing" maxlength="40" autocomplete="off"><button class="btn btn-primary" data-bulk-add>Aggiungi</button></div>
            <div class="bulk-tags"></div>`,
        actions: [{ label: 'Fatto', primary: true }],
        onOpen: (modal) => {
            modalRef = modal;
            const input = modal.el.querySelector('#bulk-tag-input');
            const add = async () => {
                const name = input.value.trim();
                if (!name) return;
                input.value = '';
                await addTags(bookIds, [name]);
                paint();
            };
            modal.el.querySelector('[data-bulk-add]').addEventListener('click', add);
            input.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    add();
                }
            });
            modal.el.querySelector('.bulk-tags').addEventListener('click', async (event) => {
                const row = event.target.closest('[data-bulk-tag]');
                if (!row) return;
                const tag = state.tagById.get(row.dataset.bulkTag);
                const all = bookIds.every(id => (state.bookById.get(id) || { tags: [] }).tags.some(t => t.id === tag.id));
                if (all) await removeTag(bookIds, tag.id);
                else await addTags(bookIds, [tag.name]);
                paint();
            });
            paint();
        },
    });
}

async function openKeyModal() {
    store.set('keySeen', true);
    const data = await run(() => api('GET', '/api/library/key'));
    if (!data) return;
    const link = () => `${location.origin}/libreria?chiave=${data.code.replace(/-/g, '')}`;
    openModal({
        title: 'La tua libreria privata',
        wide: true,
        body: `<p>La libreria è collegata a questo browser e nessun altro può vederla. Con questo codice puoi aprirla anche su telefono, tablet o un altro computer.</p>
            <label class="field-label">Codice della libreria</label>
            <div class="code-box"><span class="code-value" id="library-code">${esc(data.code)}</span><button class="btn btn-sm" data-copy="code">${icon('copy', 15)}Copia</button></div>
            <div class="row-actions"><button class="btn btn-sm" data-copy="link">${icon('link', 15)}Copia link di accesso</button></div>
            <div class="warn-note">${icon('alert', 18)}<span>Chi ha il codice può vedere e modificare la tua libreria: condividilo solo con i tuoi dispositivi.</span></div>
            <div class="modal-divider"></div>
            <label class="field-label" for="open-code">Apri un'altra libreria</label>
            <div class="input-row"><input class="text-input" id="open-code" placeholder="XXXX-XXXX-XXXX-XXXX-XXXX" autocomplete="off" spellcheck="false"><button class="btn btn-primary" data-open-code>Apri</button></div>
            <div class="modal-divider"></div>
            <p class="muted-note">Hai condiviso il codice per errore? <button class="link-btn" data-rotate>Genera un nuovo codice</button>: gli altri dispositivi verranno scollegati.</p>`,
        onOpen: (modal) => {
            const root = modal.el;
            root.addEventListener('click', async (event) => {
                const copy = event.target.closest('[data-copy]');
                if (copy) {
                    const ok = await copyText(copy.dataset.copy === 'link' ? link() : data.code);
                    toast(ok ? (copy.dataset.copy === 'link' ? 'Link copiato' : 'Codice copiato') : 'Copia non riuscita: seleziona il testo a mano', { type: ok ? 'success' : 'error', duration: 2200 });
                }
                if (event.target.closest('[data-open-code]')) {
                    const value = root.querySelector('#open-code').value;
                    if (!value.trim()) return;
                    const opened = await run(() => api('POST', '/api/library/key', { code: value }));
                    if (!opened) return;
                    modal.close();
                    state.selection.clear();
                    state.drawerId = null;
                    await refresh();
                    go({ type: 'folder', id: null });
                    toast(`Libreria aperta: ${plural(opened.books, 'libro', 'libri')}`, { type: 'success' });
                }
                if (event.target.closest('[data-rotate]')) {
                    const ok = await confirmDialog({
                        title: 'Generare un nuovo codice?',
                        message: 'Il codice attuale smetterà di funzionare e gli altri dispositivi collegati non vedranno più questa libreria. Questo browser resta collegato.',
                        confirm: 'Genera nuovo codice',
                        danger: true,
                    });
                    if (!ok) return;
                    const rotated = await run(() => api('POST', '/api/library/key/rotate'));
                    if (!rotated) return;
                    data.code = rotated.code;
                    root.querySelector('#library-code').textContent = rotated.code;
                    toast('Nuovo codice generato', { type: 'success' });
                }
            });
            root.querySelector('#open-code').addEventListener('keydown', (event) => {
                if (event.key === 'Enter') root.querySelector('[data-open-code]').click();
            });
        },
    });
    render();
}

async function handleLinkedLibrary(code) {
    const clean = () => history.replaceState(null, '', location.pathname + location.hash);
    let current = null;
    try { current = await api('GET', '/api/library/key'); } catch (_) { current = null; }
    if (current && current.code.replace(/-/g, '').toUpperCase() === String(code).replace(/-/g, '').toUpperCase()) {
        clean();
        return;
    }
    const warn = state.books.length && current
        ? `<div class="warn-note">${icon('alert', 18)}<span>Questo browser ha già una libreria con ${plural(state.books.length, 'libro', 'libri')}. Per ritrovarla in futuro salva il suo codice: <strong>${esc(current.code)}</strong></span></div>`
        : '';
    openModal({
        title: 'Aprire la libreria collegata?',
        body: `<p>Hai aperto un link di accesso a una libreria. Aprila solo se è tua o se ti fidi di chi te l'ha inviato.</p>${warn}`,
        actions: [
            { label: 'Annulla', action: () => { clean(); } },
            {
                label: 'Apri libreria',
                primary: true,
                action: async () => {
                    const opened = await run(() => api('POST', '/api/library/key', { code }));
                    clean();
                    if (!opened) return;
                    await refresh();
                    go({ type: 'folder', id: null }, { replace: true });
                    toast(`Libreria aperta: ${plural(opened.books, 'libro', 'libri')}`, { type: 'success' });
                },
            },
        ],
        onClose: clean,
    });
}

// ---------------------------------------------------------------------------
// Drawer (book details)
// ---------------------------------------------------------------------------
let drawerCloseTimer = null;

function openDrawer(id, focusTags = false) {
    state.drawerId = id;
    render();
    if (focusTags) {
        requestAnimationFrame(() => {
            const input = $('#drawer-root .tag-input');
            if (input) input.focus();
        });
    }
}

function closeDrawer() {
    if (!state.drawerId) return;
    state.drawerId = null;
    render();
}

function drawerHTML(book) {
    const path = book.folder_id ? folderPath(book.folder_id) : [];
    const folder = path[path.length - 1];
    const tagging = isTagging(book);
    const tags = book.tags.map(t => ({ tag: state.tagById.get(t.id), source: t.source })).filter(t => t.tag);
    const hasAuto = tags.some(t => t.source === 'auto');
    const pagesLabel = book.num_pages ? ['Pagine', formatNumber(book.num_pages)] : ['Capitoli', formatNumber(book.num_chapters || 0)];
    const translations = [...book.translations].sort((a, b) => b.created_at - a.created_at);
    const details = [
        ['Formato', book.file_type.toUpperCase()],
        ['Dimensione', formatSize(book.file_size)],
        pagesLabel,
        ['Parole', formatNumber(book.total_words)],
        ['Token stimati', formatNumber(book.estimated_tokens)],
        ['Lingua', book.language ? langName(book.language) : 'Non rilevata'],
        ['Aggiunto il', formatDate(book.created_at)],
        ['File originale', book.original_filename],
    ];
    return `<div class="drawer-backdrop" data-action="close-drawer"></div>
    <aside class="drawer" role="dialog" aria-label="Dettagli del libro">
        <div class="drawer-head">
            <span class="drawer-head-label">Dettagli libro</span>
            <div>
                <button class="icon-btn" data-action="drawer-menu" aria-label="Altre azioni">${icon('more', 18)}</button>
                <button class="icon-btn" data-action="close-drawer" aria-label="Chiudi">${icon('x', 20)}</button>
            </div>
        </div>
        <div class="drawer-body">
            <div class="drawer-hero">
                <div class="drawer-cover">${coverHTML(book)}</div>
                <div class="drawer-titles">
                    <textarea class="drawer-title-input" rows="1" maxlength="300" data-focus-key="title" data-field="title" aria-label="Titolo" spellcheck="false">${esc(book.title)}</textarea>
                    <input class="drawer-author-input" data-focus-key="author" data-field="author" placeholder="Aggiungi autore" value="${esc(book.author || '')}" maxlength="200" aria-label="Autore">
                    <div class="drawer-subline">
                        <span class="meta-pill">${book.file_type.toUpperCase()}</span>
                        <span class="meta-pill">${formatSize(book.file_size)}</span>
                        ${book.language ? `<span class="meta-pill">${icon('globe', 12)}${esc(langName(book.language))}</span>` : ''}
                    </div>
                </div>
            </div>
            <div class="drawer-actions">
                <button class="btn btn-gold" data-action="drawer-translate">${icon('zap', 17)}Traduci</button>
                <button class="btn" data-action="drawer-download">${icon('download', 17)}Scarica</button>
                <button class="icon-btn${book.favorite ? ' is-fav' : ''}" data-action="drawer-favorite" aria-label="${book.favorite ? 'Togli dai preferiti' : 'Aggiungi ai preferiti'}" title="${book.favorite ? 'Togli dai preferiti' : 'Aggiungi ai preferiti'}">${icon(book.favorite ? 'starFill' : 'star', 18)}</button>
            </div>

            <section class="drawer-section">
                <h4>Tag ${hasAuto ? `<span class="note">${icon('sparkles', 11)} = assegnato automaticamente</span>` : ''}</h4>
                <div class="tag-editor">
                    ${tags.map(({ tag, source }) => `<span class="tag-chip" style="--tag-color:${tagColor(tag)}">${source === 'auto' ? `<span class="spark" title="Assegnato automaticamente">${icon('sparkles', 11)}</span>` : ''}${esc(tag.name)}<button class="x" data-action="drawer-remove-tag" data-tag-id="${tag.id}" aria-label="Rimuovi tag ${esc(tag.name)}">${icon('x', 11)}</button></span>`).join('')}
                    ${tagging ? `<span class="ai-pending">${icon('sparkles', 11)}${taggingLabel()}</span>` : ''}
                    <div class="tag-input-wrap">
                        <input class="tag-input" data-focus-key="tag-input" placeholder="Aggiungi un tag..." maxlength="40" autocomplete="off" aria-label="Aggiungi tag">
                        <div class="tag-suggest" role="listbox"></div>
                    </div>
                </div>
                <div class="row-actions"><button class="btn btn-sm btn-ghost" data-action="drawer-retag"${tagging ? ' disabled' : ''}>${icon('refresh', 14)}Rigenera tag automatici</button></div>
            </section>

            ${book.summary ? `<section class="drawer-section"><h4>Di cosa parla</h4><p class="drawer-summary">${esc(book.summary)}</p></section>` : ''}

            <section class="drawer-section">
                <h4>Cartella</h4>
                <div class="drawer-folder" style="--item-color:${folderColor(folder)}">
                    ${icon(folder ? 'folderFill' : 'books', 18)}
                    <span class="path">${path.length ? esc(path.map(f => f.name).join(' › ')) : 'Nessuna cartella'}</span>
                    <button class="btn btn-sm" data-action="drawer-move">${icon('move', 14)}Sposta</button>
                </div>
            </section>

            <section class="drawer-section">
                <h4>Colore</h4>
                <div data-drawer-colors>${colorSwatchesHTML(book.color)}</div>
            </section>

            <section class="drawer-section">
                <h4>Traduzioni</h4>
                ${translations.length ? `<div class="translation-list">${translations.map(t => translationRowHTML(t)).join('')}</div>`
                    : '<p class="muted-note">Nessuna traduzione ancora. Premi «Traduci» per scegliere lingua e modello AI: la traduzione resterà collegata a questo libro.</p>'}
            </section>

            <section class="drawer-section">
                <h4>Dettagli</h4>
                <dl class="details-grid">${details.map(([k, v]) => `<dt>${k}</dt><dd title="${esc(v)}">${esc(v)}</dd>`).join('')}</dl>
            </section>

            <div class="row-actions" style="margin-top:26px">
                <button class="btn btn-danger-ghost" data-action="drawer-trash">${icon('trash', 16)}Sposta nel cestino</button>
            </div>
        </div>
    </aside>`;
}

function translationRowHTML(t) {
    const lang = `${esc(langName(t.source_lang))} → ${esc(langName(t.target_lang))}`;
    if (t.status === 'completed') {
        return `<div class="translation-row">${icon('checkCircle', 18)}
            <div class="grow"><div class="lang">${lang}</div><div class="sub">${esc(t.model || '')} · ${esc(formatDate(t.completed_at || t.created_at))}</div></div>
            <a class="btn btn-sm" href="/api/library/translations/${t.id}/download" download>${icon('download', 14)}Scarica</a>
            <button class="icon-btn" data-action="delete-translation" data-translation-id="${t.id}" aria-label="Elimina traduzione" title="Elimina traduzione">${icon('x', 16)}</button>
        </div>`;
    }
    if (t.status === 'error') {
        return `<div class="translation-row is-error">${icon('alert', 18)}
            <div class="grow"><div class="lang">${lang}</div><div class="sub">${esc(t.error || 'Traduzione non riuscita')}</div></div>
            <button class="icon-btn" data-action="delete-translation" data-translation-id="${t.id}" aria-label="Rimuovi" title="Rimuovi">${icon('x', 16)}</button>
        </div>`;
    }
    const pct = Math.round((t.progress || 0) * 100);
    return `<div class="translation-row">${icon('spinner', 18)}
        <div class="grow"><div class="lang">${lang} · ${pct}%</div><div class="sub">${esc(t.status_text || 'In corso...')}</div><div class="bar"><span style="width:${pct}%"></span></div></div>
    </div>`;
}

function renderDrawer() {
    const book = state.drawerId ? state.bookById.get(state.drawerId) : null;
    if (!book) {
        if (el.drawer.classList.contains('is-open')) {
            el.drawer.classList.remove('is-open');
            clearTimeout(drawerCloseTimer);
            drawerCloseTimer = setTimeout(() => {
                if (!state.drawerId) setHTML(el.drawer, '');
            }, 320);
        }
        return;
    }
    clearTimeout(drawerCloseTimer);
    const editing = document.activeElement && document.activeElement.matches
        && document.activeElement.matches('#drawer-root .drawer-title-input, #drawer-root .drawer-author-input');
    if (editing && el.drawer._bookId === book.id) return;  // refreshed when the field loses focus
    const firstOpen = !el.drawer.classList.contains('is-open');
    el.drawer._bookId = book.id;
    setHTML(el.drawer, drawerHTML(book));
    autosizeTitle();
    if (firstOpen) {
        void el.drawer.offsetWidth;
        el.drawer.classList.add('is-open');
    }
}

function autosizeTitle() {
    const title = $('#drawer-root .drawer-title-input');
    if (!title) return;
    title.style.height = 'auto';
    title.style.height = `${title.scrollHeight}px`;
}

async function saveDrawerField(input) {
    const book = state.bookById.get(state.drawerId);
    if (!book) return;
    const field = input.dataset.field;
    const value = input.value.replace(/\s+/g, ' ').trim();
    const current = field === 'title' ? book.title : (book.author || '');
    if (value === current) return;
    if (field === 'title' && !value) {
        input.value = book.title;
        autosizeTitle();
        return;
    }
    book[field] = value || null;
    reindex();
    render();
    const ok = await run(() => api('PATCH', `/api/library/books/${book.id}`, { [field]: value }));
    if (ok) toast(field === 'title' ? 'Titolo aggiornato' : 'Autore aggiornato', { type: 'success', duration: 2000 });
}

let suggestIndex = -1;
function tagSuggestions(input) {
    const book = state.bookById.get(state.drawerId);
    const box = input.parentElement.querySelector('.tag-suggest');
    const query = fold(input.value.trim());
    if (!book || !query) {
        box.innerHTML = '';
        suggestIndex = -1;
        return;
    }
    const owned = new Set(book.tags.map(t => t.id));
    const matches = state.tags.filter(t => !owned.has(t.id) && fold(t.name).includes(query))
        .sort((a, b) => (fold(a.name).startsWith(query) ? 0 : 1) - (fold(b.name).startsWith(query) ? 0 : 1) || b.count - a.count)
        .slice(0, 8);
    const exact = state.tags.some(t => fold(t.name) === query);
    const options = matches.map(t => ({ name: t.name, count: t.count, color: tagColor(t) }));
    if (!exact) options.push({ name: input.value.trim(), create: true });
    suggestIndex = Math.min(suggestIndex, options.length - 1);
    box.innerHTML = options.map((option, i) => `<button type="button" class="${i === suggestIndex ? 'is-active' : ''}" data-suggest="${esc(option.name)}">
        ${option.create ? `${icon('plus', 14)}Crea «${esc(option.name)}»` : `<span class="tag-dot" style="--tag-color:${option.color}"></span>${esc(option.name)}<span class="count">${formatNumber(option.count)}</span>`}
    </button>`).join('');
}

async function addDrawerTag(name) {
    const input = $('#drawer-root .tag-input');
    if (input) input.value = '';
    suggestIndex = -1;
    if (!name || !state.drawerId) return;
    await addTags([state.drawerId], [name]);
    const next = $('#drawer-root .tag-input');
    if (next) next.focus();
}

// ---------------------------------------------------------------------------
// Uploads
// ---------------------------------------------------------------------------
let uploadTarget = null;
let uploadSeq = 0;
let uploadCollapsed = false;
const handledBatches = new Set();

function pickFiles(folderId) {
    uploadTarget = folderId || null;
    el.fileInput.value = '';
    el.fileInput.click();
}

function pickFolder(folderId) {
    uploadTarget = folderId || null;
    el.folderInput.value = '';
    el.folderInput.click();
}

async function enqueueUploads(entries, targetFolderId) {
    targetFolderId = targetFolderId || null;
    const accepted = [];
    let skipped = 0;
    for (const entry of entries) {
        const parts = entry.relPath.split('/');
        if (parts.some(part => part.startsWith('.'))) continue;
        const ext = entry.file.name.includes('.') ? entry.file.name.split('.').pop().toLowerCase() : '';
        if (ext === 'epub' || ext === 'pdf') accepted.push(entry);
        else skipped++;
    }
    if (!accepted.length) {
        toast(skipped ? `Nessun file EPUB o PDF da caricare (${plural(skipped, 'file ignorato', 'file ignorati')})` : 'Nessun file da caricare', { type: 'error' });
        return;
    }

    const dirs = new Set();
    for (const entry of accepted) {
        const parts = entry.relPath.split('/').slice(0, -1);
        for (let i = 1; i <= parts.length; i++) dirs.add(parts.slice(0, i).join('/'));
    }
    let folderMap = {};
    let created = [];
    if (dirs.size) {
        const data = await run(() => api('POST', '/api/library/folders/tree', { parent_id: targetFolderId, paths: [...dirs] }));
        if (!data) return;
        folderMap = data.folders;
        created = data.created;
        if (targetFolderId) state.expanded.add(targetFolderId);
        saveExpanded();
        await refresh();
    }

    const topDirs = [...new Set(accepted.map(e => e.relPath.split('/').slice(0, -1)[0]).filter(Boolean))];
    const batch = { id: ++uploadSeq, created: new Set(created), topFolders: topDirs.map(d => folderMap[d]).filter(Boolean), target: targetFolderId };
    accepted.sort((a, b) => collator.compare(a.relPath, b.relPath));
    for (const entry of accepted) {
        const dir = entry.relPath.split('/').slice(0, -1).join('/');
        const item = {
            uid: ++uploadSeq,
            file: entry.file,
            name: entry.file.name,
            dir,
            folderId: dir ? (folderMap[dir] || targetFolderId) : targetFolderId,
            size: entry.file.size,
            status: 'queued',
            progress: 0,
            batch,
        };
        if (entry.file.size > MAX_UPLOAD_BYTES) {
            item.status = 'error';
            item.message = 'File troppo grande (massimo 100 MB)';
        }
        state.uploads.push(item);
    }
    state.uploadNote = skipped ? `${plural(skipped, 'file ignorato', 'file ignorati')}: si possono caricare solo EPUB e PDF` : '';
    uploadCollapsed = false;
    renderUploadPanel();
    pumpUploads();
}

function pumpUploads() {
    let slots = UPLOAD_CONCURRENCY - state.uploads.filter(u => u.status === 'uploading').length;
    for (const upload of state.uploads) {
        if (slots <= 0) break;
        if (upload.status === 'queued') {
            slots--;
            startUpload(upload);
        }
    }
    renderUploadPanel();
    if (!state.uploads.some(u => u.status === 'queued' || u.status === 'uploading')) onUploadsIdle();
}

function startUpload(upload) {
    upload.status = 'uploading';
    upload.progress = 0;
    upload.message = '';
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/library/books');
    xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
            upload.progress = event.loaded / event.total;
            scheduleUploadRender();
        }
    };
    xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (_) { data = {}; }
        if (xhr.status === 201 && data.book) {
            upload.status = 'done';
            upload.progress = 1;
            upload.bookId = data.book.id;
            mergeBook(data.book);
        } else if (xhr.status === 409 && data.code === 'duplicate') {
            upload.status = 'duplicate';
            upload.existing = data.existing;
        } else {
            upload.status = 'error';
            upload.message = data.error || (xhr.status === 413 ? 'File troppo grande (massimo 100 MB)' : `Errore del server (${xhr.status})`);
            upload.retryable = xhr.status >= 500 || xhr.status === 0;
        }
        pumpUploads();
    };
    xhr.onerror = () => {
        upload.status = 'error';
        upload.message = 'Connessione interrotta';
        upload.retryable = true;
        pumpUploads();
    };
    const form = new FormData();
    form.append('file', upload.file, upload.name);
    if (upload.folderId) form.append('folder_id', upload.folderId);
    if (upload.force) form.append('allow_duplicate', '1');
    xhr.send(form);
}

async function onUploadsIdle() {
    const batches = new Set(state.uploads.map(u => u.batch));
    for (const batch of batches) {
        if (handledBatches.has(batch.id)) continue;
        handledBatches.add(batch.id);
        const items = state.uploads.filter(u => u.batch === batch);
        // Books of freshly created folders keep the order of the files on disk
        for (const folderId of batch.created) {
            const ids = items.filter(u => u.status === 'done' && u.folderId === folderId)
                .sort((a, b) => collator.compare(a.name, b.name)).map(u => u.bookId);
            if (ids.length > 1) await run(() => api('POST', '/api/library/reorder', { kind: 'book', parent_id: folderId, ids }));
        }
        await refresh();
        const done = items.filter(u => u.status === 'done').length;
        if (!done) continue;
        const top = batch.topFolders.length === 1 ? state.folderById.get(batch.topFolders[0]) : null;
        if (top) {
            toast(`Cartella «${top.name}» caricata con ${plural(done, 'libro', 'libri')}`, {
                type: 'success',
                action: 'Apri',
                onAction: () => go({ type: 'folder', id: top.id }),
            });
        } else {
            toast(`${plural(done, 'libro caricato', 'libri caricati')}: i tag automatici arrivano tra pochi secondi`, { type: 'success' });
        }
    }
}

let uploadRenderQueued = false;
function scheduleUploadRender() {
    if (uploadRenderQueued) return;
    uploadRenderQueued = true;
    requestAnimationFrame(() => {
        uploadRenderQueued = false;
        renderUploadPanel();
    });
}

function renderUploadPanel() {
    const uploads = state.uploads;
    el.uploadPanel.classList.toggle('is-visible', uploads.length > 0);
    el.uploadPanel.classList.toggle('is-collapsed', uploadCollapsed);
    if (!uploads.length) {
        el.uploadPanel.innerHTML = '';
        return;
    }
    const active = uploads.some(u => u.status === 'queued' || u.status === 'uploading');
    const finished = uploads.filter(u => u.status !== 'queued' && u.status !== 'uploading').length;
    const done = uploads.filter(u => u.status === 'done').length;
    const duplicates = uploads.filter(u => u.status === 'duplicate').length;
    const errors = uploads.filter(u => u.status === 'error').length;
    const totalBytes = uploads.reduce((sum, u) => sum + u.size, 0) || 1;
    const doneBytes = uploads.reduce((sum, u) => sum + (u.status === 'uploading' ? u.size * u.progress : (u.status === 'queued' ? 0 : u.size)), 0);
    const ratio = Math.min(1, doneBytes / totalBytes);
    const circumference = 2 * Math.PI * 14;
    const tagging = uploads.some(u => u.status === 'done' && isTagging(state.bookById.get(u.bookId) || {}));
    const title = active ? `Caricamento ${formatNumber(finished)} di ${formatNumber(uploads.length)}` : `${plural(done, 'libro caricato', 'libri caricati')}`;
    const sub = active
        ? `${Math.round(ratio * 100)}% · ${formatSize(doneBytes)} di ${formatSize(totalBytes)}`
        : [duplicates ? `${formatNumber(duplicates)} già presenti` : '', errors ? `${formatNumber(errors)} con errori` : '', tagging ? 'tag automatici in arrivo...' : ''].filter(Boolean).join(' · ') || 'Completato';

    const itemHTML = (u) => {
        let stateHTML = '';
        let message = '';
        if (u.status === 'uploading' || u.status === 'queued') {
            stateHTML = u.status === 'uploading' ? icon('spinner', 16) : '';
            message = u.status === 'queued' ? '<div class="msg">In coda</div>' : `<div class="bar"><span style="width:${Math.round(u.progress * 100)}%"></span></div>`;
        } else if (u.status === 'done') {
            const book = state.bookById.get(u.bookId);
            stateHTML = `<span class="state ok">${icon('checkCircle', 18)}</span>`;
            message = book && isTagging(book) ? `<div class="msg ok">${icon('sparkles', 11)}${taggingLabel()}</div>` : '<div class="msg ok">Caricato</div>';
        } else if (u.status === 'duplicate') {
            stateHTML = `<span class="state warn">${icon('info', 18)}</span>`;
            message = `<div class="msg warn">Già nella libreria · <button class="link-btn" data-action="upload-show" data-uid="${u.uid}">Mostra</button> · <button class="link-btn" data-action="upload-force" data-uid="${u.uid}">Carica comunque</button></div>`;
        } else {
            stateHTML = `<span class="state error">${icon('alert', 18)}</span>`;
            message = `<div class="msg error">${esc(u.message || 'Errore')}${u.retryable ? ` · <button class="link-btn" data-action="upload-retry" data-uid="${u.uid}">Riprova</button>` : ''}</div>`;
        }
        return `<div class="upload-item">
            <span class="kind ${u.name.toLowerCase().endsWith('.pdf') ? 'pdf' : ''}">${u.name.toLowerCase().endsWith('.pdf') ? 'PDF' : 'EPUB'}</span>
            <div class="main"><div class="name" title="${esc(u.name)}">${esc(u.name)}</div>${u.dir ? `<div class="path">${icon('folderFill', 11)}<span>${esc(u.dir.replace(/\//g, ' › '))}</span></div>` : ''}${message}</div>
            ${stateHTML}
        </div>`;
    };

    const shown = uploads.slice(-250).reverse();
    el.uploadPanel.innerHTML = `
        <div class="upload-head">
            <svg class="upload-ring${active ? '' : ' is-done'}" viewBox="0 0 34 34" aria-hidden="true">
                <defs><linearGradient id="upload-grad" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#8B5CF6"/><stop offset="1" stop-color="#22D3EE"/></linearGradient></defs>
                <circle class="bg" cx="17" cy="17" r="14"/>
                <circle class="fg" cx="17" cy="17" r="14" stroke-dasharray="${circumference}" stroke-dashoffset="${circumference * (1 - (active ? ratio : 1))}" transform="rotate(-90 17 17)"/>
            </svg>
            <div class="upload-head-text"><div class="upload-title">${title}</div><div class="upload-sub">${esc(sub)}</div></div>
            <button class="icon-btn" data-action="upload-collapse" aria-label="${uploadCollapsed ? 'Espandi' : 'Riduci'}">${icon(uploadCollapsed ? 'chevronRight' : 'chevronDown', 18)}</button>
            ${active ? '' : `<button class="icon-btn" data-action="upload-close" aria-label="Chiudi">${icon('x', 18)}</button>`}
        </div>
        <div class="upload-list">${shown.map(itemHTML).join('')}${state.uploadNote ? `<div class="upload-note">${esc(state.uploadNote)}</div>` : ''}</div>`;
}

function findUpload(uid) { return state.uploads.find(u => String(u.uid) === String(uid)); }

// Folder traversal for drag & drop from the computer
function collectDropped(dataTransfer) {
    const items = Array.from(dataTransfer.items || []).filter(item => item.kind === 'file');
    const entries = items.map(item => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null));
    if (entries.length && entries.every(Boolean)) {
        return Promise.all(entries.map(entry => readEntry(entry, ''))).then(lists => lists.flat());
    }
    return Promise.resolve(Array.from(dataTransfer.files || []).map(file => ({ file, relPath: file.name })));
}

async function readEntry(entry, prefix) {
    if (entry.isFile) {
        const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
        return [{ file, relPath: prefix + file.name }];
    }
    if (entry.isDirectory) {
        const reader = entry.createReader();
        const children = [];
        for (;;) {
            const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
            if (!batch.length) break;
            children.push(...batch);
        }
        const files = [];
        for (const child of children) files.push(...await readEntry(child, `${prefix}${entry.name}/`));
        return files;
    }
    return [];
}

// ---------------------------------------------------------------------------
// Drag & drop inside the library (mouse + touch)
// ---------------------------------------------------------------------------
const drag = { pending: null, active: null };
let suppressClickUntil = 0;
const marker = document.createElement('div');
marker.className = 'drop-marker';
document.body.appendChild(marker);

function onPointerDown(event) {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    const node = event.target.closest('[data-kind][data-id]');
    if (!node || !node.closest('#lib-content, #sb-tree')) return;
    if (event.target.closest('button, input, textarea, a, select')) return;
    if (state.renaming || state.view.type === 'trash') return;
    cancelPending();
    const pending = {
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
        kind: node.dataset.kind,
        id: node.dataset.id,
        node,
        type: event.pointerType,
        armed: false,
        timer: null,
    };
    if (event.pointerType !== 'mouse') {
        pending.timer = setTimeout(() => {
            if (drag.pending !== pending) return;
            pending.armed = true;
            node.classList.add('press-armed');
            if (navigator.vibrate) navigator.vibrate(12);
        }, 350);
    }
    drag.pending = pending;
}

function cancelPending() {
    const pending = drag.pending;
    if (!pending) return;
    clearTimeout(pending.timer);
    pending.node.classList.remove('press-armed');
    drag.pending = null;
}

function onPointerMove(event) {
    if (drag.active) {
        if (event.pointerId !== drag.active.pointerId) return;
        event.preventDefault();
        updateDrag(event.clientX, event.clientY);
        return;
    }
    const pending = drag.pending;
    if (!pending || event.pointerId !== pending.pointerId) return;
    const distance = Math.hypot(event.clientX - pending.x, event.clientY - pending.y);
    if (pending.type === 'mouse' || pending.armed) {
        if (distance > 6) startDrag(event);
    } else if (distance > 10) {
        cancelPending();
    }
}

function onPointerUp(event) {
    if (drag.active) {
        if (event.pointerId === drag.active.pointerId) finishDrag();
        return;
    }
    const pending = drag.pending;
    if (!pending || event.pointerId !== pending.pointerId) return;
    const armed = pending.armed;
    cancelPending();
    if (armed) {
        suppressClickUntil = Date.now() + 350;
        if (!openMenuEl) openItemMenu(pending.kind, pending.id, { x: event.clientX, y: event.clientY });
    }
}

function onPointerCancel(event) {
    if (drag.active && event.pointerId === drag.active.pointerId) {
        cancelDrag();
        return;
    }
    if (drag.pending && event.pointerId === drag.pending.pointerId) cancelPending();
}

function startDrag(event) {
    const pending = drag.pending;
    cancelPending();
    const key = keyOf(pending.kind, pending.id);
    let items = state.selection.has(key) ? selectedItems() : [{ kind: pending.kind, id: pending.id }];
    items = items.filter(i => (i.kind === 'book' ? state.bookById.has(i.id) : state.folderById.has(i.id)));
    if (!items.length) return;
    closeMenu();
    const ghost = createGhost(items);
    document.body.appendChild(ghost);
    document.body.classList.add('is-dragging');
    for (const item of items) {
        for (const node of $$(`[data-kind="${item.kind}"][data-id="${item.id}"]`)) node.classList.add('is-dragging');
    }
    const blocked = new Set();
    for (const item of items) if (item.kind === 'folder') for (const id of subtreeIds(item.id)) blocked.add(id);
    drag.active = {
        pointerId: pending.pointerId,
        items,
        ghost,
        target: null,
        x: event.clientX,
        y: event.clientY,
        raf: 0,
        hoverId: null,
        hoverSince: 0,
        hasBooks: items.some(i => i.kind === 'book'),
        hasFolders: items.some(i => i.kind === 'folder'),
        blocked,
    };
    suppressClickUntil = Infinity;
    updateDrag(event.clientX, event.clientY);
    drag.active.raf = requestAnimationFrame(autoScroll);
}

function createGhost(items) {
    const first = items[0];
    const ghost = document.createElement('div');
    ghost.className = 'drag-ghost';
    let thumb;
    let name;
    if (first.kind === 'book') {
        const book = state.bookById.get(first.id);
        thumb = `<div class="drag-ghost-thumb">${coverHTML(book, true)}</div>`;
        name = book.title;
    } else {
        const folder = state.folderById.get(first.id);
        thumb = `<div class="drag-ghost-thumb is-folder" style="--item-color:${folderColor(folder)}">${FOLDER_GLYPH}</div>`;
        name = folder.name;
    }
    ghost.innerHTML = `${thumb}<div class="drag-ghost-text"><div class="drag-ghost-name">${esc(items.length > 1 ? `${name} e altri ${items.length - 1}` : name)}</div><div class="drag-ghost-hint">Rilascia su una cartella</div></div>${items.length > 1 ? `<span class="drag-ghost-count">${items.length}</span>` : ''}`;
    return ghost;
}

function updateDrag(x, y) {
    const active = drag.active;
    active.x = x;
    active.y = y;
    active.ghost.style.transform = `translate(${Math.round(x + 16)}px, ${Math.round(y + 14)}px)`;
    const node = document.elementFromPoint(x, y);
    setDropTarget(resolveTarget(node, x, y));

    const row = node && node.closest('#sb-tree .tree-row');
    if (row) {
        const id = row.dataset.id;
        if (active.hoverId !== id) {
            active.hoverId = id;
            active.hoverSince = Date.now();
        } else if (Date.now() - active.hoverSince > 650 && !state.expanded.has(id) && (state.childFolders.get(id) || []).length) {
            state.expanded.add(id);
            saveExpanded();
            renderSidebar();
        }
    } else {
        active.hoverId = null;
    }
}

function allIn(items, parentId) { return items.every(item => parentOf(item) === (parentId || null)); }

function folderTarget(folderId, node) {
    const active = drag.active;
    folderId = folderId || null;
    const folder = folderId ? state.folderById.get(folderId) : null;
    if (folderId && active.blocked.has(folderId)) {
        return { type: 'folder', invalid: true, node, label: 'Non puoi spostare una cartella dentro se stessa' };
    }
    if (allIn(active.items, folderId)) {
        return { type: 'none', node: null, label: folder ? `Già in «${folder.name}»` : 'Già qui' };
    }
    return { type: 'folder', folderId, node, label: folder ? `Sposta in «${folder.name}»` : 'Sposta fuori dalle cartelle' };
}

function reorderTarget(card, x, y) {
    const rect = card.getBoundingClientRect();
    const after = state.layout === 'list' ? y > rect.top + rect.height / 2 : x > rect.left + rect.width / 2;
    return { type: 'reorder', kind: card.dataset.kind, id: card.dataset.id, after, rect, node: null, label: 'Riordina qui' };
}

function resolveTarget(node, x, y) {
    const active = drag.active;
    if (!node) return null;
    const view = state.lastView;
    const card = node.closest('#lib-content [data-kind][data-id]');
    if (card && view) {
        const kind = card.dataset.kind;
        const id = card.dataset.id;
        const sameKind = active.items.every(i => i.kind === kind);
        const canReorder = view.reorderable && sameKind && allIn(active.items, view.container);
        if (kind === 'folder') {
            const rect = card.getBoundingClientRect();
            const edge = state.layout === 'list'
                ? (y - rect.top < rect.height * 0.25 || rect.bottom - y < rect.height * 0.25)
                : (x - rect.left < rect.width * 0.2 || rect.right - x < rect.width * 0.2);
            if (canReorder && edge) return reorderTarget(card, x, y);
            if (active.items.some(i => i.kind === 'folder' && i.id === id)) return { type: 'none', node: null, label: 'Rilascia su una cartella' };
            return folderTarget(id, card);
        }
        if (canReorder) return reorderTarget(card, x, y);
        if (view.mode !== 'flat' && !allIn(active.items, view.container)) return folderTarget(view.container, null);
        return null;
    }
    const zone = node.closest('[data-drop]');
    if (zone) {
        switch (zone.dataset.drop) {
            case 'folder':
                return folderTarget(zone.dataset.folderId || null, zone);
            case 'favorite':
                return active.hasFolders
                    ? { type: 'favorite', invalid: true, node: zone, label: 'Solo i libri possono essere preferiti' }
                    : { type: 'favorite', node: zone, label: 'Aggiungi ai preferiti' };
            case 'tag': {
                const tag = state.tagById.get(zone.dataset.tagId);
                if (!tag) return null;
                return active.hasFolders
                    ? { type: 'tag', invalid: true, node: zone, label: 'I tag si assegnano solo ai libri' }
                    : { type: 'tag', tag, node: zone, label: `Aggiungi il tag «${tag.name}»` };
            }
            case 'trash':
                return { type: 'trash', node: zone, label: 'Sposta nel cestino' };
            default:
                return null;
        }
    }
    if (view && view.mode !== 'flat' && node.closest('#lib-content') && !allIn(active.items, view.container)) {
        return folderTarget(view.container, null);
    }
    return null;
}

function setDropTarget(target) {
    const active = drag.active;
    const previous = active.target;
    if (previous && previous.node && (!target || previous.node !== target.node)) {
        previous.node.classList.remove('is-drop-target', 'is-drop-invalid');
    }
    active.target = target;
    if (target && target.node) target.node.classList.add(target.invalid ? 'is-drop-invalid' : 'is-drop-target');

    if (target && target.type === 'reorder') {
        const r = target.rect;
        if (state.layout === 'list') {
            marker.style.cssText = `display:block;left:${r.left}px;top:${(target.after ? r.bottom + 3 : r.top - 3) - 1.5}px;width:${r.width}px;height:3px`;
        } else {
            const gap = target.kind === 'book' ? 11 : 7;
            marker.style.cssText = `display:block;left:${(target.after ? r.right + gap : r.left - gap) - 1.5}px;top:${r.top}px;width:3px;height:${r.height}px`;
        }
    } else {
        marker.style.display = 'none';
    }
    const hint = active.ghost.querySelector('.drag-ghost-hint');
    hint.textContent = target ? target.label : 'Rilascia su una cartella';
    hint.classList.toggle('is-invalid', Boolean(target && target.invalid));
}

function autoScroll() {
    const active = drag.active;
    if (!active) return;
    for (const container of [el.scroll, $('#sb-scroll')]) {
        const r = container.getBoundingClientRect();
        if (active.x < r.left || active.x > r.right) continue;
        const edge = 60;
        let dy = 0;
        if (active.y < r.top + edge && active.y > r.top - 60) dy = -Math.ceil((r.top + edge - active.y) / 5);
        else if (active.y > r.bottom - edge && active.y < r.bottom + 60) dy = Math.ceil((active.y - (r.bottom - edge)) / 5);
        if (dy) {
            container.scrollTop += dy;
            updateDrag(active.x, active.y);
        }
    }
    active.raf = requestAnimationFrame(autoScroll);
}

function endDragVisuals() {
    const active = drag.active;
    if (!active) return;
    cancelAnimationFrame(active.raf);
    active.ghost.remove();
    marker.style.display = 'none';
    if (active.target && active.target.node) active.target.node.classList.remove('is-drop-target', 'is-drop-invalid');
    for (const node of $$('.is-dragging')) node.classList.remove('is-dragging');
    drag.active = null;
    suppressClickUntil = Date.now() + 250;
    invalidate();
    render();
}

function finishDrag() {
    const active = drag.active;
    const target = active.target;
    const items = active.items;
    endDragVisuals();
    if (!target || target.invalid || target.type === 'none') return;
    const bookIds = items.filter(i => i.kind === 'book').map(i => i.id);
    switch (target.type) {
        case 'folder': moveItems(items, target.folderId); break;
        case 'reorder': reorderItems(items, target); break;
        case 'favorite': setFavorite(bookIds, true); break;
        case 'tag':
            addTags(bookIds, [target.tag.name]).then(ok => {
                if (ok) toast(`Tag «${target.tag.name}» aggiunto a ${plural(bookIds.length, 'libro', 'libri')}`, { type: 'success', duration: 2600 });
            });
            break;
        case 'trash': trashItems(items); break;
        default: break;
    }
}

function cancelDrag() { endDragVisuals(); }

// ---------------------------------------------------------------------------
// Files dragged from the computer
// ---------------------------------------------------------------------------
let fileDragTimer = null;
let fileDragTarget = null;
const hasFiles = (event) => Array.from((event.dataTransfer && event.dataTransfer.types) || []).includes('Files');

function fileDropFolder(node) {
    const zone = node && node.closest && node.closest('[data-drop="folder"]');
    if (zone) return zone.dataset.folderId || null;
    return currentFolderId();
}

function endFileDrag() {
    clearTimeout(fileDragTimer);
    el.dropOverlay.classList.remove('is-visible');
    if (fileDragTarget) fileDragTarget.classList.remove('is-drop-target');
    fileDragTarget = null;
}

window.addEventListener('dragover', (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    const zone = event.target.closest && event.target.closest('[data-drop="folder"]');
    if (zone !== fileDragTarget) {
        if (fileDragTarget) fileDragTarget.classList.remove('is-drop-target');
        fileDragTarget = zone;
        if (zone) zone.classList.add('is-drop-target');
    }
    const folderId = fileDropFolder(event.target);
    const folder = folderId ? state.folderById.get(folderId) : null;
    el.dropOverlayTitle.textContent = folder ? `Rilascia per caricare in «${folder.name}»` : 'Rilascia per caricare nella libreria';
    el.dropOverlay.classList.add('is-visible');
    clearTimeout(fileDragTimer);
    fileDragTimer = setTimeout(endFileDrag, 220);
});

window.addEventListener('drop', (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    const target = fileDropFolder(event.target);
    endFileDrag();
    collectDropped(event.dataTransfer)
        .then(entries => enqueueUploads(entries, target))
        .catch(() => toast('Impossibile leggere i file trascinati', { type: 'error' }));
});

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------
let lastTreeClick = { id: null, time: 0 };

function handleNav(node) {
    const type = node.dataset.nav;
    if (type === 'folder') {
        const id = node.dataset.id || null;
        if (node.classList.contains('tree-row')) {
            const now = Date.now();
            if (lastTreeClick.id === id && now - lastTreeClick.time < 450) {
                lastTreeClick = { id: null, time: 0 };
                startRename('folder', id);
                return;
            }
            lastTreeClick = { id, time: now };
        }
        go({ type: 'folder', id });
        return;
    }
    go({ type });
}

function handleCardClick(card, event) {
    const key = keyOf(card.dataset.kind, card.dataset.id);
    if (event.shiftKey && state.anchor) {
        selectRange(key);
        return;
    }
    if (event.metaKey || event.ctrlKey || state.selection.size) {
        toggleSelect(key);
        return;
    }
    if (card.dataset.kind === 'folder') go({ type: 'folder', id: card.dataset.id });
    else openDrawer(card.dataset.id);
}

async function handleAction(action, node) {
    const card = node.closest('[data-kind][data-id]');
    switch (action) {
        case 'toggle-select':
            if (card) toggleSelect(keyOf(card.dataset.kind, card.dataset.id));
            break;
        case 'item-menu':
            if (card) openItemMenu(card.dataset.kind, card.dataset.id, node);
            break;
        case 'toggle-tree': {
            const id = node.dataset.id;
            if (state.expanded.has(id)) state.expanded.delete(id);
            else state.expanded.add(id);
            saveExpanded();
            renderSidebar();
            break;
        }
        case 'new-folder': createFolder(currentFolderId()); break;
        case 'new-folder-root': createFolder(null); break;
        case 'upload-files': pickFiles(currentFolderId()); break;
        case 'upload-folder': pickFolder(currentFolderId()); break;
        case 'upload-menu': openUploadMenu(node); break;
        case 'sort-menu': openSortMenu(node); break;
        case 'filter-menu': openFilterMenu(node); break;
        case 'layout':
            state.layout = node.dataset.layout;
            store.set('layout', state.layout);
            render();
            break;
        case 'view-chip': {
            const type = node.dataset.view;
            go(type === 'folder' ? { type: 'folder', id: null } : { type });
            break;
        }
        case 'filter-tag': toggleTagFilter(node.dataset.tagId); break;
        case 'sidebar-tag': {
            const tagId = node.dataset.tagId;
            if (state.view.type === 'folder' && !state.view.id && !isFiltering()) state.view = { type: 'all' };
            if (state.view.type === 'trash') go({ type: 'all' });
            if (state.filters.tags.length === 1 && state.filters.tags[0] === tagId) state.filters.tags = [];
            else state.filters.tags = [tagId];
            history.replaceState(null, '', hashFor(state.view));
            closeSidebar();
            render();
            break;
        }
        case 'tag-menu': openTagMenu(node.dataset.tagId, node); break;
        case 'toggle-all-tags':
            state.showAllTags = !state.showAllTags;
            renderSidebar();
            break;
        case 'clear-filters': clearFilters(); break;
        case 'remove-filter':
            state.filters[node.dataset.filter] = null;
            render();
            break;
        case 'open-key': openKeyModal(); break;
        case 'dismiss-key-banner':
            store.set('keyBannerDismissed', true);
            render();
            break;
        case 'open-sidebar': openSidebar(); break;
        case 'close-sidebar': closeSidebar(); break;
        case 'close-drawer': closeDrawer(); break;
        case 'drawer-menu': if (state.drawerId) openBookMenu(state.drawerId, node); break;
        case 'drawer-translate': translateBook(state.drawerId); break;
        case 'drawer-download': downloadBook(state.drawerId); break;
        case 'drawer-favorite': {
            const book = state.bookById.get(state.drawerId);
            if (book) setFavorite([book.id], !book.favorite);
            break;
        }
        case 'drawer-remove-tag': removeTag([state.drawerId], node.dataset.tagId); break;
        case 'drawer-retag': retag(state.drawerId); break;
        case 'drawer-move': openMovePicker([{ kind: 'book', id: state.drawerId }]); break;
        case 'drawer-trash': trashItems([{ kind: 'book', id: state.drawerId }]); break;
        case 'delete-translation': {
            const ok = await confirmDialog({ title: 'Eliminare la traduzione?', message: 'Il file tradotto verrà eliminato. Il libro originale resta nella libreria.', confirm: 'Elimina', danger: true });
            if (!ok) break;
            const done = await run(() => api('DELETE', `/api/library/translations/${node.dataset.translationId}`));
            if (done) await refresh();
            break;
        }
        case 'sel-move': openMovePicker(selectedItems()); break;
        case 'sel-color': openMenu([{ heading: 'Colore' }, { colors: true, current: null, onPick: color => setColor(selectedItems(), color) }], node); break;
        case 'sel-tags': openTagModal(selectedItems().filter(i => i.kind === 'book').map(i => i.id)); break;
        case 'sel-favorite': {
            const ids = selectedItems().filter(i => i.kind === 'book').map(i => i.id);
            const allFavorite = ids.every(id => (state.bookById.get(id) || {}).favorite);
            setFavorite(ids, !allFavorite);
            break;
        }
        case 'sel-download': {
            const book = selectedItems().find(i => i.kind === 'book');
            if (book) downloadBook(book.id);
            break;
        }
        case 'sel-trash': trashItems(selectedItems()); break;
        case 'sel-clear': clearSelection(); break;
        case 'trash-restore': {
            const kind = node.dataset.trashKind;
            const id = node.dataset.trashId;
            const ok = await run(() => api('POST', '/api/library/trash/restore', kind === 'book' ? { book_ids: [id] } : { folder_ids: [id] }));
            if (ok) {
                toast('Ripristinato', { type: 'success', duration: 2200 });
                await refresh();
                loadTrash();
            }
            break;
        }
        case 'trash-purge': {
            const kind = node.dataset.trashKind;
            const id = node.dataset.trashId;
            const item = (state.trash.items || []).find(i => i.id === id);
            const confirmed = await confirmDialog({
                title: 'Eliminare definitivamente?',
                message: kind === 'folder'
                    ? `La cartella «${item ? item.name : ''}» e i suoi ${plural(item ? item.book_count : 0, 'libro', 'libri')} verranno eliminati per sempre, insieme alle traduzioni.`
                    : `«${item ? item.name : ''}» e le sue traduzioni verranno eliminati per sempre.`,
                confirm: 'Elimina per sempre',
                danger: true,
            });
            if (!confirmed) break;
            const ok = await run(() => api('POST', '/api/library/trash/purge', kind === 'book' ? { book_ids: [id] } : { folder_ids: [id] }));
            if (ok) {
                await refresh();
                loadTrash();
            }
            break;
        }
        case 'empty-trash': {
            const confirmed = await confirmDialog({
                title: 'Svuotare il cestino?',
                message: 'Tutti gli elementi nel cestino verranno eliminati per sempre, insieme alle loro traduzioni.',
                confirm: 'Svuota cestino',
                danger: true,
            });
            if (!confirmed) break;
            const ok = await run(() => api('POST', '/api/library/trash/empty'));
            if (ok) {
                toast('Cestino svuotato', { type: 'success', duration: 2200 });
                await refresh();
                loadTrash();
            }
            break;
        }
        case 'upload-collapse':
            uploadCollapsed = !uploadCollapsed;
            renderUploadPanel();
            break;
        case 'upload-close':
            state.uploads = state.uploads.filter(u => u.status === 'queued' || u.status === 'uploading');
            state.uploadNote = '';
            renderUploadPanel();
            break;
        case 'upload-force':
        case 'upload-retry': {
            const upload = findUpload(node.dataset.uid);
            if (!upload) break;
            upload.force = action === 'upload-force' || upload.force;
            upload.status = 'queued';
            handledBatches.delete(upload.batch.id);
            pumpUploads();
            break;
        }
        case 'upload-show': {
            const upload = findUpload(node.dataset.uid);
            if (upload && upload.existing) {
                if (!state.bookById.has(upload.existing.id)) await refresh();
                go({ type: 'folder', id: upload.existing.folder_id || null });
                openDrawer(upload.existing.id);
            }
            break;
        }
        default:
            break;
    }
}

document.addEventListener('click', (event) => {
    // Swallow the click that some browsers fire at the end of a drag or long-press
    if (Date.now() < suppressClickUntil) {
        event.preventDefault();
        event.stopPropagation();
        return;
    }
    if (event.target.closest('.rename-input')) return;
    const actionNode = event.target.closest('[data-action]');
    if (actionNode && !actionNode.disabled && !actionNode.closest('.ctx-menu, .modal-backdrop')) {
        event.stopPropagation();
        handleAction(actionNode.dataset.action, actionNode);
        return;
    }
    const navNode = event.target.closest('[data-nav]');
    if (navNode) {
        handleNav(navNode);
        return;
    }
    const card = event.target.closest('#lib-content [data-kind][data-id]');
    if (card) {
        handleCardClick(card, event);
        return;
    }
    if (event.target.closest('#lib-content') && !event.target.closest('button, a, input') && state.selection.size) {
        clearSelection();
    }
}, true);

document.addEventListener('contextmenu', (event) => {
    if (event.target.closest('input, textarea, .modal-backdrop, .ctx-menu')) return;
    const card = event.target.closest('[data-kind][data-id]');
    if (card && card.closest('#lib-content, #sb-tree')) {
        event.preventDefault();
        cancelPending();
        openItemMenu(card.dataset.kind, card.dataset.id, { x: event.clientX, y: event.clientY });
        return;
    }
    const tag = event.target.closest('.sb-tag, .tag-filter');
    if (tag) {
        event.preventDefault();
        openTagMenu(tag.dataset.tagId, { x: event.clientX, y: event.clientY });
        return;
    }
    if (event.target.closest('#lib-content') && state.view.type === 'folder') {
        event.preventDefault();
        openContentMenu({ x: event.clientX, y: event.clientY });
    }
});

document.addEventListener('pointerdown', (event) => {
    if (openMenuEl && !openMenuEl.contains(event.target)) closeMenu();
}, true);
document.addEventListener('pointerdown', onPointerDown);
window.addEventListener('pointermove', onPointerMove, { passive: false });
window.addEventListener('pointerup', onPointerUp);
window.addEventListener('pointercancel', onPointerCancel);
document.addEventListener('touchmove', (event) => {
    if (drag.active || (drag.pending && drag.pending.armed)) event.preventDefault();
}, { passive: false });
window.addEventListener('resize', closeMenu);
// Scrolling closes menus, except for the scroll caused by the click that opened one
el.scroll.addEventListener('scroll', () => {
    if (performance.now() - menuOpenedAt > 300) closeMenu();
}, { passive: true });

function keyboardTargets() {
    if (state.selection.size) return selectedItems();
    const focused = document.activeElement && document.activeElement.closest && document.activeElement.closest('#lib-content [data-kind][data-id]');
    return focused ? [{ kind: focused.dataset.kind, id: focused.dataset.id }] : [];
}

document.addEventListener('keydown', (event) => {
    if (drag.active) {
        if (event.key === 'Escape') cancelDrag();
        return;
    }
    if (modalStack.length) {
        if (event.key === 'Escape') {
            event.preventDefault();
            modalStack[modalStack.length - 1].close();
        }
        return;
    }
    if (openMenuEl) {
        menuKeydown(event);
        return;
    }
    const target = event.target;
    if (target.closest('input, textarea, select, [contenteditable="true"]')) {
        if (event.key === 'Escape') {
            if (target === el.search && el.search.value) {
                el.search.value = '';
                state.filters.q = '';
                render();
            } else {
                target.blur();
            }
        }
        return;
    }
    if (target.matches('[role="button"], [role="treeitem"]') && (event.key === 'Enter' || event.key === ' ')) {
        event.preventDefault();
        target.click();
        return;
    }
    const mod = event.metaKey || event.ctrlKey;
    if (event.key === '/' || (mod && event.key.toLowerCase() === 'k')) {
        event.preventDefault();
        el.search.focus();
        el.search.select();
        return;
    }
    if (event.key === 'Escape') {
        if (state.drawerId) closeDrawer();
        else if (state.selection.size) clearSelection();
        else if (isFiltering()) clearFilters();
        else closeSidebar();
        return;
    }
    if (state.view.type === 'trash') return;
    if (mod && event.key.toLowerCase() === 'a') {
        event.preventDefault();
        selectAllVisible();
        return;
    }
    const targets = keyboardTargets();
    if ((event.key === 'Delete' || (mod && event.key === 'Backspace')) && targets.length) {
        event.preventDefault();
        trashItems(targets);
        return;
    }
    if (event.key === 'F2' && targets.length === 1) {
        event.preventDefault();
        startRename(targets[0].kind, targets[0].id);
        return;
    }
    const card = target.closest && target.closest('#lib-content [data-kind][data-id]');
    if (card && event.key === 'Enter') {
        event.preventDefault();
        if (card.dataset.kind === 'folder') go({ type: 'folder', id: card.dataset.id });
        else openDrawer(card.dataset.id);
    } else if (card && event.key === ' ') {
        event.preventDefault();
        toggleSelect(keyOf(card.dataset.kind, card.dataset.id));
    }
});

let searchTimer = null;
el.search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
        state.filters.q = el.search.value;
        render();
    }, 110);
});

el.fileInput.addEventListener('change', () => {
    const files = Array.from(el.fileInput.files || []);
    if (files.length) enqueueUploads(files.map(file => ({ file, relPath: file.name })), uploadTarget);
});

el.folderInput.addEventListener('change', () => {
    const files = Array.from(el.folderInput.files || []);
    if (files.length) enqueueUploads(files.map(file => ({ file, relPath: file.webkitRelativePath || file.name })), uploadTarget);
});

el.content.addEventListener('error', (event) => {
    const img = event.target;
    if (img.tagName === 'IMG' && img.classList.contains('cover-img')) {
        const card = img.closest('[data-kind="book"]');
        const book = card && state.bookById.get(card.dataset.id);
        if (book) img.outerHTML = coverHTML({ ...book, cover_url: null });
    }
}, true);

// Drawer inputs
el.drawer.addEventListener('keydown', (event) => {
    const target = event.target;
    if (target.matches('.drawer-title-input, .drawer-author-input')) {
        if (event.key === 'Enter') {
            event.preventDefault();
            target.blur();
        } else if (event.key === 'Escape') {
            const book = state.bookById.get(state.drawerId);
            target.value = target.dataset.field === 'title' ? book.title : (book.author || '');
            autosizeTitle();
        }
        return;
    }
    if (target.matches('.tag-input')) {
        const box = target.parentElement.querySelector('.tag-suggest');
        const options = $$('button', box);
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (!options.length) return;
            suggestIndex = (suggestIndex + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
            options.forEach((option, i) => option.classList.toggle('is-active', i === suggestIndex));
        } else if (event.key === 'Enter' || event.key === ',') {
            event.preventDefault();
            const chosen = suggestIndex >= 0 && options[suggestIndex] ? options[suggestIndex].dataset.suggest : target.value.trim();
            if (chosen) addDrawerTag(chosen);
        } else if (event.key === 'Escape' && target.value) {
            event.stopPropagation();
            target.value = '';
            box.innerHTML = '';
        }
    }
});

el.drawer.addEventListener('input', (event) => {
    if (event.target.matches('.drawer-title-input')) autosizeTitle();
    if (event.target.matches('.tag-input')) {
        suggestIndex = -1;
        tagSuggestions(event.target);
    }
});

el.drawer.addEventListener('focusout', (event) => {
    if (event.target.matches('.drawer-title-input, .drawer-author-input')) {
        saveDrawerField(event.target).then(() => scheduleRender());
    }
    if (event.target.matches('.tag-input')) {
        setTimeout(() => {
            const box = $('#drawer-root .tag-suggest');
            if (box && !box.contains(document.activeElement)) box.innerHTML = '';
        }, 150);
    }
});

el.drawer.addEventListener('click', (event) => {
    const suggestion = event.target.closest('[data-suggest]');
    if (suggestion) {
        event.stopPropagation();
        addDrawerTag(suggestion.dataset.suggest);
        return;
    }
    const swatch = event.target.closest('[data-drawer-colors] .swatch');
    if (swatch && state.drawerId) {
        event.stopPropagation();
        setColor([{ kind: 'book', id: state.drawerId }], swatch.dataset.color || null);
    }
}, true);

window.addEventListener('popstate', () => {
    if (state.loaded) applyView(viewFromHash());
});

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
async function init() {
    try {
        await refresh();
    } catch (error) {
        el.content.innerHTML = `<div class="empty-state is-compact"><div class="empty-title">Impossibile caricare la libreria</div>
            <p class="empty-text">${esc(error.message)}</p><div class="empty-actions"><button class="btn btn-primary" onclick="location.reload()">${icon('refresh')}Riprova</button></div></div>`;
        return;
    }
    const initial = viewFromHash();
    state.view = { type: '__init__' };
    if (!location.hash) history.replaceState(null, '', hashFor({ type: 'folder', id: null }));
    applyView(initial);
    const code = new URLSearchParams(location.search).get('chiave');
    if (code) handleLinkedLibrary(code);
}

init();
})();
