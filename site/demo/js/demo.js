/**
 * site/demo/js/demo.js
 * --------------------
 * The interactive demo on the home page: a small, self-contained simulation of the testbed in the
 * paper (a control plane and three workers, one busybox-spread replica per worker, an edge-healer
 * pod with a Serf sidecar on every worker). Nothing here talks to a real cluster.
 *
 * What it models, and where it comes from in the code base:
 *   - Gossip (src/gossip.py): every agent broadcasts its free CPU about once a second; a peer's
 *     entry in an agent's map is updated when the message arrives, so views can be stale. Pausing
 *     gossip stops the messages between nodes; each agent's own entry stays current.
 *   - Offline check (src/main.py, is_offline): a Pod loss first checks the API server with a 1 s
 *     timeout; only when that fails does the agent try to restore the Pod.
 *   - Bidding (src/scheduler.py, bid_and_bind): an agent wins unless some peer's gossiped free CPU
 *     is strictly higher than its own. Ties therefore give two winners (a duplicate), and stale
 *     views can give none.
 *   - Reconciliation (paper, Sec. III): when the WAN returns, locally bound Pods are pushed to the
 *     API server; a second bind of the same Pod is a 409 conflict (bind_conflicts_total) and the
 *     duplicate is removed. Losses nobody restored go back to the ReplicaSet controller.
 *
 * Timings other than the 1 s offline check are illustrative, not measurements.
 */

const NODES = ['worker1', 'worker2', 'worker3'];
const CAPACITY = 1000;      // millicores per worker
const HEALER_REQ = 50;      // edge-healer container request (config/daemonset.yaml)
const POD_CPU = 100;        // what one busybox-spread replica uses, in this demo
const DESIRED = 3;          // demo/busybox-spread.yaml: replicas: 3

const T = {
  gossipEvery: 1.0,     // s between an agent's free-CPU broadcasts
  gossipTravel: 0.35,   // s for a gossip message to reach a peer (animation)
  apiTravel: 0.3,       // s for a message over the WAN link (animation)
  offlineTimeout: 1.0,  // is_offline(timeout=1.0)
  localBind: 0.05,      // optimistic local bind
  containerStart: 0.25, // Pending -> Running
  cpCreate: 0.3,        // ReplicaSet controller creates the replacement
  cpSchedule: 0.5,      // kube-scheduler picks a node
  terminate: 0.4,       // a killed Pod fades out
  drift: 2.0,           // s between background-load changes
};

const BASE_LOAD = { worker1: 300, worker2: 380, worker3: 220 };
const STRESS = 350;

const POD_CHARS = 'bcdfghjklmnpqrstvwxz2456789';
const RS_HASH = '5d8f7b9c4';

// ---------------------------------------------------------------------------
// Geometry of the cluster drawing (viewBox 0 0 720 450)
// ---------------------------------------------------------------------------

const CP = { x: 250, y: 14, w: 220, h: 74 };
const W = 180;
const H = 180;
const BOX = { worker1: { x: 20, y: 210 }, worker2: { x: 270, y: 210 }, worker3: { x: 520, y: 210 } };
const CP_PORT = { x: CP.x + CP.w / 2, y: CP.y + CP.h };
const top = (n) => ({ x: BOX[n].x + W / 2, y: BOX[n].y });

/** Point at fraction f along the WAN line between the control plane and node n. */
function apiPoint(n, f) {
  const a = CP_PORT;
  const b = top(n);
  return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f };
}

