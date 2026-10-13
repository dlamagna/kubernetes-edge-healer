/**
 * site/common/js/nav.js
 * ---------------------
 * Site chrome shared by every page: the header links with the Docs menu, the paper footer, the
 * BibTeX block, and links / text filled from the paper metadata in ./site.js. Adapted from the
 * agentraffic site (github.com/dlamagna/agentraffic, ui/common/js/nav.js).
 *
 * Load it as a module; on import it fills every placeholder on the page:
 *
 *   <nav data-site-nav="home" aria-label="Site"></nav>
 *       header links for page "home" (see MENUS): Home · Demo · Docs ▾ · Paper ↗ · Poster ↗ · Code ↗
 *   <footer data-site-footer></footer>          paper title, author, venue, PDFs, GitHub, cite
 *   <div data-site-cite></div>                  BibTeX with a copy button
 *   <a data-site-href="paper | poster | repo | repo:<path> | cite">   href from site.js
 *   <span data-site-text="title | venue | authors">                  text from site.js
 *
 * Links are made relative to the page (this file sits in site/common/js/, two levels below
 * site/), so the site works from any sub-path.
 *
 * Docs menu: a disclosure button (aria-expanded / aria-controls) over a list of links.
 * ArrowDown / ArrowUp open it and move between entries (Home / End jump), Esc closes it and
 * returns focus to the button; it also closes on a click outside or when focus leaves it.
 */

import { AUTHORS, BIBTEX, PAPER_PATH, POSTER_PATH, REPO_URL, TITLE, VENUE, VENUE_SHORT, YEAR, repoUrl } from './site.js';

const ROOT = new URL('../../', import.meta.url); // site/

/** Pages a header can link to; paths are relative to site/. */
export const PAGES = {
  home: { label: 'Home', path: '', title: 'The project page' },
  demo: { label: 'Demo', path: '#demo', title: 'The interactive demo: cut the WAN, kill a pod, watch the cluster heal' },
};

/** The Docs menu, in order. Add an entry here when a new docs page is written. */
export const DOCS = [
  { id: 'blog', label: 'Write-up', path: 'docs/blog/', description: 'The project in plain words' },
  { id: 'reproduce', label: 'Reproduce the experiment', path: 'docs/blog/#reproduce', description: 'Run the prototype on a KinD cluster' },
];

/** Header links per page (data-site-nav value), before Docs / Paper / Poster / Code. */
const MENUS = {
  home: ['home', 'demo'],
  'docs-blog': ['home', 'demo'],
};

const SVG_NS = 'http://www.w3.org/2000/svg';
const ICONS = {
  caret: '<path d="M6 9l6 6 6-6"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
};

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === 'text') node.textContent = v;
    else node.setAttribute(k, v === true ? '' : v);
  }
  node.append(...[].concat(children));
  return node;
}

function icon(name, cls) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', cls);
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  svg.innerHTML = ICONS[name];
  return svg;
}

/** A path under site/ as an href relative to the current page ('' -> the site/ index). */
export function siteHref(path) {
  const target = new URL(path, ROOT);
  const here = new URL('.', window.location.href);
  if (here.origin !== ROOT.origin || !here.pathname.startsWith(ROOT.pathname)) return target.href;
  const depth = here.pathname.slice(ROOT.pathname.length).split('/').filter(Boolean).length;
  const [file, hash = ''] = path.split('#');
  return ('../'.repeat(depth) + file || './') + (hash ? `#${hash}` : '');
}

function isCurrent(path) {
  if (path.includes('#')) return false; // in-page anchors are never "the current page"
  const strip = (p) => p.replace(/index\.html$/, '');
  return strip(new URL(path, ROOT).pathname) === strip(window.location.pathname);
}

function externalLink(label, href, title) {
  return el('a', { class: 'site-link site-link--ext', href, target: '_blank', rel: 'noopener', title }, [
    label,
    icon('external', 'site-link__icon'),
    el('span', { class: 'site-sr', text: ' (opens in a new tab)' }),
  ]);
}

// ---------------------------------------------------------------------------
// Docs ▾
// ---------------------------------------------------------------------------

let menuCount = 0;

/**
 * A disclosure menu. `kind` names it: every element carries the shared .site-menu* class
 * (styles) plus .<kind>-menu*. `entries`: [{ id, label, description, path }].
 */
