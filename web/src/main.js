import * as THREE from 'three';
import { loadWorld, b2t } from './world.js';
import { makeCollision } from './collision.js';
import { Input } from './input.js';
import { initAudio, sfx, music } from './audio.js';
import { Rat, RAT } from './rat.js';
import { MaxHunter } from './max.js';
import { Hud } from './hud.js';

const $ = id => document.getElementById(id);
const clamp = THREE.MathUtils.clamp;
const wrap = a => Math.atan2(Math.sin(a), Math.cos(a));
const isTouch = matchMedia('(pointer: coarse)').matches;

// --------------------------------------------------------------------------- Renderer
const canvas = $('c');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.AgXToneMapping;
const scene = new THREE.Scene();
scene.background = new THREE.Color('#05070c');
const camera = new THREE.PerspectiveCamera(70, 1, 0.01, 60);

const world = await loadWorld(p => { $('bar-fill').style.width = `${p * 100}%`; }).catch(err => {
  $('loader-text').textContent = 'Fehler beim Laden: ' + err.message;
  throw err;
});
world.addTo(scene);
const layout = world.layout;
const N = world.byName;
const col = makeCollision(layout);
const hud = new Hud(layout);
const input = new Input(canvas, $('stick'), $('knob'), $('action'));
const rat = new Rat(world);
scene.add(rat.blob);
const hunter = new MaxHunter(world, col, scene);
hunter.setActive(false);
const NEST = layout.hideout.pos;
const EXIT = layout.exit;
const MAX_HEAD = layout.max.head;

// --------------------------------------------------------------------------- schlafender Max
const sleeper = { parts: [N['Max'], N['Max_Koerper']], body: N['Max_Koerper'] };
sleeper.scale = sleeper.body.scale.clone();
sleeper.rot = sleeper.body.rotation.clone();
const zzz = [0, 1, 2].map(() => { const s = world.textSprite('Z', '#cfe3ff', 0.09); scene.add(s); return s; });

// --------------------------------------------------------------------------- Gegenstaende
const UP = new THREE.Vector3(0, 1, 0);
const ITEM_R = { Dose: 0.03, Mate: 0.036 };
const items = layout.items.map(it => {
  const obj = N[it.name];
  if (!obj) return null;
  return {
    ...it, obj, kind: it.name.split('_')[0],
    home: { p: obj.position.clone(), q: obj.quaternion.clone(), s: obj.scale.clone() },
  };
}).filter(Boolean);
const physItems = items.filter(i => i.role === 'noise' && i.pos[2] < 0.05);
const figures = items.filter(i => i.role === 'mini');
const puddle = layout.water?.[0];

function resetItems() {
  for (const it of items) {
    it.obj.position.copy(it.home.p);
    it.obj.quaternion.copy(it.home.q);
    it.obj.scale.copy(it.home.s);
    it.obj.visible = true;
    Object.assign(it, {
      x: it.pos[0], y: it.pos[1], vx: 0, vy: 0, standing: !it.lying, tip: null, tipT: 0, yawAdd: 0, cool: 0,
      drunk: false, progress: 0, carried: false, delivered: false,
    });
  }
}

function tipOver(it, nx, ny) {
  it.standing = false;
  const axis = new THREE.Vector3(-ny, 0, -nx).normalize();   // up x Stossrichtung
  it.tip = new THREE.Quaternion().setFromAxisAngle(axis, Math.PI / 2);
  it.tipT = 0;
}

const _q = new THREE.Quaternion();
const _qy = new THREE.Quaternion();
function placeItem(it) {
  const r = ITEM_R[it.kind] || 0.03;
  let lift = 0;
  _qy.setFromAxisAngle(UP, it.yawAdd);
  if (it.tip) {
    it.tipT = Math.min(1, it.tipT + 1 / 10);
    _q.identity().slerp(it.tip, it.tipT).multiply(_qy).multiply(it.home.q);
    lift = r * it.tipT * 0.9;
  } else {
    _q.copy(_qy).multiply(it.home.q);
  }
  it.obj.quaternion.copy(_q);
  it.obj.position.set(it.x, it.pos[2] + lift, -it.y);
}

