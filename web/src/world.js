import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

// Blender (x=Ost, y=Nord, z=oben)  ->  three (x, z, -y)
export const b2t = (x, y, z = 0) => new THREE.Vector3(x, z, -y);

const CHAR_ROOTS = ['Ratte', 'MaxWach'];

const BASE = import.meta.env.BASE_URL;  // funktioniert lokal und unter /<repo>/ auf GitHub Pages

export async function loadWorld(onProgress) {
  const manager = new THREE.LoadingManager();
  manager.onProgress = (_u, done, total) => onProgress(done / total);
  const layout = await fetch(`${BASE}assets/layout.json`).then(r => r.json());
  const texLoader = new THREE.TextureLoader(manager);
  const lightmap = await texLoader.loadAsync(`${BASE}assets/lightmap.webp`)
    .catch(() => texLoader.loadAsync(`${BASE}assets/lightmap.png`));
  const gltf = await new GLTFLoader(manager).loadAsync(`${BASE}assets/apartment.glb`);
  return new World(layout, lightmap, gltf);
}

function canvasTex(w, h, draw) {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  draw(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export class World {
  constructor(layout, lightmap, gltf) {
    this.layout = layout;
    this.root = gltf.scene;
    this.byName = {};
    this.root.traverse(o => { this.byName[o.name] = o; });

    // ---- gebackenes Licht: Unlit + Lightmap fuer alles Statische/Kleinkram
    lightmap.flipY = false;
    lightmap.colorSpace = THREE.SRGBColorSpace;
    lightmap.channel = 1;
    lightmap.anisotropy = 4;
    this.lightmap = lightmap;
    this.lmIntensity = Math.PI * layout.meta.lm_max;
    this.glowMats = [];
    this.rippleTex = canvasTex(256, 256, (g) => {
      g.fillStyle = 'rgb(190,190,190)';
      g.fillRect(0, 0, 256, 256);
      g.strokeStyle = 'rgba(255,255,255,0.55)';
      for (let i = 0; i < 26; i++) {
        g.lineWidth = 1 + Math.random() * 2.5;
        g.beginPath();
        const y = Math.random() * 256;
        for (let x = -10; x <= 266; x += 8) g.lineTo(x, y + Math.sin(x * 0.05 + i) * 9 + Math.sin(x * 0.13) * 4);
        g.stroke();
      }
    });
    this.rippleTex.wrapS = this.rippleTex.wrapT = THREE.RepeatWrapping;
    this.rippleTex.repeat.set(3, 3);

    const charMeshes = new Set();
    for (const n of CHAR_ROOTS) this.byName[n]?.traverse(o => o.isMesh && charMeshes.add(o));
    const cache = new Map();
    this.root.traverse(o => {
      if (!o.isMesh || charMeshes.has(o)) return;
      o.material = Array.isArray(o.material) ? o.material.map(m => this.convert(m, cache)) : this.convert(o.material, cache);
      if (o.userData.role === 'static' || o.userData.role === 'ceiling') {
        o.matrixAutoUpdate = false;
        o.updateMatrix();
      }
    });

    // ---- Figuren: Lambert + Echtzeitlicht, Helligkeit wird pro Frame aus den Raumlichtern geschaetzt
    this.chars = {};
    for (const n of CHAR_ROOTS) {
      const mats = [];
      const conv = new Map();
      this.byName[n]?.traverse(o => {
        if (!o.isMesh) return;
        const swap = m => {
          if (!conv.has(m)) {
            const nm = new THREE.MeshLambertMaterial({ color: m.color.clone(), map: m.map || null });
            mats.push({ mat: nm, base: m.color.clone() });
            conv.set(m, nm);
          }
          return conv.get(m);
        };
        o.material = Array.isArray(o.material) ? o.material.map(swap) : swap(o.material);
      });
      this.chars[n] = mats;
    }
    this.ambient = new THREE.AmbientLight(0xffffff, 0.75);
    this.sun = new THREE.DirectionalLight(0xffffff, 1.2);
    this.sun.position.set(0.4, 1, 0.25);

    this.lights = layout.lights.map(l => ({ ...l }));

    // ---- Ofen-Schimmer
    const glowTex = canvasTex(64, 64, (g) => {
      const grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
      grd.addColorStop(0, 'rgba(255,150,60,1)');
      grd.addColorStop(0.4, 'rgba(255,90,20,0.35)');
      grd.addColorStop(1, 'rgba(255,60,0,0)');
      g.fillStyle = grd;
      g.fillRect(0, 0, 64, 64);
    });
    this.ovenGlow = new THREE.Sprite(new THREE.SpriteMaterial({
      map: glowTex, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.5,
    }));
    this.ovenGlow.position.copy(b2t(...layout.oven));
    this.ovenGlow.scale.setScalar(0.55);

    this.blobTex = canvasTex(64, 64, (g) => {
      const grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
      grd.addColorStop(0, 'rgba(0,0,0,0.75)');
      grd.addColorStop(1, 'rgba(0,0,0,0)');
      g.fillStyle = grd;
      g.fillRect(0, 0, 64, 64);
    });

    this.ceiling = this.byName['Ceiling'];
  }

  convert(m, cache) {
    if (cache.has(m)) return cache.get(m);
    let out;
    const emissive = m.emissive && (m.emissive.r + m.emissive.g + m.emissive.b) > 0 && m.emissiveIntensity > 0;
    if (m.name.startsWith('Wasser')) {
      out = new THREE.MeshBasicMaterial({
        color: m.color.clone().multiplyScalar(1.15), map: this.rippleTex, lightMap: this.lightmap,
        lightMapIntensity: this.lmIntensity, transparent: true, opacity: 0.62, depthWrite: false,
      });
    } else if (emissive && m.name.startsWith('Glow_')) {
      out = new THREE.MeshLambertMaterial({
        color: m.color, lightMap: this.lightmap, lightMapIntensity: this.lmIntensity,
        emissive: m.emissive.clone().multiplyScalar(m.emissiveIntensity),
      });
      this.glowMats.push({ mat: out, base: out.emissive.clone() });
    } else if (emissive) {
      out = new THREE.MeshBasicMaterial({
        color: m.emissive.clone().multiplyScalar(m.emissiveIntensity), map: m.emissiveMap || null,
      });
    } else {
      out = new THREE.MeshBasicMaterial({
        color: m.color, map: m.map || null, lightMap: this.lightmap, lightMapIntensity: this.lmIntensity,
      });
      if (out.map) out.map.anisotropy = 8;
    }
    out.name = m.name;
    cache.set(m, out);
    m.dispose();
    return out;
  }

  addTo(scene) {
    scene.add(this.root, this.ambient, this.sun, this.ovenGlow);
  }

  // grobe Licht-Schaetzung an einem Punkt (Lichter im selben Raum zaehlen voll)
  lightAt(col, x, y, z = 0.1) {
    const room = col.roomAt(x, y);
    let r = 0.05, g = 0.055, b = 0.08;
    for (const l of this.lights) {
      if (l.room === undefined) l.room = col.roomAt(l.pos[0], l.pos[1]);
      const dx = l.pos[0] - x, dy = l.pos[1] - y, dz = l.pos[2] - z;
      let w = (l.energy / (dx * dx + dy * dy + dz * dz + 0.6)) * 0.06;
      if (l.room !== room) w *= 0.1;
      r += w * l.color[0];
      g += w * l.color[1];
      b += w * l.color[2];
    }
    const k = (v) => Math.min(1.35, v);
    return { r: k(r), g: k(g), b: k(b), level: (r + g + b) / 3 };
  }

  tintChar(name, light) {
    for (const { mat, base } of this.chars[name] || []) {
      mat.color.setRGB(base.r * light.r, base.g * light.g, base.b * light.b);
    }
  }

  makeBlob(radius, opacity = 0.55) {
    const m = new THREE.Mesh(
      new THREE.PlaneGeometry(radius * 2, radius * 2),
      new THREE.MeshBasicMaterial({ map: this.blobTex, transparent: true, depthWrite: false, opacity }),
    );
    m.rotation.x = -Math.PI / 2;
    m.renderOrder = 2;
    return m;
  }

  textSprite(text, color, size = 0.25) {
    const tex = canvasTex(128, 128, (g) => {
      g.font = 'bold 104px -apple-system, Helvetica, sans-serif';
      g.textAlign = 'center';
      g.textBaseline = 'middle';
      g.lineWidth = 10;
      g.strokeStyle = 'rgba(0,0,0,0.85)';
      g.strokeText(text, 64, 70);
      g.fillStyle = color;
      g.fillText(text, 64, 70);
    });
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
    s.scale.setScalar(size);
    s.renderOrder = 10;
    return s;
  }

  update(t) {
    const flicker = 1 + 0.07 * Math.sin(t * 7.3) + 0.05 * Math.sin(t * 13.7 + 1.3) + 0.04 * Math.sin(t * 23.1);
    for (const g of this.glowMats) g.mat.emissive.copy(g.base).multiplyScalar(flicker);
    this.ovenGlow.material.opacity = 0.45 * flicker;
    this.rippleTex.offset.set(Math.sin(t * 0.3) * 0.05, t * 0.04);
  }
}
