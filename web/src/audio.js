// Alle Sounds werden synthetisiert (keine Audiodateien noetig).

let ctx = null;
let master = null;
let noiseBuf = null;

export function initAudio() {
  if (ctx) { ctx.resume(); return; }
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return;
  ctx = new AC();
  master = ctx.createGain();
  master.gain.value = 0.7;
  master.connect(ctx.destination);
  noiseBuf = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
  const d = noiseBuf.getChannelData(0);
  for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
}

function gain(v, t0, attack, decay) {
  const g = ctx.createGain();
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(Math.max(v, 0.0002), t0 + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + attack + decay);
  g.connect(master);
  return g;
}

function tone(type, freq, vol, attack, decay, { to = null, delay = 0 } = {}) {
  if (!ctx) return;
  const t0 = ctx.currentTime + delay;
  const o = ctx.createOscillator();
  o.type = type;
  o.frequency.setValueAtTime(freq, t0);
  if (to) o.frequency.exponentialRampToValueAtTime(to, t0 + attack + decay);
  o.connect(gain(vol, t0, attack, decay));
  o.start(t0);
  o.stop(t0 + attack + decay + 0.05);
}

function noise(vol, attack, decay, { type = 'bandpass', freq = 1000, q = 1, delay = 0, rate = 1 } = {}) {
  if (!ctx) return;
  const t0 = ctx.currentTime + delay;
  const s = ctx.createBufferSource();
  s.buffer = noiseBuf;
  s.playbackRate.value = rate;
  const f = ctx.createBiquadFilter();
  f.type = type;
  f.frequency.value = freq;
  f.Q.value = q;
  s.connect(f);
  f.connect(gain(vol, t0, attack, decay));
  s.start(t0, Math.random() * 0.5);
  s.stop(t0 + attack + decay + 0.05);
}

export const sfx = {
  clink(v = 1) {  // Dose
    const p = 0.9 + Math.random() * 0.25;
    [2300, 3700, 5400].forEach((f, i) => tone('sine', f * p, 0.12 * v / (i + 1), 0.002, 0.25 - i * 0.05));
    noise(0.1 * v, 0.002, 0.06, { freq: 4000, q: 2 });
  },
  bottle(v = 1) {  // Glasflasche
    const p = 0.9 + Math.random() * 0.2;
    [880, 1760, 2650].forEach((f, i) => tone('sine', f * p, 0.16 * v / (i + 1), 0.003, 0.4));
    noise(0.12 * v, 0.002, 0.08, { freq: 2500, q: 3 });
  },
  slurp(v = 1) {
    noise(0.22 * v, 0.03, 0.22, { freq: 700 + Math.random() * 500, q: 6 });
    tone('sine', 300, 0.05 * v, 0.02, 0.15, { to: 500 });
  },
  burp(v = 1) {
    tone('sawtooth', 95, 0.25 * v, 0.03, 0.5, { to: 70 });
    noise(0.12 * v, 0.03, 0.45, { type: 'lowpass', freq: 400 });
  },
  squeak(v = 1) { tone('sine', 2600, 0.08 * v, 0.01, 0.09, { to: 3400 }); },
  pickup() { tone('square', 660, 0.07, 0.005, 0.08); tone('square', 990, 0.07, 0.005, 0.1, { delay: 0.08 }); },
  deliver() { [523, 659, 784, 1047].forEach((f, i) => tone('triangle', f, 0.12, 0.005, 0.18, { delay: i * 0.07 })); },
  splash(v = 1) { noise(0.25 * v, 0.01, 0.2, { type: 'highpass', freq: 1200 }); },
  snore(v = 1) {
    noise(0.35 * v, 0.6, 0.7, { type: 'lowpass', freq: 260, rate: 0.6 });
    tone('sawtooth', 55, 0.04 * v, 0.6, 0.6, { to: 48 });
  },
  step(v = 1) { tone('sine', 75, 0.35 * v, 0.005, 0.14, { to: 45 }); noise(0.06 * v, 0.003, 0.05, { type: 'lowpass', freq: 500 }); },
  alert() {  // das beruehmte "!"
    [440, 554, 659, 880].forEach(f => tone('sawtooth', f, 0.08, 0.005, 0.5, { to: f * 0.97 }));
    tone('square', 1760, 0.06, 0.005, 0.25);
  },
  huh() { tone('triangle', 500, 0.1, 0.01, 0.25, { to: 800 }); },
  swoosh() { noise(0.3, 0.05, 0.25, { freq: 600, q: 0.7 }); },
  bucket() { tone('sine', 120, 0.4, 0.003, 0.3, { to: 70 }); noise(0.25, 0.002, 0.15, { type: 'lowpass', freq: 900 }); },
  caught() { [392, 330, 262, 196].forEach((f, i) => tone('sawtooth', f, 0.1, 0.01, 0.3, { delay: i * 0.18 })); },
  wake() { tone('sawtooth', 150, 0.18, 0.05, 0.6, { to: 320 }); noise(0.15, 0.05, 0.5, { freq: 500 }); },
  win() { [523, 659, 784, 1047, 1319].forEach((f, i) => tone('triangle', f, 0.13, 0.005, 0.35, { delay: i * 0.1 })); },
  ui() { tone('square', 880, 0.05, 0.003, 0.05); },
};