function dropdown(kind, label, entries) {
  const k = (part) => `site-menu${part} ${kind}-menu${part}`;
  const id = `site-${kind}-menu-${++menuCount}`;
  const wrap = el('div', { class: k('') });
  const current = entries.some((d) => isCurrent(d.path));
  const button = el('button', {
    type: 'button', class: `${k('__button')}${current ? ' is-current' : ''}`,
    'aria-expanded': 'false', 'aria-controls': id,
  }, [label, icon('caret', 'site-menu__caret')]);
  const panel = el('div', { class: k('__panel'), id, hidden: true });
  const list = el('ul', { class: 'site-menu__list' });
  for (const item of entries) {
    const a = el('a', { class: k('__item'), href: siteHref(item.path), 'data-doc': item.id }, [
      el('span', { class: k('__label'), text: item.label }),
    ]);
    if (item.description) a.title = item.description;
    if (isCurrent(item.path)) a.setAttribute('aria-current', 'page');
    list.append(el('li', {}, a));
  }
  panel.append(list);
  wrap.append(button, panel);

  const items = () => [...panel.querySelectorAll('a[href]')];
  const isOpen = () => button.getAttribute('aria-expanded') === 'true';

  function place() {
    // keep the panel inside the viewport (it hangs from the button's left edge)
    panel.style.left = '0px';
    const margin = 8;
    const vw = document.documentElement.clientWidth;
    const r = panel.getBoundingClientRect();
    let shift = 0;
    if (r.right > vw - margin) shift = vw - margin - r.right;
    if (r.left + shift < margin) shift = margin - r.left;
    panel.style.left = `${shift}px`;
  }
  function onDocPointer(ev) { if (!wrap.contains(ev.target)) close(); }
  function onDocKey(ev) { if (ev.key === 'Escape') { close(); button.focus(); } }
  function open() {
    if (isOpen()) return;
    button.setAttribute('aria-expanded', 'true');
    panel.hidden = false;
    place();
    document.addEventListener('pointerdown', onDocPointer, true);
    document.addEventListener('keydown', onDocKey);
    window.addEventListener('resize', place);
  }
  function close() {
    if (!isOpen()) return;
    button.setAttribute('aria-expanded', 'false');
    panel.hidden = true;
    document.removeEventListener('pointerdown', onDocPointer, true);
    document.removeEventListener('keydown', onDocKey);
    window.removeEventListener('resize', place);
  }

  button.addEventListener('click', () => (isOpen() ? close() : open()));
  panel.addEventListener('click', (ev) => { if (ev.target.closest('a')) close(); });
  wrap.addEventListener('focusout', (ev) => {
    if (ev.relatedTarget && !wrap.contains(ev.relatedTarget)) close();
  });
  wrap.addEventListener('keydown', (ev) => {
    const list = items();
    const i = list.indexOf(document.activeElement);
    let next = null;
    if (ev.key === 'ArrowDown') next = i < 0 ? 0 : (i + 1) % list.length;
    else if (ev.key === 'ArrowUp') next = i < 0 ? list.length - 1 : (i - 1 + list.length) % list.length;
    else if ((ev.key === 'Home' || ev.key === 'End') && i >= 0) next = ev.key === 'Home' ? 0 : list.length - 1;
    else if (ev.key === 'Escape' && isOpen()) {
      close();
      button.focus();
      ev.preventDefault();
      ev.stopPropagation();
      return;
    }
    if (next === null) return;
    open();
    list[next].focus();
    ev.preventDefault();
  });
  return wrap;
}

/** Fills `host` with the header links of page `pageId` (a MENUS key). */
export function mountNav(host, pageId = host.dataset.siteNav) {
  const links = (MENUS[pageId] || MENUS.home).map((id) => {
    const page = PAGES[id];
    const a = el('a', { class: 'site-link', href: siteHref(page.path), title: page.title, text: page.label });
    if (isCurrent(page.path)) {
      a.setAttribute('aria-current', 'page');
      a.classList.add('active');
    }
    return a;
  });
  host.classList.add('site-links');
  if (!host.hasAttribute('aria-label')) host.setAttribute('aria-label', 'Site');
  host.replaceChildren(
    ...links,
    dropdown('docs', 'Docs', DOCS),
    externalLink('Paper', siteHref(PAPER_PATH), 'Read the paper (PDF)'),
    externalLink('Poster', siteHref(POSTER_PATH), 'The poster (PDF)'),
    externalLink('Code', REPO_URL, 'The code on GitHub: github.com/dlamagna/kubernetes-edge-healer'),
  );
}

