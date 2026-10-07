import * as THREE from 'three';

// Wegpunkte (Blender-Koordinaten). Bad/Abstellkammer erreicht Max nicht (Palette blockiert den Weg).
const NAV = {
  living: [3.9, 1.9], lkL: [3.41, 3.95], lkK: [3.41, 4.95], kitchen: [4.2, 6.15],
  khK: [5.25, 4.95], khH: [6.2, 4.95], hallW: [7.2, 4.95], hallM: [8.6, 4.95], hallE: [9.95, 4.95],
  wcIn: [10.15, 6.0], bedDoor: [9.85, 3.85], bedN: [8.4, 3.2], bedW: [7.0, 2.9], bedS: [8.3, 1.25],
  bedFoot: [10.2, 2.7], bedE: [10.85, 3.3],
};
const EDGES = [
  ['living', 'lkL'], ['lkL', 'lkK'], ['lkK', 'kitchen'], ['lkK', 'khK'], ['kitchen', 'khK'], ['khK', 'khH'],
  ['khH', 'hallW'], ['hallW', 'hallM'], ['hallM', 'hallE'], ['hallE', 'wcIn'], ['hallE', 'bedDoor'],
  ['hallM', 'bedDoor'], ['bedDoor', 'bedN'], ['bedDoor', 'bedE'], ['bedDoor', 'bedFoot'], ['bedN', 'bedW'],
  ['bedN', 'bedS'], ['bedW', 'bedS'], ['bedS', 'bedFoot'], ['bedFoot', 'bedE'], ['bedN', 'bedFoot'],
];
const ROUTE = ['bedN', 'bedW', 'bedS', 'bedFoot', 'bedE', 'bedDoor', 'hallE', 'wcIn', 'hallE', 'hallM', 'hallW',
  'khH', 'khK', 'kitchen', 'lkK', 'lkL', 'living', 'lkL', 'lkK', 'khK', 'khH', 'hallW', 'hallM', 'bedDoor'];
const PAUSE_AT = { wcIn: 1.6, kitchen: 1.8, living: 2.2, bedE: 1.6, bedW: 1.2 };

export const VISION = { range: 4.2, half: THREE.MathUtils.degToRad(36) };
const SPEED = { patrol: 0.75, investigate: 0.9, search: 0.9, alert: 1.22 };
const R = 0.22;
const wrap = a => Math.atan2(Math.sin(a), Math.cos(a));

export class MaxHunter {
  constructor(world, col, scene) {
    const n = world.byName;
    this.world = world;
    this.col = col;
    this.root = n['MaxWach'];
    this.parts = {
      thighL: n['MaxW_OS_L'], thighR: n['MaxW_OS_R'], shinL: n['MaxW_US_L'], shinR: n['MaxW_US_R'],
      torso: n['MaxW_Torso'], head: n['MaxW_Kopf'], uaL: n['MaxW_OA_L'], uaR: n['MaxW_OA_R'],
      laL: n['MaxW_UA_L'], laR: n['MaxW_UA_R'], bucket: n['MaxW_Eimer'], hips: n['MaxW_Huefte'],
    };
    this.bucketCarry = this.parts.bucket.position.clone();
    this.blob = world.makeBlob(0.35, 0.5);
    this.iconAlert = world.textSprite('!', '#ff3b30', 0.32);
    this.iconHuh = world.textSprite('?', '#ffd60a', 0.3);
    scene.add(this.blob, this.iconAlert, this.iconHuh);

    // Sichtkegel auf dem Boden (Strahlen werden an Waenden abgeschnitten)
    this.rays = 22;
    const pos = new Float32Array((this.rays + 2) * 3);
    const idx = [];
    for (let i = 1; i <= this.rays; i++) idx.push(0, i, i + 1);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setIndex(idx);
    this.cone = new THREE.Mesh(g, new THREE.MeshBasicMaterial({
      color: 0x7dff9a, transparent: true, opacity: 0.2, depthWrite: false, side: THREE.DoubleSide,
    }));
    this.cone.frustumCulled = false;
    this.cone.renderOrder = 3;
    scene.add(this.cone);

    this.pos = new THREE.Vector2();
    this.events = [];
    this.active = false;
    this.adj = {};
    for (const k of Object.keys(NAV)) this.adj[k] = [];
    for (const [a, b] of EDGES) { this.adj[a].push(b); this.adj[b].push(a); }
  }

  setActive(on) {
    this.root.visible = on;
    this.blob.visible = this.cone.visible = on;
    this.iconAlert.visible = this.iconHuh.visible = false;
    this.active = on;
  }