function pushBy(it, px, py, vx, vy, pr, isMax) {
  const r = ITEM_R[it.kind] || 0.03;
  const dx = it.x - px, dy = it.y - py;
  const d = Math.hypot(dx, dy);
  const minD = pr + r;
  if (d >= minD || d < 1e-5) return;
  const nx = dx / d, ny = dy / d;
  it.x = px + nx * minD;
  it.y = py + ny * minD;
  const rel = vx * nx + vy * ny;
  if (rel <= 0.04) return;
  it.vx += nx * rel * 1.4;
  it.vy += ny * rel * 1.4;
  if (it.cool <= 0 && rel > 0.12) {
    const strength = clamp(rel / 0.8, 0.25, 1.3);
    it.cool = 0.35;
    const bottle = it.kind === 'Mate';
    makeNoise((bottle ? 0.3 : 0.22) * strength * (isMax ? 0.6 : 1), it.x, it.y);
    const vol = clamp(strength * camVolume(it.x, it.y), 0.05, 1.2);
    if (bottle) sfx.bottle(vol); else sfx.clink(vol);
    if (it.standing && rel > 0.22) tipOver(it, nx, ny);
  }
}

function updateItems(dt) {
  for (const it of physItems) {
    it.cool -= dt;
    pushBy(it, rat.pos.x, rat.pos.y, rat.vel.x, rat.vel.y, RAT.radius, false);
    if (hunter.active) {
      pushBy(it, hunter.pos.x, hunter.pos.y, Math.cos(hunter.yaw) * hunter.speed, Math.sin(hunter.yaw) * hunter.speed,
        0.16, true);
    }
    if (it.vx || it.vy) {
      const r = ITEM_R[it.kind] || 0.03;
      const bx = it.x, by = it.y;
      it.x += it.vx * dt;
      it.y += it.vy * dt;
      const p = { x: it.x, y: it.y };
      if (col.push(p, r, col.ratRects)) {
        let nx = p.x - it.x, ny = p.y - it.y;
        const l = Math.hypot(nx, ny) || 1;
        nx /= l; ny /= l;
        const vn = it.vx * nx + it.vy * ny;
        if (vn < 0) { it.vx -= 1.6 * vn * nx; it.vy -= 1.6 * vn * ny; }
        if (Math.abs(vn) > 0.25 && it.cool <= 0) {
          it.cool = 0.3;
          (it.kind === 'Mate' ? sfx.bottle : sfx.clink)(0.4 * camVolume(it.x, it.y));
        }
      }
      it.x = p.x; it.y = p.y;
      const f = Math.exp(-dt * (it.standing ? 6 : 2.4));
      it.vx *= f; it.vy *= f;
      if (!it.standing) it.yawAdd += Math.hypot(it.x - bx, it.y - by) * 9;
      if (Math.hypot(it.vx, it.vy) < 0.01) it.vx = it.vy = 0;
    }
    if ((it.tip && it.tipT < 1) || it.vx || it.vy) placeItem(it);
  }
}

// --------------------------------------------------------------------------- Laerm
let noise = 0;
let quiet = 0;
const atten = (d, k = 2.4) => 1 / (1 + (d / k) ** 2);
function makeNoise(amount, x, y) {
  if (level === 3) {
    if (hunter.active) hunter.hear(x, y, amount * atten(Math.hypot(x - hunter.pos.x, y - hunter.pos.y), 2.0));
    return;
  }
  noise += amount * atten(Math.hypot(x - MAX_HEAD[0], y - MAX_HEAD[1]));
  quiet = 0;
}
const camVolume = (x, y) => atten(Math.hypot(x - rat.pos.x, y - rat.pos.y), 1.5);

// --------------------------------------------------------------------------- Marker
const markers = [];
function marker(color = '#ffd60a') {
  const s = world.textSprite('▼', color, 0.07);
  s.material.depthTest = true;
  scene.add(s);
  markers.push(s);
  return s;
}
function clearMarkers() { markers.forEach(m => scene.remove(m)); markers.length = 0; }