// ---------------------------------------------------------------------------
// Footer, citation, filled links
// ---------------------------------------------------------------------------

/** Fills `host` with the paper footer: title, author and venue, PDFs, GitHub, cite. */
export function mountFooter(host) {
  const sep = () => el('span', { class: 'paper-footer__sep', 'aria-hidden': 'true', text: '·' });
  const box = el('div', { class: 'paper-footer' }, [
    el('p', { class: 'paper-footer__title' }, el('strong', { text: TITLE })),
    el('p', { class: 'paper-footer__meta', text: `${AUTHORS.map((a) => a.name).join(', ')} · ${VENUE}, ${YEAR}` }),
    el('p', { class: 'paper-footer__links' }, [
      el('a', { href: siteHref(PAPER_PATH), text: 'Paper (PDF)' }), sep(),
      el('a', { href: siteHref(POSTER_PATH), text: 'Poster (PDF)' }), sep(),
      el('a', { href: REPO_URL, text: 'Code on GitHub' }), sep(),
      el('a', { href: siteHref('docs/blog/#cite'), text: 'Cite' }), sep(),
      el('a', { href: siteHref('docs/blog/'), text: 'Write-up' }),
    ]),
  ]);
  host.classList.add('site-footer-host');
  host.replaceChildren(box);
}

async function copyText(text, fallbackNode) {
  try {
    await navigator.clipboard.writeText(text);
    return 'copied';
  } catch (_) { /* no clipboard API (insecure context) or refused: select instead */ }
  const range = document.createRange();
  range.selectNodeContents(fallbackNode);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(range);
  try {
    if (document.execCommand('copy')) return 'copied';
  } catch (_) { /* leave it selected */ }
  return 'selected';
}

/** Fills `host` with the BibTeX entry and a copy button. */
export function mountCite(host) {
  const code = el('code', { text: BIBTEX });
  const pre = el('pre', { class: 'cite-block__code', tabindex: '0', 'aria-label': 'BibTeX entry' }, code);
  const status = el('span', { class: 'cite-block__status', role: 'status' });
  const button = el('button', { type: 'button', class: 'cite-block__copy', text: 'Copy BibTeX' });
  let timer = 0;
  button.addEventListener('click', async () => {
    const result = await copyText(BIBTEX, code);
    status.textContent = result === 'copied' ? 'Copied to the clipboard' : 'Selected: press Ctrl+C (⌘C) to copy';
    button.textContent = result === 'copied' ? 'Copied' : 'Copy BibTeX';
    button.dataset.state = result;
    clearTimeout(timer);
    timer = setTimeout(() => {
      button.textContent = 'Copy BibTeX';
      delete button.dataset.state;
      status.textContent = '';
    }, 2500);
  });
  host.classList.add('cite-block');
  host.replaceChildren(
    el('div', { class: 'cite-block__bar' }, [el('span', { class: 'cite-block__label', text: 'BibTeX' }), status, button]),
    pre,
  );
}

const HREFS = {
  paper: () => siteHref(PAPER_PATH),
  poster: () => siteHref(POSTER_PATH),
  repo: () => REPO_URL,
  cite: () => siteHref('docs/blog/#cite'),
};
const TEXTS = {
  title: TITLE,
  venue: `${VENUE_SHORT} · ${YEAR}`,
  authors: AUTHORS.map((a) => a.name).join(', '),
};

/** Fills [data-site-href] / [data-site-text] (and [data-site-nav], footer, cite) under `root`. */
export function mountSite(root = document) {
  root.querySelectorAll('[data-site-nav]').forEach((host) => mountNav(host));
  root.querySelectorAll('[data-site-footer]').forEach(mountFooter);
  root.querySelectorAll('[data-site-cite]').forEach(mountCite);
  root.querySelectorAll('[data-site-href]').forEach((a) => {
    const key = a.dataset.siteHref;
    const href = key.startsWith('repo:') ? repoUrl(key.slice(5)) : HREFS[key]?.();
    if (href) a.href = href;
  });
  root.querySelectorAll('[data-site-text]').forEach((node) => {
    const text = TEXTS[node.dataset.siteText];
    if (text) node.textContent = text;
  });
}

mountSite();