/** Point at fraction f along the gossip path from node a to node b. */
function gossipPoint(a, b, f) {
  const key = [a, b].sort().join('-');
  const fwd = a < b;
  const g = fwd ? f : 1 - f;
  if (key === 'worker1-worker3') { // arc under the workers
    const p0 = { x: 110, y: 390 }; const p1 = { x: 360, y: 450 }; const p2 = { x: 610, y: 390 };
    const u = 1 - g;
    return { x: u * u * p0.x + 2 * u * g * p1.x + g * g * p2.x, y: u * u * p0.y + 2 * u * g * p1.y + g * g * p2.y };
  }
  const [l, r] = key === 'worker1-worker2' ? [{ x: 200, y: 300 }, { x: 270, y: 300 }] : [{ x: 450, y: 300 }, { x: 520, y: 300 }];
  return { x: l.x + (r.x - l.x) * g, y: l.y };
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let S;
let uid = 0;

function podSuffix() {
  let s = '';
  for (let i = 0; i < 5; i++) s += POD_CHARS[Math.floor(Math.random() * POD_CHARS.length)];
  return s;
}
const newPodName = () => `busybox-spread-${RS_HASH}-${podSuffix()}`;

function freshState(keep = {}) {
  const nodes = {};
  for (const n of NODES) nodes[n] = { base: BASE_LOAD[n], load: BASE_LOAD[n], stress: 0, nextGossip: 0.15 + NODES.indexOf(n) * 0.3, badge: null };
  const views = {};
  for (const n of NODES) views[n] = {};
  const s = {
    t: 0, paused: false, speed: keep.speed || 1,
    wan: true, healer: true, gossip: true,
    nodes, views,
    pods: [], msgs: [], queue: [], losses: [],
    metrics: { peerUpdates: 0, bindConflicts: 0, lastRestore: null, gap: 0 },
    nextDrift: T.drift, step: 0, scenario: null, dirty: true,
  };
  for (const n of NODES) s.pods.push({ id: ++uid, name: newPodName(), node: n, state: 'running', local: false });
  return s;
}

const free = (n) => CAPACITY - HEALER_REQ - S.nodes[n].load - S.nodes[n].stress
  - POD_CPU * S.pods.filter((p) => p.node === n && p.state !== 'terminating').length;
const runningCount = () => S.pods.filter((p) => p.state === 'running').length;

/** Run fn after `delay` seconds of simulated time. */
function at(delay, fn) { S.queue.push({ t: S.t + delay, fn }); }

// ---------------------------------------------------------------------------
// Log, narration, steps
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
let logEl; let sayEl; let stepsEl;

function log(node, source, text, kind = '') {
  const li = document.createElement('li');
  if (kind) li.dataset.kind = kind;
  const time = document.createElement('span');
  time.className = 'demo-log__t';
  time.textContent = `${S.t.toFixed(2).padStart(6, ' ')}s`;
  const who = document.createElement('span');
  who.className = 'demo-log__who';
  who.textContent = node ? `${node} ${source}` : source;
  const msg = document.createElement('span');
  msg.className = 'demo-log__msg';
  msg.textContent = text;
  li.append(time, who, msg);
  const stick = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 24;
  logEl.append(li);
  while (logEl.children.length > 160) logEl.firstElementChild.remove();
  if (stick) logEl.scrollTop = logEl.scrollHeight;
}

function say(text) { sayEl.textContent = text; }

function setStep(step) {
  S.step = step;
  for (const li of stepsEl.children) {
    const k = Number(li.dataset.step);
    li.classList.toggle('is-current', k === step);
    li.classList.toggle('is-done', step > 0 && k < step);
  }
}

function badge(n, text, kind, dur = 1.8) {
  S.nodes[n].badge = { text, kind, until: S.t + dur };
  S.dirty = true;
}

// ---------------------------------------------------------------------------
// Messages (gossip, API calls, offline probes)
// ---------------------------------------------------------------------------

function send(msg) {
  S.msgs.push({ id: ++uid, t0: S.t, ...msg });
}

function gossipRound(n) {
  const v = free(n);
  S.views[n][n] = { v, t: S.t };
  for (const peer of NODES) {
    if (peer === n) continue;
    send({
      kind: 'gossip', from: n, to: peer, dur: T.gossipTravel,
      arrive() {
        if (!S.healer || !S.gossip) return;
        S.views[peer][n] = { v, t: S.t };
        S.metrics.peerUpdates += 1;
      },
    });
  }
}

// ---------------------------------------------------------------------------
// Pod loss and the three ways back: control plane, edge-healer, or not at all
// ---------------------------------------------------------------------------

function killPod(pod) {
  if (!pod || pod.state === 'terminating') return;
  pod.state = 'terminating';
  S.dirty = true;
  const loss = { id: ++uid, name: pod.name, node: pod.node, t0: S.t, resolved: false, by: null };
  S.losses.push(loss);
  at(T.terminate, () => { S.pods = S.pods.filter((p) => p !== pod); S.dirty = true; });
  log(pod.node, 'kubelet', `container of default/${pod.name} stopped and removed`, 'loss');

  if (S.wan) {
    controlPlaneRestore(loss, true);
    if (S.healer) log(null, 'edge-healer (all)', `POD GONE for default/${pod.name}: control-plane reachable, skipping`);
    say(`A pod on ${pod.node} is gone. The WAN is up, so the control plane recreates it as usual.`);
  } else if (S.healer) {
    setStep(2);
    healerRestore(loss);
  } else {
    setStep(2);
    log(pod.node, 'kubelet', 'cannot reach the API server: no controller will replace the Pod', 'warn');
    say(`A pod on ${pod.node} is gone, the WAN is cut and edge-healer is off: nothing can recreate it until the control plane is back.`);
  }
}

function resolve(loss, by) {
  if (loss.resolved) return;
  loss.resolved = true;
  loss.by = by;
  S.metrics.lastRestore = { secs: S.t - loss.t0, by };
}

/** ReplicaSet controller + kube-scheduler: the normal, centralised path. */
function controlPlaneRestore(loss, report) {
  const create = () => {
    if (!S.wan || loss.resolved) return;
    const name = newPodName();
    log(null, 'controller-manager', `ReplicaSet busybox-spread: ${runningCount()}/${DESIRED} replicas, creating default/${name}`);
    at(T.cpSchedule, () => {
      if (!S.wan || loss.resolved) return;
      const count = (n) => S.pods.filter((p) => p.node === n && p.state !== 'terminating').length;
      const target = [...NODES].sort((a, b) => count(a) - count(b) || free(b) - free(a))[0];
      log(null, 'kube-scheduler', `bound default/${name} to ${target}`);
      send({
        kind: 'api', from: 'cp', to: target, dur: T.apiTravel,
        arrive() {
          if (!S.wan) { log(target, 'kubelet', `bind for default/${name} lost: WAN cut`, 'warn'); return; }
          const pod = { id: ++uid, name, node: target, state: 'pending', local: false };
          S.pods.push(pod);
          S.dirty = true;
          at(T.containerStart, () => {
            if (!S.pods.includes(pod)) return;
            pod.state = 'running';
            S.dirty = true;
            resolve(loss, 'control plane');
            log(target, 'kubelet', `default/${name} Running`, 'ok');
          });
        },
      });
    });
  };
  if (report) {
    send({ kind: 'api', from: loss.node, to: 'cp', dur: T.apiTravel, arrive: () => { if (S.wan) at(T.cpCreate - T.apiTravel, create); } });
  } else {
    at(T.cpCreate, create);
  }
}

/** edge-healer: every agent checks the API (1 s timeout), then bids with its gossip view. */
function healerRestore(loss) {
  say(`A pod on ${loss.node} is gone and the API server is unreachable. Every edge-healer agent checks the API (1 s timeout)…`);
  for (const n of NODES) {
    log(n, 'edge-healer', `POD GONE event fired for default/${loss.name}`);
    send({ kind: 'probe', from: n, to: 'cp', dur: T.offlineTimeout });
  }
  setStep(3);
  at(T.offlineTimeout, () => {
    if (S.wan || !S.healer || loss.resolved) return; // the WAN came back meanwhile: the control plane takes over
    setStep(4);
    const winners = [];
    for (const n of NODES) {
      log(n, 'edge-healer', 'control-plane check failed (assuming offline): TimeoutError');
      const view = S.views[n];
      const mine = view[n]?.v ?? 0;
      const better = Object.entries(view).filter(([peer, e]) => peer !== n && e.v > mine);
      if (better.length) {
        log(n, 'scheduler', `lost bid for default/${loss.name} (mine ${mine}m < ${better.map(([p, e]) => `${p} ${e.v}m`).join(', ')})`);
        badge(n, 'lost bid', 'lost');
      } else {
        winners.push(n);
      }
    }
    if (!winners.length) {
      say('No agent won the bid: their gossip views disagree (try resuming gossip). The pod stays lost until the WAN returns.');
      log(null, 'edge-healer (all)', `no agent won the bid for default/${loss.name}; it will be recreated when the control plane is back`, 'warn');
      return;
    }
    setStep(5);
    for (const n of winners) {
      const pod = { id: ++uid, name: loss.name, node: n, state: 'pending', local: true, loss: loss.id };
      at(T.localBind, () => {
        S.pods.push(pod);
        S.dirty = true;
        log(n, 'scheduler', `won bid – bound pod default/${loss.name} to ${n} in ${T.localBind.toFixed(3)}s`, 'ok');
        badge(n, 'won bid', 'won', 2.4);
        at(T.containerStart, () => {
          if (!S.pods.includes(pod)) return;
          pod.state = 'running';
          S.dirty = true;
          resolve(loss, 'edge-healer');
          log(n, 'edge-healer', `Restored default/${loss.name} in ${(S.t - loss.t0).toFixed(3)}s`, 'ok');
        });
      });
    }
    const who = winners.length > 1 ? `${winners.slice(0, -1).join(', ')} and ${winners.at(-1)}` : winners[0];
    say(winners.length > 1
      ? `${who} each believed no peer had more free CPU (a tie, or views gone stale), so each bound the pod: `
        + 'duplicates, to be reconciled when the WAN returns.'
      : `${who} has the most free CPU in its gossip view, wins the bid and binds the replacement locally. No scheduler involved.`);
  });
}

/** WAN back: push local binds, drop duplicates (409), hand unrestored losses to the control plane. */
function reconcile() {
  setStep(6);
  const local = S.pods.filter((p) => p.local);
  const synced = new Set(S.pods.filter((p) => !p.local).map((p) => p.name));
  let delay = 0.2;
  for (const pod of local) {
    at(delay, () => {
      if (!S.pods.includes(pod)) return;
      send({
        kind: 'api', from: pod.node, to: 'cp', dur: T.apiTravel,
        arrive() {
          if (!S.wan || !S.pods.includes(pod)) return;
          if (synced.has(pod.name)) {
            S.metrics.bindConflicts += 1;
            log(pod.node, 'edge-healer', `409 Conflict on /binding for default/${pod.name}: duplicate, deleting`, 'warn');
            pod.state = 'terminating';
            S.dirty = true;
            at(T.terminate, () => { S.pods = S.pods.filter((p) => p !== pod); S.dirty = true; });
          } else {
            synced.add(pod.name);
            pod.local = false;
            S.dirty = true;
            log(pod.node, 'edge-healer', `pushed local bind of default/${pod.name} to the API server`, 'ok');
          }
        },
      });
    });
    delay += 0.15;
  }
  at(delay + T.apiTravel + 0.1, () => {
    const open = S.losses.filter((l) => !l.resolved && !S.pods.some((p) => p.name === l.name && p.state !== 'terminating'));
    for (const l of open) controlPlaneRestore(l, false);
    if (!local.length && !open.length) setStep(0);
  });
}

// ---------------------------------------------------------------------------
// Toggles
// ---------------------------------------------------------------------------

function setWan(up, quiet = false) {
  if (S.wan === up) return;
  S.wan = up;
  S.dirty = true;
  if (!up) {
    setStep(1);
    log(null, 'iptables', 'OUTPUT -p tcp --dport 6443 -j DROP: workers cut off from the API server', 'warn');
    if (!quiet) say('The WAN is cut: the workers can no longer reach the API server. Now kill a pod.');
  } else {
    log(null, 'iptables', 'rule deleted: API server reachable again', 'ok');
    if (!quiet) say('The WAN is back. Locally bound pods are pushed to the API server; duplicates are removed.');
    reconcile();
  }
  syncControls();
}

function setHealer(on) {
  if (S.healer === on) return;
  S.healer = on;
  S.dirty = true;
  if (!on) for (const n of NODES) S.views[n] = {};
  log(null, 'kubectl', on ? 'apply -f config/daemonset.yaml: edge-healer + serf on every worker' : 'delete ds/edge-healer: no agents, no gossip');
  say(on ? 'edge-healer is deployed again: one agent and one Serf sidecar per worker.'
    : 'edge-healer is off: this is plain Kubernetes. Cut the WAN and kill a pod to see the "freeze-brain" problem.');
  syncControls();
}

function setGossip(on) {
  if (S.gossip === on) return;
  S.gossip = on;
  log(null, 'serf', on ? 'gossip between the nodes resumed' : 'gossip between the nodes paused: views of the peers will go stale');
  say(on ? 'Gossip resumed: the free-CPU maps converge again within a second or so.'
    : 'Gossip is paused: each agent still knows its own free CPU, but its view of the others goes stale. '
      + 'Add or remove load on the node with the most free CPU, then kill a pod: the agents may disagree.');
  syncControls();
}

function toggleStress(n) {
  S.nodes[n].stress = S.nodes[n].stress ? 0 : STRESS;
  S.dirty = true;
  log(n, 'load', S.nodes[n].stress ? `+${STRESS}m CPU load` : 'extra CPU load removed');
  syncControls();
}

// ---------------------------------------------------------------------------
// The guided run: the experiment workflow of the paper (Fig. 3)
// ---------------------------------------------------------------------------

function playScenario() {
  reset();
  S.scenario = true;
  for (const n of NODES) S.nodes[n].base = S.nodes[n].load = { worker1: 420, worker2: 360, worker3: 180 }[n];
  S.dirty = true;
  say('Three workers, one busybox-spread replica each. The Serf sidecars gossip each node\'s free CPU about once a second.');
  const steps = [
    [3.2, () => setWan(false, true) || say('Step 1: block the API (iptables DROP on :6443). The workers are now on their own.')],
    [3.0, () => {
      const pod = S.pods.find((p) => p.node === 'worker2');
      killPod(pod);
    }],
    [5.5, () => say('worker3 now runs the replacement. Note the "local" tag: the API server does not know about it yet.')],
    [3.5, () => setWan(true, true) || say('Step 6: restore the WAN. worker3 pushes its local bind to the API server: the cluster is consistent again.')],
    [3.0, () => {
      S.scenario = null;
      say('Done. Try it yourself: switch edge-healer off, cut the WAN and kill a pod. The replica stays lost until the WAN returns.');
      syncControls();
    }],
  ];
  let t = 0;
  for (const [d, fn] of steps) { t += d; at(t, () => { if (S.scenario) fn(); }); }
  syncControls();
}

/** Any manual action ends the guided run (the simulation itself carries on). */
function manual(fn) {
  return (...args) => {
    if (S.scenario) { S.scenario = null; say(''); }
    fn(...args);
    syncControls();
  };
}

function reset() {
  S = freshState({ speed: S?.speed });
  logEl.replaceChildren();
  setStep(0);
  say('Cut the WAN, then kill a pod. Or press "Play the experiment" for a guided run.');
  log(null, 'kind', `cluster edge-healer: control plane + ${NODES.join(', ')}; deployment busybox-spread (${DESIRED} replicas)`);
  syncControls();
}

// ---------------------------------------------------------------------------
// Simulation step
// ---------------------------------------------------------------------------

function step(dt) {
  S.t += dt;

  // gossip
  for (const n of NODES) {
    const node = S.nodes[n];
    if (S.t >= node.nextGossip) {
      node.nextGossip = S.t + T.gossipEvery + (Math.random() - 0.5) * 0.2;
      if (S.healer && S.gossip) gossipRound(n);
      else if (S.healer) S.views[n][n] = { v: free(n), t: S.t }; // no gossip between nodes, but an agent still knows its own CPU
    }
  }

  // background load drifts a little
  if (S.t >= S.nextDrift) {
    S.nextDrift = S.t + T.drift;
    for (const n of NODES) {
      const node = S.nodes[n];
      node.load = Math.round(Math.min(node.base + 20, Math.max(node.base - 20, node.load + (Math.random() - 0.5) * 16)));
    }
    S.dirty = true;
  }

  // scheduled events (they may schedule more)
  for (;;) {
    const due = S.queue.filter((e) => e.t <= S.t);
    if (!due.length) break;
    S.queue = S.queue.filter((e) => e.t > S.t);
    due.sort((a, b) => a.t - b.t).forEach((e) => e.fn());
  }

  // messages
  const done = S.msgs.filter((m) => S.t - m.t0 >= m.dur);
  S.msgs = S.msgs.filter((m) => S.t - m.t0 < m.dur);
  done.forEach((m) => m.arrive?.());

  // badges
  for (const n of NODES) if (S.nodes[n].badge && S.t > S.nodes[n].badge.until) { S.nodes[n].badge = null; S.dirty = true; }

  if (runningCount() < DESIRED) S.metrics.gap += dt;
}

// ---------------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------------

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function drawStatic() {
  const out = [];
  const cut = !S.wan;

  // WAN lines
  for (const n of NODES) {
    const a = CP_PORT; const b = top(n);
    out.push(`<line class="wan${cut ? ' wan--cut' : ''}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"/>`);
    if (cut) {
      const m = apiPoint(n, 0.5);
      out.push(`<path class="wan-x" d="M${m.x - 7} ${m.y - 7}L${m.x + 7} ${m.y + 7}M${m.x + 7} ${m.y - 7}L${m.x - 7} ${m.y + 7}"/>`);
    }
  }
  out.push(`<text class="wan-label${cut ? ' wan-label--cut' : ''}" x="476" y="112">${cut ? 'iptables DROP :6443' : 'WAN · API calls :6443'}</text>`);

  // gossip ring
  const g = S.healer ? (S.gossip ? 'gossip' : 'gossip gossip--paused') : 'gossip gossip--off';
  out.push(`<line class="${g}" x1="200" y1="300" x2="270" y2="300"/>`);
  out.push(`<line class="${g}" x1="450" y1="300" x2="520" y2="300"/>`);
  out.push(`<path class="${g}" d="M110 390 Q 360 450 610 390"/>`);
  if (S.healer) out.push(`<text class="gossip-label" x="360" y="444">serf gossip: free CPU${S.gossip ? '' : ' (paused)'}</text>`);

  // control plane
  out.push(`<g class="cp${cut ? ' cp--cut' : ''}">
    <rect x="${CP.x}" y="${CP.y}" width="${CP.w}" height="${CP.h}" rx="8"/>
    <text class="cp-title" x="${CP.x + CP.w / 2}" y="${CP.y + 26}">Control plane</text>
    <text class="cp-sub" x="${CP.x + CP.w / 2}" y="${CP.y + 45}">API server · scheduler · controllers</text>
    <text class="cp-sub" x="${CP.x + CP.w / 2}" y="${CP.y + 62}">${cut ? 'unreachable from the workers' : 'reachable'}</text>
  </g>`);

  // workers
  for (const n of NODES) {
    const { x, y } = BOX[n];
    const node = S.nodes[n];
    const f = free(n);
    const used = CAPACITY - f;
    const b = node.badge;
    out.push(`<g class="worker${b ? ` worker--${b.kind}` : ''}" data-node="${n}">`);
    out.push(`<rect class="worker-box" x="${x}" y="${y}" width="${W}" height="${H}" rx="8"/>`);
    out.push(`<text class="worker-title" x="${x + 12}" y="${y + 22}">${n}</text>`);
    if (b) out.push(`<text class="worker-badge worker-badge--${b.kind}" x="${x + W - 12}" y="${y + 22}">${esc(b.text)}</text>`);
    else if (node.stress) out.push(`<text class="worker-badge worker-badge--stress" x="${x + W - 12}" y="${y + 22}">+${STRESS}m load</text>`);
    out.push(`<text class="cpu-label" x="${x + 12}" y="${y + 42}">CPU</text>`);
    out.push(`<text class="cpu-free" x="${x + W - 12}" y="${y + 42}">free ${f}m</text>`);
    out.push(`<rect class="cpu-track" x="${x + 12}" y="${y + 48}" width="${W - 24}" height="7" rx="3.5"/>`);
    out.push(`<rect class="cpu-used" x="${x + 12}" y="${y + 48}" width="${((W - 24) * Math.min(used, CAPACITY)) / CAPACITY}" height="7" rx="3.5"/>`);

    // edge-healer pod
    if (S.healer) {
      out.push(`<rect class="eh-pod" x="${x + 12}" y="${y + 64}" width="${W - 24}" height="36" rx="5"/>`);
      out.push(`<text class="eh-title" x="${x + 18}" y="${y + 76}">edge-healer pod</text>`);
      out.push(`<rect class="eh-chip" x="${x + 18}" y="${y + 80}" width="74" height="15" rx="3"/><text class="eh-chip-t" x="${x + 55}" y="${y + 91}">healer</text>`);
      out.push(`<rect class="eh-chip eh-chip--serf" x="${x + 98}" y="${y + 80}" width="64" height="15" rx="3"/><text class="eh-chip-t" x="${x + 130}" y="${y + 91}">serf</text>`);
    } else {
      out.push(`<rect class="eh-pod eh-pod--off" x="${x + 12}" y="${y + 64}" width="${W - 24}" height="36" rx="5"/>`);
      out.push(`<text class="eh-off" x="${x + W / 2}" y="${y + 86}">no edge-healer</text>`);
    }

    // busybox pods
    const pods = S.pods.filter((p) => p.node === n);
    if (!pods.length) {
      out.push(`<rect class="pod-slot" x="${x + 12}" y="${y + 110}" width="${W - 24}" height="56" rx="5"/>`);
      out.push(`<text class="pod-slot-t" x="${x + W / 2}" y="${y + 142}">no busybox pod</text>`);
    }
    const gap = 6;
    const pw = Math.min(48, (W - 24 - gap * (pods.length - 1)) / Math.max(1, pods.length));
    pods.forEach((p, i) => {
      const px = x + 12 + i * (pw + gap);
      const dup = p.local && S.pods.some((q) => q !== p && q.name === p.name && q.state !== 'terminating');
      const tag = p.state === 'terminating' ? 'killed' : dup ? 'dup' : p.local ? 'local' : p.state === 'pending' ? 'pending' : '';
      const cls = `pod pod--${p.state}${p.local ? ' pod--local' : ''}${dup ? ' pod--dup' : ''}`;
      out.push(`<g class="${cls}" data-pod="${p.id}"><title>default/${esc(p.name)} (${p.state}${p.local ? ', bound locally' : ''}). Click to kill it.</title>`);
      out.push(`<rect x="${px}" y="${y + 110}" width="${pw}" height="56" rx="5"/>`);
      out.push(`<text class="pod-t" x="${px + pw / 2}" y="${y + 130}">bb</text>`);
      out.push(`<text class="pod-s" x="${px + pw / 2}" y="${y + 144}">${p.name.slice(-5)}</text>`);
      if (tag) out.push(`<text class="pod-tag" x="${px + pw / 2}" y="${y + 158}">${tag}</text>`);
      out.push('</g>');
    });
    out.push('</g>');
  }
  return out.join('');
}

function drawMsgs() {
  return S.msgs.map((m) => {
    const f = Math.min(1, (S.t - m.t0) / m.dur);
    let p;
    if (m.kind === 'gossip') p = gossipPoint(m.from, m.to, f);
    else if (m.kind === 'probe') {
      // the request heads for the API server and dies at the cut
      p = apiPoint(m.from, 1 - Math.min(f * 1.6, 0.5));
      return `<circle class="msg msg--probe" cx="${p.x}" cy="${p.y}" r="4" opacity="${f > 0.6 ? (1 - f) * 2.5 : 1}"/>`;
    } else if (m.from === 'cp') p = apiPoint(m.to, f);
    else p = apiPoint(m.from, 1 - f);
    return `<circle class="msg msg--${m.kind}" cx="${p.x}" cy="${p.y}" r="${m.kind === 'gossip' ? 3.5 : 4.5}"/>`;
  }).join('');
}

let staticLayer; let msgLayer; let svgEl;
let sideAt = -Infinity;

function render() {
  const changed = S.dirty;
  if (S.dirty) {
    staticLayer.innerHTML = drawStatic();
    S.dirty = false;
    const r = runningCount();
    svgEl.setAttribute('aria-label', `Cluster diagram. WAN ${S.wan ? 'connected' : 'cut'}; edge-healer ${S.healer ? 'on' : 'off'}; `
      + `${r} of ${DESIRED} busybox replicas running. ${NODES.map((n) => `${n}: ${S.pods.filter((p) => p.node === n).length} pod(s), ${free(n)}m free`).join('; ')}.`);
  }
  msgLayer.innerHTML = drawMsgs();
  const now = performance.now();
  if (changed || now - sideAt > 100) { // the tables and numbers: ~10 times a second is plenty
    sideAt = now;
    renderSide();
  }
}

function renderSide() {
  // free-CPU maps
  const rows = NODES.map((obs) => {
    const cells = NODES.map((sub) => {
      const e = S.views[obs][sub];
      if (!S.healer) return '<td class="v v--none">–</td>';
      if (!e) return '<td class="v v--none">?</td>';
      const age = S.t - e.t;
      const cls = sub === obs ? 'v v--self' : age > 3 ? 'v v--stale' : 'v';
      return `<td class="${cls}" title="${sub} as seen by ${obs}, ${age.toFixed(1)} s ago">${e.v}m</td>`;
    }).join('');
    return `<tr><th scope="row">${obs}</th>${cells}</tr>`;
  }).join('');
  viewsBody.innerHTML = rows;

  const r = runningCount();
  const last = S.metrics.lastRestore;
  metric.running.textContent = `${r} / ${DESIRED}`;
  metric.running.dataset.state = r < DESIRED ? 'low' : r > DESIRED ? 'high' : 'ok';
  metric.restore.textContent = last ? `${last.secs.toFixed(2)} s` : '–';
  metric.restoreBy.textContent = last ? `by ${last.by}` : '';
  metric.gap.textContent = `${S.metrics.gap.toFixed(1)} s`;
  metric.conflicts.textContent = String(S.metrics.bindConflicts);
  metric.updates.textContent = String(S.metrics.peerUpdates);
  clockEl.textContent = `t = ${S.t.toFixed(1)} s`;

  for (const n of NODES) {
    killBtn[n].disabled = !S.pods.some((p) => p.node === n && p.state !== 'terminating');
  }
}

// ---------------------------------------------------------------------------
// Controls
// ---------------------------------------------------------------------------

let viewsBody; let clockEl;
const metric = {};
const killBtn = {};
const stressBtn = {};
let ctl = {};

function syncControls() {
  const set = (btn, on, onText, offText) => {
    btn.setAttribute('aria-pressed', String(on));
    btn.querySelector('[data-state]').textContent = on ? onText : offText;
  };
  set(ctl.wan, S.wan, 'connected', 'cut');
  set(ctl.healer, S.healer, 'on', 'off');
  set(ctl.gossip, S.gossip && S.healer, 'running', S.healer ? 'paused' : 'off');
  ctl.wan.classList.toggle('is-alert', !S.wan);
  ctl.gossip.disabled = !S.healer;
  ctl.pause.setAttribute('aria-pressed', String(S.paused));
  ctl.pause.textContent = S.paused ? 'Resume' : 'Pause';
  ctl.play.textContent = S.scenario ? 'Restart the experiment' : 'Play the experiment';
  for (const n of NODES) stressBtn[n].setAttribute('aria-pressed', String(!!S.nodes[n].stress));
}

function buildNodeRows(host) {
  for (const n of NODES) {
    const row = document.createElement('div');
    row.className = 'demo-node';
    const name = document.createElement('span');
    name.className = 'demo-node__name';
    name.textContent = n;
    const kill = document.createElement('button');
    kill.type = 'button';
    kill.className = 'btn-secondary btn-small';
    kill.textContent = 'Kill a pod';
    kill.addEventListener('click', manual(() => {
      const pod = S.pods.find((p) => p.node === n && p.state !== 'terminating');
      killPod(pod);
    }));
    const stress = document.createElement('button');
    stress.type = 'button';
    stress.className = 'btn-secondary btn-small';
    stress.setAttribute('aria-pressed', 'false');
    stress.textContent = 'Add CPU load';
    stress.title = `Add ${STRESS}m of background CPU load: the node becomes a worse bidder`;
    stress.addEventListener('click', manual(() => toggleStress(n)));
    killBtn[n] = kill;
    stressBtn[n] = stress;
    row.append(name, kill, stress);
    host.append(row);
  }
}

function init() {
  const root = $('demo');
  if (!root) return;
  svgEl = $('demoSvg');
  logEl = $('demoLog');
  sayEl = $('demoSay');
  stepsEl = $('demoSteps');
  viewsBody = $('demoViews').querySelector('tbody');
  clockEl = $('demoClock');
  for (const k of ['running', 'restore', 'restoreBy', 'gap', 'conflicts', 'updates']) metric[k] = root.querySelector(`[data-metric="${k}"]`);
  ctl = { play: $('demoPlay'), pause: $('demoPause'), reset: $('demoReset'), speed: $('demoSpeed'), wan: $('demoWan'), healer: $('demoHealer'), gossip: $('demoGossip') };

  const NS = 'http://www.w3.org/2000/svg';
  staticLayer = document.createElementNS(NS, 'g');
  msgLayer = document.createElementNS(NS, 'g');
  msgLayer.setAttribute('class', 'msgs');
  svgEl.replaceChildren(staticLayer, msgLayer);

  buildNodeRows($('demoNodes'));

  ctl.play.addEventListener('click', () => { playScenario(); S.paused = false; syncControls(); });
  ctl.pause.addEventListener('click', () => { S.paused = !S.paused; syncControls(); });
  ctl.reset.addEventListener('click', () => reset());
  ctl.speed.addEventListener('change', () => { S.speed = Number(ctl.speed.value) || 1; });
  ctl.wan.addEventListener('click', manual(() => setWan(!S.wan)));
  ctl.healer.addEventListener('click', manual(() => setHealer(!S.healer)));
  ctl.gossip.addEventListener('click', manual(() => setGossip(!S.gossip)));
  svgEl.addEventListener('click', (ev) => {
    const g = ev.target.closest('[data-pod]');
    if (!g) return;
    manual(() => killPod(S.pods.find((p) => p.id === Number(g.dataset.pod))))();
  });

  S = freshState({ speed: Number(ctl.speed.value) || 1 });
  reset();

  // Run only while the demo is on screen.
  let visible = true;
  if ('IntersectionObserver' in window) {
    new IntersectionObserver((entries) => { visible = entries.some((e) => e.isIntersecting); }).observe(root);
  }
  let last = performance.now();
  const frame = (now) => {
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (visible && !S.paused) {
      step(dt * S.speed);
      render();
    }
    requestAnimationFrame(frame);
  };
  render();
  requestAnimationFrame(frame);
}

init();