// --------------------------------------------------------------------------- Level
const LEVELS = {
  1: {
    name: 'Red-Bull-Rausch',
    time: 210,
    timeout: 'Draußen wird es hell. Max\' Wecker klingelt gleich – und es sind noch Dosen voll.',
    codec: [
      'Ratte, hörst du mich? Max pennt. Die Party ist vorbei, aber überall liegen noch halbvolle Energy-Dosen.',
      'Trink alle leer. Jede Dose macht dich schneller – und danach musst du rülpsen.',
      'Leise! Rennen, Rülpsen und gegen Flaschen laufen macht Lärm. Ist der Lärmbalken voll, wacht Max auf.',
    ],
  },
  2: {
    name: 'Operation Plastikkrieger',
    time: 300,
    timeout: 'Die Sonne geht auf. Gleich merkt Max, dass seine Armee fehlt.',
    codec: [
      'Max sammelt Warhammer-Figuren. Sechs davon sind von seiner Palette ausgebüxt und liegen in der Wohnung.',
      'Bring alle sechs in dein Versteck in der Abstellkammer. Du kannst immer nur eine im Maul tragen.',
      'Tipp: unter der Couch, unterm Küchentisch … und eine steht mitten in der Pfütze im Bad. Platsch.',
    ],
  },
  3: {
    name: 'Metal Gear Ratte',
    time: 180,
    timeout: 'Zu spät: Max hat den Kammerjäger angerufen. Der klingelt gerade.',
    codec: [
      'Ratte! Max ist aufgewacht. Er trägt nur seine pinke Strumpfhose – und einen Eimer.',
      'Im Schlafzimmer ist ein Loch in der Außenwand. Das ist dein Weg in die Freiheit.',
      'Bleib aus seinem Sichtkegel. Unter Bett und Couch sieht er dich nicht, im Dunkeln bemerkt er dich später. Lärm lockt ihn an.',
    ],
  },
};

let level = 1;
let state = 'menu';   // menu | brief | play | paused | won | lost
let elapsed = 0;
let targets = [];
let delivered = 0;
let burpAt = -1;
let snoreT = 0;
let splashT = 0;
let stepT = 0;
let wakeWarned = false;
let nestMarker = null;
let exitMarker = null;
const done = new Set((() => { try { return JSON.parse(localStorage.getItem('maxrat.done') || '[]'); } catch { return []; } })());

function startLevel(n) {
  level = n;
  resetItems();
  clearMarkers();
  nestMarker = exitMarker = null;
  noise = 0;
  elapsed = 0;
  delivered = 0;
  burpAt = -1;
  wakeWarned = false;
  rat.reset(NEST[0] - 0.35, NEST[1], Math.PI);
  rat.root.visible = true;
  rat.blob.visible = true;
  camYaw = rat.yaw;
  camPitch = 0.36;
  camDist = 0.65;
  const awake = n === 3;
  sleeper.parts.forEach(o => { o.visible = !awake; });
  zzz.forEach(z => { z.visible = !awake; });
  hunter.setActive(awake);
  if (awake) hunter.reset();
  figures.forEach(f => { f.obj.visible = n === 2; });
  if (n === 1) {
    targets = physItems.filter(i => i.kind === 'Dose' && !i.crushed);
    targets.forEach(t => { t.marker = marker(); });
  } else if (n === 2) {
    targets = figures;
    targets.forEach(t => { t.marker = marker(); });
    nestMarker = marker('#5ac8fa');
  } else {
    targets = [];
    exitMarker = marker('#ffffff');
    exitMarker.position.copy(b2t(EXIT.pos[0] - 0.05, EXIT.pos[1], 0.16));
  }
  hud.status('');
  hud.timer(LEVELS[n].time);
  updateObjective();
  showBrief();
}

function updateObjective() {
  const L = LEVELS[level];
  if (level === 1) hud.objective(L.name, `Dosen leer: ${targets.filter(t => t.drunk).length} / ${targets.length}`);
  if (level === 2) hud.objective(L.name, `Figuren im Versteck: ${delivered} / ${figures.length}`);
  if (level === 3) hud.objective(L.name, 'Ziel: Loch in der Schlafzimmerwand');
}

function win() {
  if (state !== 'play') return;
  state = 'won';
  done.add(level);
  try { localStorage.setItem('maxrat.done', JSON.stringify([...done])); } catch { /* privat */ }
  sfx.win();
  const t = `${Math.floor(elapsed / 60)}:${String(Math.floor(elapsed % 60)).padStart(2, '0')}`;
  const msg = {
    1: 'Alle Dosen leer. Du zitterst ein bisschen. Max schnarcht weiter.',
    2: 'Sechs Figuren sicher im Nest. Du bist jetzt offiziell Sammlerin.',
    3: 'Du bist draußen! Hinter dir flucht ein Mann in pinker Strumpfhose.',
  }[level];
  showResult('Geschafft!', `${msg}<br><small>Zeit ${t}</small>`, level < 3 ? 'Nächstes Level' : null);
}

