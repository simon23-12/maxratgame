import * as THREE from 'three';

export const RAT = {
  sneak: 0.55,   // m/s, leise
  run: 1.35,     // m/s, laut
  radius: 0.05,
};

const wrap = a => Math.atan2(Math.sin(a), Math.cos(a));

export class Rat {
  constructor(world) {
    const n = world.byName;
    this.root = n['Ratte'];
    this.head = n['Ratte_Kopf'];
    this.legs = ['VL', 'VR', 'HL', 'HR'].map(k => n['Ratte_Bein_' + k]);
    this.tail = [];
    for (let i = 1; n['Ratte_Schwanz_' + i]; i++) this.tail.push(n['Ratte_Schwanz_' + i]);
    this.headRest = this.head.position.clone();
    this.blob = world.makeBlob(0.08);
    this.pos = new THREE.Vector2();
    this.prev = new THREE.Vector2();
    this.vel = new THREE.Vector2();
    this.yaw = 0;
    this.speed = 0;      // Zielgeschwindigkeit (geglaettet)
    this.actual = 0;     // tatsaechliche Geschwindigkeit nach Kollision
    this.phase = 0;
    this.boost = 1;
    this.busy = false;   // trinkt gerade
    this.carry = null;
    this.hidden = false;
    this.running = false;
  }

  reset(x, y, yaw) {
    this.pos.set(x, y);
    this.prev.copy(this.pos);
    this.vel.set(0, 0);
    this.yaw = yaw;
    this.speed = this.actual = 0;
    this.boost = 1;
    this.busy = false;
    this.carry = null;
  }

  get forward() { return new THREE.Vector2(Math.cos(this.yaw), Math.sin(this.yaw)); }

  mouth() { return this.pos.clone().addScaledVector(this.forward, 0.09); }

  // wish: gewuenschte Laufrichtung in Welt-Koordinaten, Laenge 0..1
  update(dt, wish, runKey, col) {
    const mag = Math.min(1, wish.length());
    let target = 0;
    this.running = false;
    if (!this.busy && mag > 0.08) {
      this.running = runKey || mag > 0.86;
      target = (this.running ? RAT.run : RAT.sneak * Math.min(1, mag / 0.86)) * this.boost * (this.carry ? 0.85 : 1);
      const want = Math.atan2(wish.y, wish.x);
      const d = wrap(want - this.yaw);
      this.yaw += Math.sign(d) * Math.min(Math.abs(d), 13 * dt);
      if (Math.abs(d) > 2.2) target *= 0.3;  // erst umdrehen
    }
    this.speed += (target - this.speed) * Math.min(1, dt * 12);
    this.prev.copy(this.pos);
    this.pos.addScaledVector(this.forward, this.speed * dt);
    col.push(this.pos, RAT.radius, col.ratRects);
    this.vel.subVectors(this.pos, this.prev).divideScalar(Math.max(dt, 1e-4));
    this.actual = this.vel.length();
    this.hidden = col.inUnder(this.pos.x, this.pos.y);
  }

  animate(dt, t) {
    const s = this.actual;
    const k = Math.min(1, s / 0.3);
    this.phase += s * dt * 60;
    const sw = Math.sin(this.phase) * 0.75 * k;
    this.legs[0].rotation.z = sw;
    this.legs[3].rotation.z = sw;
    this.legs[1].rotation.z = -sw;
    this.legs[2].rotation.z = -sw;
    this.root.position.set(this.pos.x, Math.abs(Math.sin(this.phase)) * 0.004 * k, -this.pos.y);
    this.root.rotation.set(0, this.yaw, 0);
    // Kopf: schnueffeln, beim Trinken runter
    if (this.busy) {
      this.head.rotation.set(0, Math.sin(t * 3) * 0.05, -0.45 + Math.sin(t * 14) * 0.07);
    } else {
      this.head.rotation.set(0, (1 - k) * Math.sin(t * 1.7) * 0.25, Math.sin(t * 17) * 0.03 * (1 - k) - 0.05 * k);
    }
    const wav = s > 0.05 ? 10 : 2.4;
    const amp = 0.14 + 0.12 * k;
    this.tail.forEach((seg, i) => {
      seg.rotation.y = Math.sin(t * wav - i * 0.75) * amp;
      seg.rotation.z = i === 0 ? -0.12 : 0.02;
    });
    this.blob.position.set(this.pos.x, 0.007, -this.pos.y);
    if (this.carry) {
      const m = this.mouth();
      this.carry.position.set(m.x, 0.025, -m.y);
      this.carry.rotation.set(0, this.yaw + Math.PI / 2, 0);
    }
  }
}
