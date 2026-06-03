export const LiraUtils = {
    $: (id) => document.getElementById(id),

    $$: (selector) => document.querySelectorAll(selector),

    create: (tag, className, innerHTML = '') => {
        const el = document.createElement(tag);
        if (className) el.className = className;
        if (innerHTML) el.innerHTML = innerHTML;
        return el;
    },

    clear: (el) => {
        if (el) el.innerHTML = '';
    },

    escapeHTML: (str) => {
        if (!str) return "";
        return str.replace(/[&<>"']/g, (m) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        }[m]));
    }
};