function lose(title, text) {
  if (state !== 'play') return;
  state = 'lost';
  showResult(title, text, null);
}

// --------------------------------------------------------------------------- Overlays
function hideOverlays() { ['menu', 'brief', 'result', 'pause-menu'].forEach(id => { $(id).hidden = true; }); }

function showMenu() {
  state = 'menu';
  hideOverlays();
  hud.show(false);
  input.enabled = false;
  input.reset();
  const list = $('level-list');
  list.innerHTML = '';
  for (const n of [1, 2, 3]) {
    const b = document.createElement('button');
    b.className = 'level-card';
    b.innerHTML = `<span class="lv">Level ${n}</span><span class="nm">${LEVELS[n].name}</span>${done.has(n) ? '<span class="ok">✓</span>' : ''}`;
    b.addEventListener('click', () => { initAudio(); sfx.ui(); startLevel(n); });
    list.appendChild(b);
  }
  $('menu').hidden = false;
  music.setRate(1);
  music.play('menu');
}

function showBrief() {
  state = 'brief';
  hideOverlays();
  hud.show(false);
  input.enabled = false;
  $('brief-title').textContent = `Level ${level} · ${LEVELS[level].name} · ⏱ ${Math.floor(LEVELS[level].time / 60)}:${String(LEVELS[level].time % 60).padStart(2, '0')}`;
  $('brief-text').innerHTML = LEVELS[level].codec.map(l => `<p>${l}</p>`).join('');
  $('brief-controls').textContent = isTouch
    ? 'Daumen unten aufsetzen und ziehen = laufen · weit ziehen = rennen (laut!) · oben wischen = Kamera'
    : 'WASD = schleichen · Shift = rennen (laut!) · Maus ziehen = Kamera · Leertaste = Aktion';
  $('brief').hidden = false;
}

function showResult(title, html, nextLabel) {
  music.stop();
  setTimeout(() => { if (state === 'won' || state === 'lost') { music.setRate(1); music.play('menu'); } }, 2600);
  hud.show(false);
  input.enabled = false;
  input.reset();
  $('result-title').textContent = title;
  $('result-text').innerHTML = html;
  $('result-next').hidden = !nextLabel;
  if (nextLabel) $('result-next').textContent = nextLabel;
  setTimeout(() => { $('result').hidden = false; }, 700);
}

$('brief-go').addEventListener('click', () => {
  initAudio();
  sfx.ui();
  hideOverlays();
  state = 'play';
  hud.show(true);
  input.enabled = true;
  levelMusic();
});

function levelMusic() {
  music.setRate(1);
  music.play(level === 3 ? (hunter.state === 'alert' ? 'alert' : 'stealth') : 'sneak');
}

function updateMusicButtons() {
  for (const id of ['music-menu', 'music-pause']) $(id).textContent = `♪ Musik: ${music.muted ? 'aus' : 'an'}`;
}
for (const id of ['music-menu', 'music-pause']) {
  $(id).addEventListener('click', () => { initAudio(); music.toggleMute(); if (state === 'menu') music.play('menu'); updateMusicButtons(); });
}
updateMusicButtons();
// Erste Beruehrung im Menue startet Audio + Menuemusik (Browser erlauben Ton erst nach Interaktion)
$('menu').addEventListener('pointerdown', () => { initAudio(); music.play('menu'); });
$('result-retry').addEventListener('click', () => startLevel(level));
$('result-next').addEventListener('click', () => startLevel(level + 1));
$('result-menu').addEventListener('click', showMenu);
$('pause').addEventListener('click', () => {
  if (state !== 'play') return;
  state = 'paused';
  input.enabled = false;
  input.reset();
  music.stop();
  $('pause-menu').hidden = false;
});
$('pause-resume').addEventListener('click', () => { $('pause-menu').hidden = true; state = 'play'; input.enabled = true; levelMusic(); });
$('pause-restart').addEventListener('click', () => startLevel(level));
$('pause-menu-btn').addEventListener('click', showMenu);