// =========================================================================== Musik
// Kleiner Sequencer mit Vorausplanung. Alle Stuecke sind eigene Kompositionen.
// Notation: "C4" Note, "C4+E4" Akkord, "." Pause; Drums: k = Kick, s = Snare, h = Hihat, o = offene Hihat.

const NOTE = { C: 0, 'C#': 1, D: 2, 'D#': 3, E: 4, F: 5, 'F#': 6, G: 7, 'G#': 8, A: 9, 'A#': 10, B: 11 };
const freq = n => {
  const m = /^([A-G]#?)(-?\d)$/.exec(n);
  return 440 * 2 ** ((NOTE[m[1]] + (+m[2] + 1) * 12 - 69) / 12);
};
const seq = s => s.trim().split(/\s+/);

const SONGS = {
  // Menue: alberne Polka mit Tuba
  menu: {
    bpm: 136, steps: 2, vol: 0.5, tracks: [
      { inst: 'tuba', pat: seq('C2 . G1 . C2 . G1 . F1 . C2 . F1 . C2 . G1 . C2 . G1 . G1 . D2 . G1 . B1 .') },
      { inst: 'pluck', pat: seq('. C4+E4+G4 . C4+E4+G4 . C4+E4+G4 . C4+E4+G4 . C4+F4+A4 . C4+F4+A4 . C4+E4+G4 . C4+E4+G4 . B3+D4+G4 . B3+D4+G4 . B3+D4+F4 . B3+D4+F4') },
      { inst: 'lead', pat: seq('E5 E5 G5 . E5 C5 D5 . F5 F5 A5 . F5 D5 E5 . D5 D5 B4 . D5 G5 F5 E5 D5 . G4 . B4 . D5 . E5 E5 G5 . E5 C5 D5 . F5 F5 A5 . C6 A5 G5 . F5 E5 D5 B4 G4 . D5 . C5 . . . G4 . C5 .') },
      { inst: 'drum', pat: seq('k h s h k h s h') },
    ],
  },
  // Level 1+2: auf Zehenspitzen schleichen (Pizzicato, chromatisch, leise)
  sneak: {
    bpm: 104, steps: 4, vol: 0.42, tracks: [
      { inst: 'pizz', pat: seq('A2 . . . C3 . . . D3 . D#3 . E3 . . . A2 . . . C3 . . . E3 . D#3 . D3 . C3 . A2 . . . C3 . . . D3 . D#3 . E3 . . . F3 . E3 . D#3 . D3 . C3 . B2 . G#2 . . .') },
      { inst: 'tink', pat: seq('. . . . . . E5 . . . . . . . A5 . . . . . . . E5 . . . . . . . G#5 .') },
      { inst: 'lead', pat: seq('. . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . A4 . . C5 . . E5 . D#5 . E5 . . . . . F5 . E5 . D#5 . D5 . C5 . B4 . A4 . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .') },
      { inst: 'drum', pat: seq('h . . . . . h . h . . . . . h .') },
      { inst: 'whistle', pat: seq(Array(127).fill('.').concat(['W']).join(' ')) },
    ],
  },
  // Level 3: Spionage-Ostinato, angespannt
  stealth: {
    bpm: 92, steps: 4, vol: 0.45, tracks: [
      { inst: 'bass', pat: seq('D2 . D2 . D2 . D2 . D2 . D2 . F2 . E2 . D2 . D2 . D2 . D2 . A#1 . A#1 . C2 . C2 .') },
      { inst: 'drum', pat: seq('k . . . . . . k . . k . . . . . k . . . . . . k . . k . . . h .') },
      { inst: 'stab', pat: seq('D4+F4+A4+E5 . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . A#3+D4+F4+C5 . . . . . . . . . . . . . . . . . . . . . . . . . . . . . . .') },
      { inst: 'lead', pat: seq('. . . . . . . . A5 . . . . . . . . . . . G5 . F5 . E5 . . . . . . . . . . . . . . . D5 . . . . . . . . . . . E5 . F5 . A4 . . . . . . .') },
    ],
  },
  // Alarm: schnell und hektisch
  alert: {
    bpm: 170, steps: 4, vol: 0.5, tracks: [
      { inst: 'bass', pat: seq('D2 D2 D3 D2 F2 D2 G2 G#2 D2 D2 D3 D2 C3 A#2 A2 G#2') },
      { inst: 'drum', pat: seq('k h h h s h k h k h k h s h s s') },
      { inst: 'stab', pat: seq('D4+F4+A4 . . D4+F4+A4 . . D4+F4+A4 . . . C4+E4+G4 . C#4+F4+G#4 . . . D4+F4+A4 . . D4+F4+A4 . . D4+F4+A4 . . . F4+A4+C5 . E4+G#4+B4 . . .') },
      { inst: 'lead', pat: seq('A5 . G5 A5 . . D6 . C6 . A5 . G5 . F5 G5 A5 . G5 A5 . . F6 . E6 . D6 . C6 . A5 .') },
    ],
  },
};

function musicVoice(bus, type, f, t, dur, vol, { attack = 0.005, filter = null, glide = null, vib = 0 } = {}) {
  const o = ctx.createOscillator();
  o.type = type;
  o.frequency.setValueAtTime(f, t);
  if (glide) o.frequency.exponentialRampToValueAtTime(glide, t + dur);
  if (vib) {
    const l = ctx.createOscillator();
    const lg = ctx.createGain();
    l.frequency.value = 5.5;
    lg.gain.value = f * vib;
    l.connect(lg);
    lg.connect(o.frequency);
    l.start(t);
    l.stop(t + dur + 0.1);
  }
  const g = ctx.createGain();
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(vol, t + attack);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  let node = o;
  if (filter) {
    const fl = ctx.createBiquadFilter();
    fl.type = 'lowpass';
    fl.frequency.value = filter;
    o.connect(fl);
    node = fl;
  }
  node.connect(g);
  g.connect(bus);
  o.start(t);
  o.stop(t + dur + 0.05);
}

function musicNoise(bus, t, dur, vol, type, f) {
  const s = ctx.createBufferSource();
  s.buffer = noiseBuf;
  const fl = ctx.createBiquadFilter();
  fl.type = type;
  fl.frequency.value = f;
  const g = ctx.createGain();
  g.gain.setValueAtTime(vol, t);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  s.connect(fl);
  fl.connect(g);
  g.connect(bus);
  s.start(t, Math.random() * 0.5);
  s.stop(t + dur + 0.05);
}

const INST = {
  tuba: (b, n, t, sp) => musicVoice(b, 'sawtooth', freq(n), t, sp * 1.6, 0.22, { filter: 420, attack: 0.02 }),
  pluck: (b, n, t, sp) => musicVoice(b, 'triangle', freq(n), t, sp * 0.8, 0.07),
  lead: (b, n, t, sp) => musicVoice(b, 'square', freq(n), t, sp * 0.9, 0.05, { filter: 2600, vib: 0.006 }),
  pizz: (b, n, t, sp) => musicVoice(b, 'triangle', freq(n), t, sp * 1.4, 0.3, { filter: 1200, attack: 0.003 }),
  tink: (b, n, t, sp) => musicVoice(b, 'sine', freq(n), t, sp * 2.5, 0.06, { attack: 0.002 }),
  bass: (b, n, t, sp) => musicVoice(b, 'sawtooth', freq(n), t, sp * 0.9, 0.16, { filter: 600 }),
  stab: (b, n, t, sp) => musicVoice(b, 'sawtooth', freq(n), t, sp * 3, 0.045, { filter: 1800, attack: 0.01 }),
  whistle: (b, _n, t) => musicVoice(b, 'sine', 900, t, 0.7, 0.07, { glide: 2400, attack: 0.05 }),  // Lotusfloete
  drum: (b, n, t) => {
    if (n === 'k') musicVoice(b, 'sine', 110, t, 0.18, 0.5, { glide: 40, attack: 0.002 });
    if (n === 's') musicNoise(b, t, 0.14, 0.22, 'bandpass', 1800);
    if (n === 'h') musicNoise(b, t, 0.04, 0.08, 'highpass', 7000);
    if (n === 'o') musicNoise(b, t, 0.2, 0.07, 'highpass', 6000);
  },
};

class Music {
  constructor() {
    this.song = null;
    this.rate = 1;
    this.muted = (() => { try { return localStorage.getItem('maxrat.mute') === '1'; } catch { return false; } })();
  }

  ensureBus() {
    if (this.bus || !ctx) return;
    this.bus = ctx.createGain();
    this.bus.gain.value = this.muted ? 0 : 0.8;
    this.bus.connect(master);
  }

  play(name) {
    if (!ctx || this.name === name) return;
    this.ensureBus();
    this.name = name;
    this.song = SONGS[name];
    this.step = 0;
    this.next = ctx.currentTime + 0.08;
    this.songGain?.gain.setTargetAtTime(0, ctx.currentTime, 0.08);
    this.songGain = ctx.createGain();
    this.songGain.gain.value = this.song.vol;
    this.songGain.connect(this.bus);
    if (!this.timer) this.timer = setInterval(() => this.schedule(), 25);
  }

  stop() {
    this.name = null;
    this.song = null;
    this.songGain?.gain.setTargetAtTime(0, ctx.currentTime, 0.1);
  }

  setRate(r) { this.rate = r; }

  toggleMute() {
    this.muted = !this.muted;
    try { localStorage.setItem('maxrat.mute', this.muted ? '1' : '0'); } catch { /* egal */ }
    this.ensureBus();
    if (this.bus) this.bus.gain.setTargetAtTime(this.muted ? 0 : 0.8, ctx.currentTime, 0.05);
    return this.muted;
  }

  schedule() {
    if (!this.song || !ctx || ctx.state !== 'running') return;
    const sp = 60 / this.song.bpm / this.song.steps / this.rate;   // Sekunden pro Schritt
    while (this.next < ctx.currentTime + 0.15) {
      for (const tr of this.song.tracks) {
        const n = tr.pat[this.step % tr.pat.length];
        if (n === '.') continue;
        for (const part of n.split('+')) INST[tr.inst](this.songGain, part, this.next, sp);
      }
      this.next += sp;
      this.step++;
    }
  }
}

export const music = new Music();

// Test-Hilfe: rendert ein Stueck offline und liefert Lautstaerke (RMS) zurueck
export async function _renderSong(name, secs = 4) {
  const saved = [ctx, master, noiseBuf];
  ctx = new OfflineAudioContext(1, 44100 * secs, 44100);
  master = ctx.createGain();
  master.connect(ctx.destination);
  noiseBuf = ctx.createBuffer(1, 44100, 44100);
  noiseBuf.getChannelData(0).forEach((_, i, a) => { a[i] = Math.random() * 2 - 1; });
  const song = SONGS[name];
  const sp = 60 / song.bpm / song.steps;
  let peak = 0;
  for (let step = 0, t = 0; t < secs - 0.5; step++, t += sp) {
    for (const tr of song.tracks) {
      const n = tr.pat[step % tr.pat.length];
      if (n !== '.') for (const part of n.split('+')) INST[tr.inst](master, part, t, sp);
    }
  }
  const buf = await ctx.startRendering();
  const d = buf.getChannelData(0);
  let sum = 0;
  for (const v of d) { sum += v * v; peak = Math.max(peak, Math.abs(v)); }
  [ctx, master, noiseBuf] = saved;
  return { rms: Math.sqrt(sum / d.length), peak };
}