  reset() {
    this.pos.set(...NAV.bedN);
    this.yaw = Math.PI;
    this.state = 'patrol';
    this.routeIdx = 0;
    this.path = [];
    this.pause = 0;
    this.suspicion = 0;
    this.lastSeen = null;
    this.unseen = 0;
    this.lookTimer = 0;
    this.slamT = -1;
    this.phase = 0;
    this.speed = 0;
    this.headLook = 0;
    this.stateTime = 0;
    this.seesRat = false;
    this.events = [];
  }

  // ----------------------------------------------------------------- Navigation
  nearestNode(x, y) {
    let best = null, bd = Infinity;
    for (const [k, [nx, ny]] of Object.entries(NAV)) {
      const d = Math.hypot(nx - x, ny - y) + (this.col.los(x, y, nx, ny) ? 0 : 10);
      if (d < bd) { bd = d; best = k; }
    }
    return best;
  }

  planTo(x, y) {
    const s = this.nearestNode(this.pos.x, this.pos.y), g = this.nearestNode(x, y);
    const dist = {}, prev = {};
    const open = new Set(Object.keys(NAV));
    for (const k of open) dist[k] = Infinity;
    dist[s] = 0;
    while (open.size) {
      let u = null;
      for (const k of open) if (u === null || dist[k] < dist[u]) u = k;
      open.delete(u);
      if (u === g) break;
      for (const v of this.adj[u]) {
        const d = dist[u] + Math.hypot(NAV[u][0] - NAV[v][0], NAV[u][1] - NAV[v][1]);
        if (d < dist[v]) { dist[v] = d; prev[v] = u; }
      }
    }
    const path = [];
    for (let k = g; k; k = prev[k]) { path.unshift(NAV[k]); if (k === s) break; }
    path.push([x, y]);
    this.path = path;
  }

  steer(dt, tx, ty, speed) {
    const dx = tx - this.pos.x, dy = ty - this.pos.y;
    const d = Math.hypot(dx, dy);
    const want = Math.atan2(dy, dx);
    const diff = wrap(want - this.yaw);
    this.yaw += Math.sign(diff) * Math.min(Math.abs(diff), 4.5 * dt);
    const v = Math.abs(diff) > 1.0 ? speed * 0.25 : speed;
    this.speed += (Math.min(v, d * 3) - this.speed) * Math.min(1, dt * 6);
    this.pos.x += Math.cos(this.yaw) * this.speed * dt;
    this.pos.y += Math.sin(this.yaw) * this.speed * dt;
    this.col.push(this.pos, R, this.col.maxRects);
    return d;
  }

  followPath(dt, speed) {
    while (this.path.length) {
      const [px, py] = this.path[0];
      // Abkuerzung: naechsten Punkt direkt ansteuern, wenn sichtbar
      if (this.path.length > 1 && this.col.raycast(this.pos.x, this.pos.y, this.path[1][0], this.path[1][1],
        this.col.maxRects, R * 0.9) >= 0.999) { this.path.shift(); continue; }
      const d = this.steer(dt, px, py, speed);
      if (d < 0.25) this.path.shift();
      return this.path.length > 0;
    }
    this.speed *= Math.max(0, 1 - dt * 6);
    return false;
  }

  turnTo(dt, x, y, rate = 3.5) {
    const diff = wrap(Math.atan2(y - this.pos.y, x - this.pos.x) - this.yaw);
    this.yaw += Math.sign(diff) * Math.min(Math.abs(diff), rate * dt);
    this.speed *= Math.max(0, 1 - dt * 6);
  }

  // ----------------------------------------------------------------- Wahrnehmung
  canSee(rat) {
    if (rat.hidden) return false;
    const dx = rat.pos.x - this.pos.x, dy = rat.pos.y - this.pos.y;
    const d = Math.hypot(dx, dy);
    if (d > VISION.range) return false;
    const ang = Math.abs(wrap(Math.atan2(dy, dx) - this.yaw - this.headLook));
    if (ang > VISION.half && d > 0.55) return false;
    return this.col.los(this.pos.x, this.pos.y, rat.pos.x, rat.pos.y);
  }

  hear(x, y, loud) {
    if (!this.active || this.state === 'alert' || this.slamT >= 0) return;
    if (loud < 0.05) return;
    this.investigate = [x, y];
    this.setState('investigate');
    this.planTo(x, y);
    this.events.push('huh');
  }

  setState(s) {
    if (this.state === s) return;
    this.state = s;
    this.stateTime = 0;
  }