// --------------------------------------------------------------------------- Kamera (3rd Person)
let camYaw = 0, camPitch = 0.36, camDist = 0.65, manualT = 0;
const camPos = new THREE.Vector3();
function updateCamera(dt, look, menuSpin) {
  if (menuSpin) {  // Menue: langsame Kamerafahrt im Wohnzimmer
    const a = Math.sin(elapsed * 0.12) * 0.5;
    camera.position.copy(b2t(4.9 + Math.sin(a) * 0.3, 0.6, 1.25));
    camera.lookAt(b2t(2.4 + Math.sin(a) * 0.8, 3.4, 0.55));
    return;
  }
  if (look.dx || look.dy) {
    camYaw -= look.dx * 0.009;
    camPitch = clamp(camPitch + look.dy * 0.004, 0.05, 1.25);
    manualT = 1.6;
  }
  manualT -= dt;
  if (manualT <= 0 && rat.actual > 0.1) camYaw += wrap(rat.yaw - camYaw) * Math.min(1, dt * 2.0);
  const under = rat.hidden;
  const want = under ? 0.36 : level === 3 ? 1.35 : 0.66;
  camDist += (want - camDist) * Math.min(1, dt * 4);
  const pitch = under ? Math.min(camPitch, 0.1) : (level === 3 ? Math.max(camPitch, 0.6) : camPitch);
  const horiz = camDist * Math.cos(pitch);
  const bx = rat.pos.x - Math.cos(camYaw) * horiz, by = rat.pos.y - Math.sin(camYaw) * horiz;
  // Kamera nicht in Waende/Moebel stecken (unter Moebeln nur Waende pruefen, sonst startet der Strahl im Moebel)
  const f = Math.max(0.12, col.raycast(rat.pos.x, rat.pos.y, bx, by, under ? col.walls : col.maxRects, 0.06));
  const cx = rat.pos.x + (bx - rat.pos.x) * f, cy = rat.pos.y + (by - rat.pos.y) * f;
  const h = 0.06 + camDist * Math.sin(pitch);
  camPos.copy(b2t(cx, cy, h));
  camera.position.lerp(camPos, Math.min(1, dt * 14));
  camera.lookAt(b2t(rat.pos.x + Math.cos(camYaw) * 0.12, rat.pos.y + Math.sin(camYaw) * 0.12, 0.05));
}

// --------------------------------------------------------------------------- Spielschritt
function nearest(list, x, y, maxD, pred) {
  let best = null, bd = maxD;
  for (const it of list) {
    if (!pred(it)) continue;
    const d = Math.hypot(it.x - x, it.y - y);
    if (d < bd) { bd = d; best = it; }
  }
  return best;
}

