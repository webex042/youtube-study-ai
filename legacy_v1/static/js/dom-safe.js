/**
 * Safe DOM Construction
 *
 * Every string the dashboard renders originates from one of two untrusted
 * sources: a YouTube transcript, or an LLM that was fed that transcript. The
 * server sanitises both, but the browser is the layer where a mistake actually
 * becomes script execution, so it does not rely on that.
 *
 * The rule this module enforces: untrusted text reaches the page only through
 * `textContent`. There is no `innerHTML` anywhere in the client code, so there
 * is no parser for an attacker to reach in the first place — escaping bugs,
 * mutation XSS and double-decoding all stop being possible rather than being
 * defended against.
 *
 * Markdown is the one case that genuinely needs structure. `renderMarkdown`
 * therefore builds real elements from the parsed text instead of assembling an
 * HTML string, so inline markup in the source can never become live markup in
 * the output.
 *
 * Loaded before every other script. Exposed on `window.SafeDOM`.
 */

(function (global) {
    'use strict';

    // ── Element construction ─────────────────────────────────────

    /**
     * Create an element.
     *
     * `text` is always assigned via textContent — the single place untrusted
     * strings enter the DOM. Attributes are set with setAttribute, which does
     * not parse markup.
     */
    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    /** Remove all children. Replaces the `innerHTML = ''` idiom. */
    function clear(node) {
        if (!node) return node;
        while (node.firstChild) node.removeChild(node.firstChild);
        return node;
    }

    /** Append children, skipping nullish entries so callers can use `cond && node`. */
    function append(parent, children) {
        (Array.isArray(children) ? children : [children]).forEach((child) => {
            if (child) parent.appendChild(child);
        });
        return parent;
    }

    /** Replace a node's contents in one call. */
    function replace(node, children) {
        if (!node) return node;
        clear(node);
        return append(node, children);
    }

    // ── URL safety ───────────────────────────────────────────────

    // Anything that isn't plainly http(s) is treated as hostile. Checked after
    // stripping whitespace and invisible characters, since a zero-width space or
    // a tab inside `javascript:` still executes in some browsers.
    const UNSAFE_SCHEME = /^(?:javascript|data|vbscript|file|blob|about|jar):/i;
    // Matches C0/C1 controls, whitespace, soft hyphen, zero-width and bidi
    // formatting characters. Written as escapes so the source stays readable
    // and the class cannot be broken by an editor normalising the file.
    const INVISIBLE = /[\u0000-\u0020\u00a0\u00ad\u2000-\u200f\u2028\u2029\u202a-\u202e\u2060-\u2064\u206a-\u206f\ufeff]/g;

    /** Return the URL if it is a safe absolute http(s) URL, otherwise null. */
    function safeUrl(value) {
        if (typeof value !== 'string' || !value) return null;
        const probe = value.replace(INVISIBLE, '').toLowerCase();
        if (UNSAFE_SCHEME.test(probe)) return null;
        if (!/^https?:\/\//.test(probe)) return null;
        try {
            const parsed = new URL(value);
            if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
            return parsed.href;
        } catch (err) {
            return null;
        }
    }

    /**
     * Build a link, or a plain span when the URL fails validation.
     *
     * Falling back to a span rather than dropping the element keeps the label
     * visible: a suspicious URL should not make content disappear.
     */
    function link(href, className, text, title) {
        const safe = safeUrl(href);
        if (!safe) return el('span', className, text);
        const anchor = el('a', className, text);
        anchor.setAttribute('href', safe);
        anchor.setAttribute('target', '_blank');
        // noopener/noreferrer: the opened tab must not get a handle on ours.
        anchor.setAttribute('rel', 'noopener noreferrer');
        if (title) anchor.setAttribute('title', title);
        return anchor;
    }

    // YouTube IDs are exactly 11 chars from a fixed alphabet. Validating here
    // as well as server-side means a malformed ID can't be interpolated into a
    // URL the page then offers to the user.
    const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;

    function youtubeUrl(videoId, seconds) {
        if (typeof videoId !== 'string' || !VIDEO_ID.test(videoId)) return null;
        const start = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
        return 'https://www.youtube.com/watch?v=' + videoId + '&t=' + start + 's';
    }

    // ── Markdown ─────────────────────────────────────────────────

    // Inline spans, in precedence order. Code is matched first so that markers
    // inside a code span are shown literally rather than re-parsed.
    const INLINE_RULES = [
        { pattern: /`([^`]+)`/, tag: 'code', className: 'md-code' },
        { pattern: /\*\*([^*]+)\*\*/, tag: 'strong', className: '' },
        { pattern: /__([^_]+)__/, tag: 'strong', className: '' },
        { pattern: /\*([^*]+)\*/, tag: 'em', className: '' },
        { pattern: /_([^_]+)_/, tag: 'em', className: '' },
    ];

    /**
     * Turn one line of markdown into a list of text and element nodes.
     *
     * Recurses on the segment before each match and treats the captured body as
     * plain text. Because the body is assigned with textContent, markup written
     * inside emphasis (`**<img onerror=…>**`) stays inert.
     */
    function inlineNodes(text) {
        if (!text) return [];

        let earliest = null;
        for (let i = 0; i < INLINE_RULES.length; i++) {
            const match = INLINE_RULES[i].pattern.exec(text);
            if (match && (!earliest || match.index < earliest.match.index)) {
                earliest = { match: match, rule: INLINE_RULES[i] };
            }
        }

        if (!earliest) return [document.createTextNode(text)];

        const match = earliest.match;
        const nodes = [];
        if (match.index > 0) {
            nodes.push.apply(nodes, inlineNodes(text.slice(0, match.index)));
        }
        nodes.push(el(earliest.rule.tag, earliest.rule.className, match[1]));
        nodes.push.apply(nodes, inlineNodes(text.slice(match.index + match[0].length)));
        return nodes;
    }

    // Guards against a pathological document (a very long AI response, or a
    // transcript that is mostly list markers) turning into tens of thousands of
    // nodes and freezing the tab.
    const MAX_MD_LINES = 4000;
    const MAX_MD_CHARS = 200000;

    /**
     * Render markdown into a container element built from real nodes.
     *
     * Supported: h1–h3, unordered and ordered lists, blockquotes, fenced code,
     * horizontal rules, paragraphs, and the inline spans above. Deliberately
     * unsupported: links, images, raw HTML — none are needed for study notes,
     * and each is an injection surface. Link syntax degrades to its label text.
     */
    function renderMarkdown(source) {
        const wrapper = el('div', 'md-content');
        if (typeof source !== 'string' || !source.trim()) return wrapper;

        const capped = source.length > MAX_MD_CHARS ? source.slice(0, MAX_MD_CHARS) : source;
        const lines = capped.split(/\r?\n/).slice(0, MAX_MD_LINES);

        let list = null;         // open <ul>/<ol>, if any
        let paragraph = null;    // buffered paragraph lines
        let codeLines = null;    // inside a ``` fence

        function flushParagraph() {
            if (!paragraph || !paragraph.length) { paragraph = null; return; }
            const p = el('p', 'md-p');
            paragraph.forEach((line, i) => {
                if (i > 0) p.appendChild(el('br'));
                append(p, inlineNodes(line));
            });
            wrapper.appendChild(p);
            paragraph = null;
        }

        function flushList() {
            list = null;
        }

        function flushAll() {
            flushParagraph();
            flushList();
        }

        for (let i = 0; i < lines.length; i++) {
            const raw = lines[i];
            const line = raw.trim();

            // Fenced code: contents are taken verbatim as text, never parsed.
            if (/^```/.test(line)) {
                if (codeLines) {
                    const pre = el('pre', 'md-pre');
                    pre.appendChild(el('code', 'md-code-block', codeLines.join('\n')));
                    wrapper.appendChild(pre);
                    codeLines = null;
                } else {
                    flushAll();
                    codeLines = [];
                }
                continue;
            }
            if (codeLines) { codeLines.push(raw); continue; }

            if (!line) { flushAll(); continue; }

            if (/^(?:---|___|\*\*\*)$/.test(line)) {
                flushAll();
                wrapper.appendChild(el('hr', 'md-hr'));
                continue;
            }

            const heading = /^(#{1,6})\s+(.*)$/.exec(line);
            if (heading) {
                flushAll();
                const level = Math.min(heading[1].length, 3);
                const tag = ['h2', 'h3', 'h4'][level - 1];
                append(wrapper.appendChild(el(tag, 'md-h' + (level + 1))), inlineNodes(heading[2]));
                continue;
            }

            const quote = /^>\s?(.*)$/.exec(line);
            if (quote) {
                flushAll();
                append(wrapper.appendChild(el('blockquote', 'md-quote')), inlineNodes(quote[1]));
                continue;
            }

            const bullet = /^[-*+]\s+(.*)$/.exec(line);
            const numbered = /^\d{1,3}[.)]\s+(.*)$/.exec(line);
            if (bullet || numbered) {
                flushParagraph();
                const wanted = bullet ? 'UL' : 'OL';
                if (!list || list.tagName !== wanted) {
                    list = el(wanted.toLowerCase(), bullet ? 'md-ul' : 'md-ol');
                    wrapper.appendChild(list);
                }
                append(list.appendChild(el('li', 'md-li')), inlineNodes((bullet || numbered)[1]));
                continue;
            }

            flushList();
            if (!paragraph) paragraph = [];
            paragraph.push(line);
        }

        // Close anything still open, including an unterminated code fence.
        if (codeLines) {
            const pre = el('pre', 'md-pre');
            pre.appendChild(el('code', 'md-code-block', codeLines.join('\n')));
            wrapper.appendChild(pre);
        }
        flushAll();

        return wrapper;
    }

    // ── Misc helpers ─────────────────────────────────────────────

    /** Zero-padded number, used for the editorial section numerals. */
    function pad(value, width) {
        return String(Math.max(0, Math.floor(Number(value) || 0))).padStart(width || 2, '0');
    }

    /** Coerce untrusted input to a display string with a hard length cap. */
    function str(value, maxLength) {
        if (value === null || value === undefined) return '';
        const out = typeof value === 'string' ? value : String(value);
        return maxLength && out.length > maxLength ? out.slice(0, maxLength) + '…' : out;
    }

    function formatTimestamp(seconds) {
        const total = Math.max(0, Math.floor(Number(seconds) || 0));
        const h = Math.floor(total / 3600);
        const m = Math.floor((total % 3600) / 60);
        const s = total % 60;
        if (h > 0) return h + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
        return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
    }

    global.SafeDOM = {
        el: el,
        clear: clear,
        append: append,
        replace: replace,
        link: link,
        safeUrl: safeUrl,
        youtubeUrl: youtubeUrl,
        renderMarkdown: renderMarkdown,
        pad: pad,
        str: str,
        formatTimestamp: formatTimestamp,
    };
})(window);
