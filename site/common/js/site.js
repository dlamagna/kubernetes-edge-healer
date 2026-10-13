/**
 * site/common/js/site.js
 * ----------------------
 * The paper and code base, in one place. Every page takes the paper / poster / GitHub links and
 * the citation from here (header links, footer, citation block, docs pages), so a change of URL
 * is made once. No side effects: safe to import from anywhere.
 */

export const TITLE = 'Edge-Healer: Decentralising Kubernetes with Edge Self-Healing';

export const AUTHORS = [
  { name: 'Davide Lamagna', short: 'Lamagna', affiliation: 'Universitat Politècnica de Catalunya, Barcelona' },
];

export const VENUE = "Facultat d'Informàtica de Barcelona, Universitat Politècnica de Catalunya";
export const VENUE_SHORT = 'UPC · FIB';
export const YEAR = 2025;

/** The paper and the poster, published next to the site by scripts/build-site.sh (paths under site/). */
export const PAPER_PATH = 'assets/lamagna_edge_healer_final.pdf';
export const POSTER_PATH = 'assets/lamagna_edge_healer_poster.pdf';

export const REPO_URL = 'https://github.com/dlamagna/kubernetes-edge-healer';
export const REPO_BRANCH = 'main';

/** The public site. */
export const SITE_URL = 'https://dlamagna.github.io/kubernetes-edge-healer/';

/** A file or directory in the code base on GitHub, e.g. repoUrl('src/scheduler.py'). */
export function repoUrl(path = '') {
  if (!path) return REPO_URL;
  const kind = path.endsWith('/') ? 'tree' : 'blob';
  return `${REPO_URL}/${kind}/${REPO_BRANCH}/${path.replace(/\/$/, '')}`;
}

export const BIBTEX = `@misc{lamagna2025edgehealer,
  title        = {Edge-Healer: Decentralising Kubernetes with Edge Self-Healing},
  author       = {Lamagna, Davide},
  year         = {2025},
  institution  = {Universitat Polit{\\\`e}cnica de Catalunya},
  howpublished = {\\url{${REPO_URL}}},
}`;