  // ----------------------------------------------------------------- Update
  update(dt, t, rat, light) {
    const ev = this.events;
    this.stateTime += dt;
    const sees = this.canSee(rat);
    this.seesRat = sees;
    const d = rat.pos.distanceTo(this.pos);

    // Verdacht steigt beim Sehen (schneller nah, beim Rennen und im Hellen)
    if (sees) {
      const vis = THREE.MathUtils.clamp(light.level * 1.6, 0.35, 1.25);
      this.suspicion += dt * Math.max(0.35, 1.7 - d * 0.32) * vis * (rat.running ? 1.6 : 1);
      this.lastSeen = [rat.pos.x, rat.pos.y];
      this.unseen = 0;
    } else {
      this.unseen += dt;
      if (this.state !== 'alert') this.suspicion -= dt * 0.25;
    }
    if (d < 0.75 && rat.running && !rat.hidden) this.suspicion += dt * 1.5;  // hoert Trippeln direkt neben sich
    this.suspicion = THREE.MathUtils.clamp(this.suspicion, 0, 1);

    if (this.slamT >= 0) {
      this.slamT += dt;
      this.speed = 0;
      if (this.slamT > 0.32 && !this.slamHit) {
        this.slamHit = true;
        ev.push('bucket');
        const [lx, ly] = this.slamAt;
        if (Math.hypot(rat.pos.x - lx, rat.pos.y - ly) < 0.2 && !rat.hidden) ev.push('caught');
      }
      if (this.slamT > 1.2) { this.slamT = -1; }
    } else if (this.state !== 'alert' && this.suspicion >= 1) {
      this.setState('alert');
      ev.push('alert');
    } else if (this.state === 'alert') {
      if (this.lastSeen && sees) {
        this.steer(dt, rat.pos.x, rat.pos.y, SPEED.alert);
        if (d < 0.62) this.slam();
      } else if (this.lastSeen) {
        if (!this.path.length || this.stateTime % 1 < dt) this.planTo(...this.lastSeen);
        const moving = this.followPath(dt, SPEED.alert);
        if (!moving && d < 0.62 && !rat.hidden) this.slam();
      }
      if (this.unseen > 3.5) {
        this.setState('search');
        this.suspicion = 0.6;
        if (this.lastSeen) this.planTo(...this.lastSeen);
        ev.push('evasion');
      }
    } else if (this.state === 'search' || this.state === 'investigate') {
      const moving = this.followPath(dt, this.state === 'search' ? SPEED.search : SPEED.investigate);
      if (!moving) {
        this.lookTimer += dt;
        this.headLook = Math.sin(this.lookTimer * 1.8) * 0.9;
        if (this.lookTimer > (this.state === 'search' ? 5 : 3)) {
          this.lookTimer = 0;
          this.headLook = 0;
          this.resumePatrol();
          ev.push('calm');
        }
      }
      if (sees && this.suspicion > 0.3) this.turnTo(dt, rat.pos.x, rat.pos.y);
    } else if (sees && this.suspicion > 0.3) {
      this.setState('suspicious');
      this.turnTo(dt, rat.pos.x, rat.pos.y);
    } else {
      if (this.state === 'suspicious' && this.suspicion <= 0.05) this.resumePatrol();
      if (this.state === 'suspicious') this.speed *= Math.max(0, 1 - dt * 6);
      else this.patrol(dt);
    }

    this.animate(dt, t);
  }

  slam() {
    this.slamT = 0;
    this.slamHit = false;
    this.slamAt = [this.pos.x + Math.cos(this.yaw) * 0.55, this.pos.y + Math.sin(this.yaw) * 0.55];
    this.events.push('swoosh');
  }

  resumePatrol() {
    this.setState('patrol');
    let best = 0, bd = Infinity;
    ROUTE.forEach((k, i) => {
      const d = Math.hypot(NAV[k][0] - this.pos.x, NAV[k][1] - this.pos.y);
      if (d < bd) { bd = d; best = i; }
    });
    this.routeIdx = best;
    this.planTo(...NAV[ROUTE[best]]);
  }

  patrol(dt) {
    if (this.pause > 0) {
      this.pause -= dt;
      this.headLook = Math.sin(this.pause * 2.2) * 0.8;
      this.speed *= Math.max(0, 1 - dt * 6);
      if (this.pause <= 0) this.headLook = 0;
      return;
    }
    if (!this.path.length) {
      this.routeIdx = (this.routeIdx + 1) % ROUTE.length;
      const k = ROUTE[this.routeIdx];
      this.path = [NAV[k]];
      this.pauseNext = PAUSE_AT[k] || 0;
    }
    if (!this.followPath(dt, SPEED.patrol) && this.pauseNext) {
      this.pause = this.pauseNext;
      this.pauseNext = 0;
    }
  }

