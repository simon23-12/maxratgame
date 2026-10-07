// Touch: schwebender Joystick (Daumen irgendwo in der unteren Bildhaelfte), zweiter Finger / obere
// Haelfte dreht die Kamera, Aktionsknopf rechts unten. Desktop: WASD, Shift rennt, Maus dreht, Leertaste = Aktion.

export class Input {
  constructor(canvas, stickEl, knobEl, actionEl) {
    this.canvas = canvas;
    this.stickEl = stickEl;
    this.knobEl = knobEl;
    this.move = { x: 0, y: 0 };      // x = rechts, y = vorwaerts, Laenge 0..1
    this.stick = { x: 0, y: 0 };
    this.camDX = 0;
    this.camDY = 0;
    this.actionDown = false;
    this.actionPressed = false;
    this.keys = new Set();
    this.enabled = false;
    this.stickId = null;
    this.camId = null;
    this.origin = { x: 0, y: 0 };
    this.last = { x: 0, y: 0 };

    addEventListener('keydown', e => {
      if (e.repeat) return;
      this.keys.add(e.code);
      if (e.code === 'Space' || e.code === 'KeyE') { this.actionDown = true; this.actionPressed = true; e.preventDefault(); }
    });
    addEventListener('keyup', e => {
      this.keys.delete(e.code);
      if (e.code === 'Space' || e.code === 'KeyE') this.actionDown = false;
    });
    addEventListener('blur', () => { this.keys.clear(); this.actionDown = false; });

    canvas.addEventListener('pointerdown', e => this.down(e));
    canvas.addEventListener('pointermove', e => this.moveEv(e));
    canvas.addEventListener('pointerup', e => this.up(e));
    canvas.addEventListener('pointercancel', e => this.up(e));

    const press = e => { e.preventDefault(); e.stopPropagation(); this.actionDown = true; this.actionPressed = true; };
    const release = e => { e.preventDefault(); this.actionDown = false; };
    actionEl.addEventListener('pointerdown', press);
    actionEl.addEventListener('pointerup', release);
    actionEl.addEventListener('pointercancel', release);
    actionEl.addEventListener('pointerleave', release);
  }

  down(e) {
    if (!this.enabled) return;
    this.canvas.setPointerCapture(e.pointerId);
    if (e.pointerType === 'touch' && this.stickId === null && e.clientY > innerHeight * 0.38) {
      this.stickId = e.pointerId;
      this.origin = { x: e.clientX, y: e.clientY };
      this.stickEl.style.left = `${e.clientX}px`;
      this.stickEl.style.top = `${e.clientY}px`;
      this.stickEl.hidden = false;
      this.knobEl.style.transform = '';
    } else if (this.camId === null) {
      this.camId = e.pointerId;
      this.last = { x: e.clientX, y: e.clientY };
    }
  }

  moveEv(e) {
    if (e.pointerId === this.stickId) {
      let dx = e.clientX - this.origin.x, dy = e.clientY - this.origin.y;
      const max = 52;
      const len = Math.hypot(dx, dy);
      if (len > max) { dx *= max / len; dy *= max / len; }
      this.knobEl.style.transform = `translate(${dx}px, ${dy}px)`;
      this.stick = { x: dx / max, y: -dy / max };
    } else if (e.pointerId === this.camId) {
      this.camDX += e.clientX - this.last.x;
      this.camDY += e.clientY - this.last.y;
      this.last = { x: e.clientX, y: e.clientY };
    }
  }

  up(e) {
    if (e.pointerId === this.stickId) {
      this.stickId = null;
      this.stick = { x: 0, y: 0 };
      this.stickEl.hidden = true;
    }
    if (e.pointerId === this.camId) this.camId = null;
  }

  reset() {
    this.stickId = this.camId = null;
    this.stick = { x: 0, y: 0 };
    this.stickEl.hidden = true;
    this.actionDown = this.actionPressed = false;
    this.camDX = this.camDY = 0;
  }

  get run() { return this.keys.has('ShiftLeft') || this.keys.has('ShiftRight'); }

  // pro Frame aufrufen
  poll() {
    let x = this.stick.x, y = this.stick.y;
    const k = this.keys;
    let kx = 0, ky = 0;
    if (k.has('KeyW') || k.has('ArrowUp')) ky += 1;
    if (k.has('KeyS') || k.has('ArrowDown')) ky -= 1;
    if (k.has('KeyA') || k.has('ArrowLeft')) kx -= 1;
    if (k.has('KeyD') || k.has('ArrowRight')) kx += 1;
    if (kx || ky) {
      const l = Math.hypot(kx, ky);
      const s = this.run ? 1 : 0.84;  // Tastatur: schleichen, mit Shift rennen
      x = (kx / l) * s;
      y = (ky / l) * s;
    }
    this.move = { x, y };
    const out = { dx: this.camDX, dy: this.camDY, pressed: this.actionPressed };
    this.camDX = this.camDY = 0;
    this.actionPressed = false;
    return out;
  }
}
