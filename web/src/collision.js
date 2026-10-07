// 2D-Kollision und Sichtlinien in Blender-Koordinaten (x = Ost, y = Nord).

export function makeCollision(layout) {
  const walls = layout.walls;
  const solids = layout.solids;
  const ratRects = [...walls, ...solids.filter(s => s.kind !== 'under').map(s => s.r)];
  const maxRects = [...walls, ...solids.map(s => s.r)];
  const underRects = solids.filter(s => s.kind === 'under').map(s => s.r);

  // Kreis aus Rechtecken schieben. Gibt true zurueck, wenn etwas beruehrt wurde.
  function push(p, r, rects) {
    let hit = false;
    for (let iter = 0; iter < 3; iter++) {
      for (const [x0, y0, x1, y1] of rects) {
        const cx = Math.max(x0, Math.min(p.x, x1));
        const cy = Math.max(y0, Math.min(p.y, y1));
        const dx = p.x - cx, dy = p.y - cy;
        const d2 = dx * dx + dy * dy;
        if (d2 >= r * r) continue;
        hit = true;
        if (d2 > 1e-10) {
          const d = Math.sqrt(d2);
          p.x = cx + (dx / d) * r;
          p.y = cy + (dy / d) * r;
        } else {
          const opts = [[p.x - x0, -1, 0], [x1 - p.x, 1, 0], [p.y - y0, 0, -1], [y1 - p.y, 0, 1]];
          opts.sort((a, b) => a[0] - b[0]);
          const [dist, ox, oy] = opts[0];
          p.x += ox * (dist + r);
          p.y += oy * (dist + r);
        }
      }
    }
    return hit;
  }

  // Strahl a->b gegen Rechteck (Slab-Methode), liefert t in [0,1] oder null
  function segRect(ax, ay, dx, dy, rect, pad) {
    const lo = [rect[0] - pad, rect[1] - pad], hi = [rect[2] + pad, rect[3] + pad];
    const a = [ax, ay], d = [dx, dy];
    let tmin = 0, tmax = 1;
    for (let i = 0; i < 2; i++) {
      if (Math.abs(d[i]) < 1e-9) {
        if (a[i] < lo[i] || a[i] > hi[i]) return null;
      } else {
        let t1 = (lo[i] - a[i]) / d[i], t2 = (hi[i] - a[i]) / d[i];
        if (t1 > t2) [t1, t2] = [t2, t1];
        tmin = Math.max(tmin, t1);
        tmax = Math.min(tmax, t2);
        if (tmin > tmax) return null;
      }
    }
    return tmin;
  }

  function raycast(ax, ay, bx, by, rects = walls, pad = 0) {
    let best = 1;
    const dx = bx - ax, dy = by - ay;
    for (const r of rects) {
      const t = segRect(ax, ay, dx, dy, r, pad);
      if (t !== null && t < best) best = t;
    }
    return best;
  }

  const los = (ax, ay, bx, by) => raycast(ax, ay, bx, by, walls) >= 0.999;
  const inRects = (x, y, rects) => rects.some(([x0, y0, x1, y1]) => x >= x0 && x <= x1 && y >= y0 && y <= y1);

  function roomAt(x, y) {
    for (const [name, [x0, y0, x1, y1]] of Object.entries(layout.rooms)) {
      if (x >= x0 && x <= x1 && y >= y0 && y <= y1) return name;
    }
    return '';
  }

  function inPoly(x, y, poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, yi] = poly[i], [xj, yj] = poly[j];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  return {
    walls, ratRects, maxRects, underRects, push, raycast, los, roomAt, inPoly,
    inUnder: (x, y) => inRects(x, y, underRects),
  };
}