  animate(dt, t) {
    const p = this.parts;
    const s = this.speed;
    const k = Math.min(1, s / 0.6);
    const prevPhase = this.phase;
    this.phase += s * dt * 5.4;
    if (Math.floor(prevPhase / Math.PI) !== Math.floor(this.phase / Math.PI) && s > 0.2) this.events.push('step');
    const sw = Math.sin(this.phase) * 0.5 * k;
    p.thighL.rotation.z = sw;
    p.thighR.rotation.z = -sw;
    p.shinL.rotation.z = -Math.max(0, -Math.cos(this.phase)) * 0.7 * k;
    p.shinR.rotation.z = -Math.max(0, Math.cos(this.phase)) * 0.7 * k;
    this.root.position.set(this.pos.x, Math.abs(Math.sin(this.phase)) * 0.025 * k, -this.pos.y);
    this.root.rotation.set(0, this.yaw, 0);
    p.head.rotation.set(0, this.headLook, 0);

    // Eimer: tragen vs. zuschlagen
    let slam = 0;
    if (this.slamT >= 0) {
      const u = this.slamT;
      slam = u < 0.32 ? (u / 0.32) ** 2 : u < 0.75 ? 1 : Math.max(0, 1 - (u - 0.75) / 0.45);
    }
    p.torso.rotation.z = -0.55 * slam + Math.sin(t * 1.3) * 0.015;
    p.bucket.position.set(
      THREE.MathUtils.lerp(this.bucketCarry.x, 0.58, slam),
      THREE.MathUtils.lerp(this.bucketCarry.y, 0.004, slam),
      0,
    );
    const ua = THREE.MathUtils.lerp(1.15, 0.75, slam);
    p.uaL.rotation.set(-0.28, 0, ua + sw * 0.1);
    p.uaR.rotation.set(0.28, 0, ua - sw * 0.1);
    p.laL.rotation.set(0, 0, THREE.MathUtils.lerp(0.55, 0.2, slam));
    p.laR.rotation.set(0, 0, THREE.MathUtils.lerp(0.55, 0.2, slam));

    this.blob.position.set(this.pos.x, 0.008, -this.pos.y);
    const top = new THREE.Vector3(this.pos.x, 2.12, -this.pos.y);
    this.iconAlert.position.copy(top);
    this.iconHuh.position.copy(top);
    const alert = this.state === 'alert';
    this.iconAlert.visible = alert && this.stateTime < 2.5;
    this.iconHuh.visible = !alert && (this.state === 'suspicious' || this.state === 'investigate' || this.state === 'search');
    this.iconAlert.scale.setScalar(0.32 * (1 + Math.max(0, 0.4 - this.stateTime)));

    // Sichtkegel
    const arr = this.cone.geometry.attributes.position.array;
    arr[0] = this.pos.x; arr[1] = 0.012; arr[2] = -this.pos.y;
    for (let i = 0; i <= this.rays; i++) {
      const a = this.yaw + this.headLook - VISION.half + (2 * VISION.half * i) / this.rays;
      const ex = this.pos.x + Math.cos(a) * VISION.range, ey = this.pos.y + Math.sin(a) * VISION.range;
      const f = this.col.raycast(this.pos.x, this.pos.y, ex, ey);
      arr[(i + 1) * 3] = this.pos.x + (ex - this.pos.x) * f;
      arr[(i + 1) * 3 + 1] = 0.012;
      arr[(i + 1) * 3 + 2] = -(this.pos.y + (ey - this.pos.y) * f);
    }
    this.cone.geometry.attributes.position.needsUpdate = true;
    const c = this.cone.material.color;
    if (alert) c.set(0xff4a3d); else if (this.suspicion > 0.3 || this.state !== 'patrol') c.set(0xffd60a); else c.set(0x7dff9a);
    this.cone.material.opacity = alert ? 0.28 : 0.18;
  }

  coneRays() {
    // fuer das Radar
    const out = [];
    for (let i = 0; i <= 10; i++) {
      const a = this.yaw + this.headLook - VISION.half + (2 * VISION.half * i) / 10;
      const ex = this.pos.x + Math.cos(a) * VISION.range, ey = this.pos.y + Math.sin(a) * VISION.range;
      const f = this.col.raycast(this.pos.x, this.pos.y, ex, ey);
      out.push([this.pos.x + (ex - this.pos.x) * f, this.pos.y + (ey - this.pos.y) * f]);
    }
    return out;
  }
}
