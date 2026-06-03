/**
 * UI i18n (gettext-style): English text is the msgid.
 * ru: infrastructure/locale/ui/ru.csv (key = English, ru = translation)
 * en: msgid returned as-is
 *
 * HTML: data-i18n="Save" or <span data-i18n>Save</span>
 * JS: i18n.t('Save') or i18n.t('Done: {done} of {total}', { done, total })
 */
function parseCsvLine(line) {
    const out = [];
    let cell = '';
    let inQuotes = false;
    for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (inQuotes) {
            if (ch === '"') {
                if (line[i + 1] === '"') {
                    cell += '"';
                    i++;
                } else {
                    inQuotes = false;
                }
            } else {
                cell += ch;
            }
        } else if (ch === '"') {
            inQuotes = true;
        } else if (ch === ',') {
            out.push(cell.trim());
            cell = '';
        } else {
            cell += ch;
        }
    }
    out.push(cell.trim());
    return out;
}

/** @param {string} text ru.csv body */
function parseRuCsv(text) {
    const lines = text.replace(/\r\n/g, '\n').split('\n').filter((l) => l.trim());
    if (!lines.length) return {};
    const header = parseCsvLine(lines[0]);
    const keyIdx = header.indexOf('key');
    const ruIdx = header.indexOf('ru');
    if (keyIdx < 0 || ruIdx < 0) return {};
    const byEn = {};
    for (let i = 1; i < lines.length; i++) {
        const cols = parseCsvLine(lines[i]);
        const en = cols[keyIdx];
        const ru = cols[ruIdx];
        if (en) byEn[en] = ru || en;
    }
    return byEn;
}

export class I18n {
    constructor() {
        this.locale = 'ru';
        /** @type {Record<string, string>} English msgid -> Russian */
        this.ru = {};
        this._ruLoaded = false;
    }

    async load(locale) {
        const loc = locale === 'en' ? 'en' : 'ru';
        if (!this._ruLoaded) {
            const res = await fetch('../scripts/chat/infrastructure/locale/ui/ru.csv');
            if (!res.ok) {
                throw new Error(`locale fetch failed: ui/ru.csv (${res.status})`);
            }
            this.ru = parseRuCsv(await res.text());
            this._ruLoaded = true;
        }
        this.locale = loc;
        document.documentElement.lang = loc;
        return loc;
    }

    /** @param {string} msgid English source string */
    t(msgid, vars = {}) {
        const id = (msgid || '').trim();
        if (!id) return '';
        let s = id;
        if (this.locale === 'ru') {
            s = this.ru[id] ?? id;
        }
        for (const [k, v] of Object.entries(vars)) {
            s = s.replaceAll(`{${k}}`, String(v));
        }
        return s;
    }

    applyDom(root = document) {
        root.querySelectorAll('[data-i18n]').forEach((el) => {
            const attrKey = el.getAttribute('data-i18n')?.trim();
            const msgid = attrKey || el.textContent?.trim() || '';
            if (!msgid) return;
            const target = el.getAttribute('data-i18n-attr');
            const text = this.t(msgid);
            if (target) {
                el.setAttribute(target, text);
            } else {
                el.textContent = text;
            }
        });
        const sel = document.getElementById('ui-locale-select');
        if (sel && sel.value !== this.locale) {
            sel.value = this.locale;
        }
    }
}
