// DOM-HUD und das Soliton-Radar (oben rechts, Kamerablick zeigt nach oben).

const $ = id => document.getElementById(id);

export class Hud {
  constructor(layout) {
    this.layout = layout;
    this.el = {
      hud: $('hud'), title: $('obj-title'), count: $('obj-count'), noise: $('noise'), noiseFill: $('noise-fill'),
      noiseFace: $('noise-face'), status: $('status'), toast: $('toast'), action: $('action'),
      actionLabel: $('action-label'), actionRing: $('action-ring'), radar: $('radar'), timer: $('timer'),
    };
    this.ctx = this.el.radar.getContext('2d');
    this.toastT = 0;
  }

  show(on) { this.el.hud.hidden = !on; if (!on) this.action(null); }

  objective(title, count) {
    this.el.title.textContent = title;
    this.el.count.textContent = count;
  }

  timer(sec) {
    const s = Math.max(0, Math.ceil(sec));
    this.el.timer.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    this.el.timer.className = sec < 30 ? 'low' : '';
  }

  noise(v, visible) {
    this.el.noise.hidden = !visible;
    if (!visible) return;
    this.el.noiseFill.style.width = `${Math.min(100, v * 100)}%`;
    this.el.noiseFill.style.background = v > 0.7 ? '#ff453a' : v > 0.4 ? '#ffd60a' : '#7dff9a';
    this.el.noiseFace.textContent = v > 0.7 ? '😠' : v > 0.4 ? '😪' : '😴';
  }

  status(text, kind) {
    this.el.status.textContent = text || '';
    this.el.status.className = kind || '';
  }

  toast(text, secs = 1.6) {
    this.el.toast.textContent = text;
    this.el.toast.classList.add('on');
    this.toastT = secs;
  }

  action(label, progress = 0) {
    const a = this.el.action;
    if (!label) { a.hidden = true; return; }
    a.hidden = false;
    this.el.actionLabel.textContent = label;
    this.el.actionRing.style.setProperty('--p', `${Math.round(progress * 100)}%`);
  }

  tick(dt) {
    if (this.toastT > 0) {
      this.toastT -= dt;
      if (this.toastT <= 0) this.el.toast.classList.remove('on');
    }
  }

  // ---------------------------------------------------------------- Radar
  radar({ rat, camYaw, targets = [], max = null, cone = null, exit = null, nest = null, jam = false, t = 0 }) {
    const c = this.ctx;
    const S = this.el.radar.width;
    const scale = S / 14;           // 14 m Durchmesser sichtbar
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.clearRect(0, 0, S, S);
    c.save();
    c.beginPath();
    c.arc(S / 2, S / 2, S / 2 - 2, 0, Math.PI * 2);
    c.clip();
    c.fillStyle = jam ? 'rgba(70,10,10,0.85)' : 'rgba(5,30,18,0.82)';
    c.fillRect(0, 0, S, S);
    // Welt -> Radar: Kamera-Blickrichtung zeigt nach oben
    c.translate(S / 2, S / 2);
    c.rotate(camYaw - Math.PI / 2);
    c.scale(scale, -scale);
    c.translate(-rat.x, -rat.y);
    c.fillStyle = jam ? 'rgba(255,120,110,0.5)' : 'rgba(125,255,154,0.55)';
    for (const [x0, y0, x1, y1] of this.layout.walls) c.fillRect(x0, y0, x1 - x0, y1 - y0);
    c.fillStyle = jam ? 'rgba(255,120,110,0.18)' : 'rgba(125,255,154,0.18)';
    for (const s of this.layout.solids) { const [x0, y0, x1, y1] = s.r; c.fillRect(x0, y0, x1 - x0, y1 - y0); }
    const dot = (x, y, r, col) => { c.fillStyle = col; c.beginPath(); c.arc(x, y, r / scale, 0, Math.PI * 2); c.fill(); };
    if (nest) dot(nest[0], nest[1], 5 + Math.sin(t * 4) * 1.5, 'rgba(90,200,255,0.9)');
    for (const [x, y] of targets) dot(x, y, 3.2, '#ffd60a');
    if (exit) dot(exit[0], exit[1], 5 + Math.sin(t * 6) * 2, '#ffffff');
    if (cone && max) {
      c.fillStyle = jam ? 'rgba(255,60,50,0.45)' : 'rgba(125,255,154,0.35)';
      c.beginPath();
      c.moveTo(max.x, max.y);
      for (const [x, y] of cone) c.lineTo(x, y);
      c.closePath();
      c.fill();
    }
    if (max) dot(max.x, max.y, 5, '#ff453a');
    c.restore();
    // Ratte in der Mitte (zeigt immer "nach oben" relativ zur eigenen Blickrichtung)
    c.save();
    c.translate(S / 2, S / 2);
    c.rotate(-(rat.yaw - camYaw));
    c.fillStyle = '#fff';
    c.beginPath();
    c.moveTo(0, -8);
    c.lineTo(5.5, 6);
    c.lineTo(-5.5, 6);
    c.closePath();
    c.fill();
    c.restore();
    if (jam) {  // Stoerung wie im Alarm
      c.fillStyle = 'rgba(255,255,255,0.12)';
      for (let i = 0; i < 40; i++) c.fillRect(Math.random() * S, Math.random() * S, 10 + Math.random() * 30, 2);
    }
    c.strokeStyle = jam ? '#ff453a' : 'rgba(125,255,154,0.8)';
    c.lineWidth = 3;
    c.beginPath();
    c.arc(S / 2, S / 2, S / 2 - 2, 0, Math.PI * 2);
    c.stroke();
  }
}