function play(dt, t, look) {
  elapsed += dt;
  const left = LEVELS[level].time - elapsed;
  hud.timer(left);
  music.setRate(left < 30 ? 1.22 : 1);
  if (left <= 30 && left + dt > 30) hud.toast('Noch 30 Sekunden!', 1.6);
  if (left <= 0) {
    sfx.caught();
    lose('Zeit abgelaufen!', LEVELS[level].timeout);
    return;
  }
  // Bewegung relativ zur Kamera
  const mv = input.move;
  const wish = new THREE.Vector2(
    Math.cos(camYaw) * mv.y + Math.sin(camYaw) * mv.x,
    Math.sin(camYaw) * mv.y - Math.cos(camYaw) * mv.x,
  );
  rat.update(dt, wish, input.run, col);
  updateItems(dt);

  // Rennen ist laut, Pfuetze platscht
  stepT -= dt;
  if (rat.running && rat.actual > 0.9 && stepT <= 0) { stepT = 0.35; makeNoise(0.035, rat.pos.x, rat.pos.y); sfx.squeak(0.25); }
  if (puddle && rat.actual > 0.15 && col.inPoly(rat.pos.x, rat.pos.y, puddle)) {
    splashT -= dt;
    if (splashT <= 0) { splashT = 0.28; sfx.splash(0.6); makeNoise(0.05, rat.pos.x, rat.pos.y); }
  }

  const m = rat.mouth();
  if (level === 1) {
    const can = nearest(targets, m.x, m.y, 0.15, i => !i.drunk);
    if (can) {
      hud.action('Trinken', can.progress);
      if (input.actionDown) {
        rat.busy = true;
        rat.yaw += wrap(Math.atan2(can.y - rat.pos.y, can.x - rat.pos.x) - rat.yaw) * Math.min(1, dt * 8);
        can.progress += dt / 1.6;
        makeNoise(0.045 * dt, rat.pos.x, rat.pos.y);
        if ((t * 3) % 1 < dt * 3) sfx.slurp(0.7);
        if (can.progress >= 1) {
          can.drunk = true;
          rat.busy = false;
          can.obj.scale.y *= 0.55;
          scene.remove(can.marker);
          rat.boost += 0.05;
          const n = targets.filter(i => i.drunk).length;
          hud.toast(n === targets.length ? 'Letzte Dose! 🥤' : `Schlürf! ${n} / ${targets.length}`);
          burpAt = elapsed + 0.8;
          updateObjective();
        }
      } else rat.busy = false;
    } else { rat.busy = false; hud.action(null); }
    if (burpAt > 0 && elapsed > burpAt) {
      burpAt = -1;
      sfx.burp(0.9);
      makeNoise(0.15, rat.pos.x, rat.pos.y);
      hud.toast('*RÜLPS*', 1);
      if (targets.every(i => i.drunk)) setTimeout(win, 900);
    }
  } else if (level === 2) {
    if (!rat.carry) {
      const fig = nearest(figures, m.x, m.y, 0.15, f => !f.carried && !f.delivered);
      hud.action(fig ? 'Nehmen' : null);
      if (fig && look.pressed) {
        fig.carried = true;
        rat.carry = fig.obj;
        scene.remove(fig.marker);
        sfx.pickup();
        makeNoise(0.02, fig.x, fig.y);
        hud.toast('Ab ins Versteck!');
      }
    } else {
      const fig = figures.find(f => f.carried);
      const dn = Math.hypot(rat.pos.x - NEST[0], rat.pos.y - NEST[1]);
      if (dn < 0.42) {
        fig.carried = false;
        fig.delivered = true;
        rat.carry = null;
        const a = delivered * 1.05;
        fig.x = NEST[0] + Math.cos(a) * 0.1;
        fig.y = NEST[1] + Math.sin(a) * 0.1;
        fig.obj.position.copy(b2t(fig.x, fig.y, 0.02));
        delivered++;
        sfx.deliver();
        hud.toast(delivered === figures.length ? 'Alle Figuren gesichert!' : `Gesichert! ${delivered} / ${figures.length}`);
        updateObjective();
        if (delivered === figures.length) setTimeout(win, 600);
        hud.action(null);
      } else {
        hud.action('Ablegen');
        if (look.pressed) {
          fig.carried = false;
          rat.carry = null;
          fig.x = m.x; fig.y = m.y;
          fig.obj.position.copy(b2t(m.x, m.y, 0));
          fig.marker = marker();
          makeNoise(0.03, m.x, m.y);
        }
      }
    }
    if (nestMarker) nestMarker.visible = !!rat.carry;
  } else if (level === 3) {
    const light = world.lightAt(col, rat.pos.x, rat.pos.y);
    hunter.update(dt, t, rat, light);
    for (const e of hunter.events) {
      const dv = atten(rat.pos.distanceTo(hunter.pos), 2.5);
      if (e === 'alert') { sfx.alert(); hud.status('! ALERT !', 'alert'); music.play('alert'); }
      if (e === 'evasion') { hud.status('EVASION', 'evasion'); music.play('stealth'); }
      if (e === 'calm') hud.status('');
      if (e === 'huh') sfx.huh();
      if (e === 'step') sfx.step(0.9 * dv);
      if (e === 'swoosh') sfx.swoosh();
      if (e === 'bucket') sfx.bucket();
      if (e === 'caught') {
        sfx.caught();
        rat.root.visible = false;
        rat.blob.visible = false;
        lose('Gefangen!', 'Max hat dich mit dem Eimer erwischt. Unter dem Eimer ist es dunkel und riecht nach Strumpfhose.');
      }
    }
    hunter.events.length = 0;
    if (Math.hypot(rat.pos.x - EXIT.pos[0], rat.pos.y - EXIT.pos[1]) < EXIT.r) win();
  }

  // Laermpegel (Level 1+2)
  if (level !== 3) {
    quiet += dt;
    if (quiet > 1.2) noise = Math.max(0, noise - dt * 0.03);
    if (noise > 0.7 && !wakeWarned) { wakeWarned = true; hud.toast('Max wälzt sich …', 1.4); sfx.wake(); }
    if (noise < 0.5) wakeWarned = false;
    if (noise >= 1) {
      sfx.wake();
      lose('Max ist aufgewacht!', 'Zu laut. Max sitzt kerzengerade im Bett und starrt dich an.');
    }
  }
}

