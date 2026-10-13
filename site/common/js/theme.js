/**
 * site/common/js/theme.js
 * ---------------------
 * Light / dark theme: the header toggle and live theme changes. The colours themselves
 * are CSS tokens in ../css/theme.css.
 *
 * The choice ('light' | 'dark', default 'light') is stored in
 * localStorage[THEME_KEY]. The resolved theme is <html data-theme="light|dark">. Each page
 * sets it before first paint with the same inline script in <head>, ahead of its stylesheets
 * (keep the copies in index.html and docs/blog/index.html in sync):
 *
 *   (function () {
 *     var t;
 *     try { t = localStorage.getItem('edge-healer-theme'); } catch (e) {}
 *     if (t !== 'light' && t !== 'dark') t = 'light';
 *     document.documentElement.setAttribute('data-theme', t);
 *   })();
 *
 * and loads this module, which renders the Light / Dark toggle into every [data-theme-toggle]
 * element and syncs other open pages through the storage event. The OS setting
 * (prefers-color-scheme) is deliberately ignored. On every change of the resolved theme it dispatches
 * `themechange` on window (detail: { theme, preference }); charts that compute colours in
 * JS listen for it (onThemeChange) and redraw.
 */

export const THEME_KEY = 'edge-healer-theme';
const CHOICES = [
  { value: 'light', label: 'Light', title: 'Light theme',
    icon: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>' },
  { value: 'dark', label: 'Dark', title: 'Dark theme',
    icon: '<path d="M20.5 14.2A8.5 8.5 0 1 1 9.8 3.5a7 7 0 0 0 10.7 10.7z"/>' },
];
/** The stored preference: 'light' (also when nothing or junk is stored) or 'dark'. */
export function getPreference() {
  let value = null;
  try { value = window.localStorage.getItem(THEME_KEY); } catch (_) { /* storage blocked */ }
  return value === 'dark' ? 'dark' : 'light';
}

/** The theme in effect: 'light' or 'dark'. */
export function currentTheme() {
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

function apply(preference) {
  const theme = preference === 'dark' ? 'dark' : 'light';
  const root = document.documentElement;
  const changed = root.getAttribute('data-theme') !== theme;
  root.setAttribute('data-theme', theme);
  document.querySelectorAll('.theme-toggle button[data-theme-choice]').forEach((b) => {
    b.setAttribute('aria-checked', String(b.dataset.themeChoice === preference));
  });
  if (changed) window.dispatchEvent(new CustomEvent('themechange', { detail: { theme, preference } }));
}

/** Store and apply a preference ('light' | 'dark'; anything else means 'light'). */
export function setPreference(preference) {
  const value = preference === 'dark' ? 'dark' : 'light';
  try { window.localStorage.setItem(THEME_KEY, value); } catch (_) { /* not persisted */ }
  apply(value);
}

/** Calls fn(theme) whenever the resolved theme changes. Returns an unsubscribe function. */
export function onThemeChange(fn) {
  const handler = (ev) => fn(ev.detail.theme);
  window.addEventListener('themechange', handler);
  return () => window.removeEventListener('themechange', handler);
}

/** Renders the Light / Dark radio group into `host`. */
export function mountToggle(host) {
  const group = document.createElement('div');
  group.className = 'theme-toggle';
  group.setAttribute('role', 'radiogroup');
  group.setAttribute('aria-label', 'Colour theme');
  const preference = getPreference();
  for (const c of CHOICES) {
    const b = document.createElement('button');
    b.type = 'button';
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(c.value === preference));
    b.setAttribute('aria-label', c.title);
    b.dataset.themeChoice = c.value;
    b.title = c.title;
    b.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${c.icon}</svg><span class="theme-toggle__label">${c.label}</span>`;
    b.addEventListener('click', () => setPreference(c.value));
    group.append(b);
  }
  group.addEventListener('keydown', (ev) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[ev.key];
    if (!step) return;
    const i = CHOICES.findIndex((c) => c.value === getPreference());
    const next = CHOICES[(i + step + CHOICES.length) % CHOICES.length].value;
    setPreference(next);
    group.querySelector(`[data-theme-choice="${next}"]`).focus();
    ev.preventDefault();
  });
  host.replaceWith(group);
  return group;
}

// Pick up changes made in other tabs.
window.addEventListener('storage', (ev) => {
  if (ev.key === THEME_KEY || ev.key === null) apply(getPreference());
});

document.querySelectorAll('[data-theme-toggle]').forEach(mountToggle);
apply(getPreference()); // in case the inline script is missing or storage changed meanwhile