// --------------------------------------------------------------------------- Loop
function resize() {
  const w = innerWidth, h = innerHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.fov = camera.aspect < 1 ? 74 : 58;
  camera.updateProjectionMatrix();
}
addEventListener('resize', resize);
resize();

const clock = new THREE.Clock();
let simTime = 0;
function tick() {
  frame(Math.min(clock.getDelta(), 0.05));
  requestAnimationFrame(tick);
}

function frame(dt) {
  simTime += dt;
  const t = simTime;
  const look = input.poll();
  world.update(t);
  hud.tick(dt);

  if (state === 'play') play(dt, t, look);
  else if (state === 'menu') elapsed += dt;

  // Animationen laufen auch in Overlays weiter
  rat.animate(dt, t);
  world.tintChar('Ratte', world.lightAt(col, rat.pos.x, rat.pos.y));
  if (hunter.active) {
    if (state !== 'play') { hunter.animate(dt, t); hunter.events.length = 0; }
    world.tintChar('MaxWach', world.lightAt(col, hunter.pos.x, hunter.pos.y, 1.2));
  } else {
    const b = 1 + 0.045 * Math.sin(t * (Math.PI * 2 / 4.8));
    const fidget = noise > 0.7 ? Math.sin(t * 9) * 0.06 : 0;
    sleeper.body.scale.set(sleeper.scale.x, sleeper.scale.y * b, sleeper.scale.z * (1 + (b - 1) * 0.3));
    sleeper.body.rotation.set(sleeper.rot.x + fidget, sleeper.rot.y, sleeper.rot.z);
    zzz.forEach((z, i) => {
      const u = (t * 0.35 + i / 3) % 1;
      z.position.copy(b2t(MAX_HEAD[0] - 0.05 + u * 0.15, MAX_HEAD[1] + u * 0.25, MAX_HEAD[2] + 0.12 + u * 0.4));
      z.material.opacity = noise > 0.6 ? 0 : Math.sin(u * Math.PI);
      z.scale.setScalar(0.05 + u * 0.06);
    });
    if (state === 'play') {
      snoreT -= dt;
      if (snoreT <= 0 && noise < 0.6) {
        snoreT = 3.8;
        sfx.snore(clamp(atten(Math.hypot(rat.pos.x - MAX_HEAD[0], rat.pos.y - MAX_HEAD[1]), 2.2) * 1.3, 0.03, 1));
      }
    }
  }
  if (state === 'menu') {
    updateCamera(dt, look, true);
  } else {
    updateCamera(dt, state === 'play' ? look : { dx: 0, dy: 0 }, false);
    for (const tg of targets) {  // Marker wippen
      if (tg.marker && !tg.drunk && !tg.delivered && !tg.carried) {
        tg.marker.position.set(tg.x, 0.12 + Math.sin(t * 4 + tg.x) * 0.015, -tg.y);
      }
    }
    if (nestMarker) nestMarker.position.copy(b2t(NEST[0], NEST[1], 0.2 + Math.sin(t * 4) * 0.02));
    if (exitMarker) exitMarker.position.y = 0.16 + Math.sin(t * 4) * 0.02;
    hud.noise(noise, level !== 3);
    const radarTargets = level === 1 ? targets.filter(i => !i.drunk).map(i => [i.x, i.y])
      : level === 2 ? figures.filter(f => !f.delivered && !f.carried).map(f => [f.x, f.y]) : [];
    hud.radar({
      rat: { x: rat.pos.x, y: rat.pos.y, yaw: rat.yaw }, camYaw, targets: radarTargets, t,
      nest: level === 2 ? NEST : null, exit: level === 3 ? EXIT.pos : null,
      max: hunter.active ? { x: hunter.pos.x, y: hunter.pos.y } : { x: MAX_HEAD[0], y: MAX_HEAD[1] },
      cone: hunter.active ? hunter.coneRays() : null, jam: hunter.active && hunter.state === 'alert',
    });
  }
  renderer.render(scene, camera);
}

$('loader').classList.add('done');
showMenu();
requestAnimationFrame(tick);

// Debug in der Konsole
Object.assign(window, {
  scene, camera, rat, hunter, world, startLevel, items, input, getState: () => state, getNoise: () => noise,
  advance(sec, dt = 1 / 60) { for (let i = 0; i < sec / dt; i++) frame(dt); },
  go() { $("brief-go").click(); },
  setCam(y) { camYaw = y; manualT = 5; },
});
