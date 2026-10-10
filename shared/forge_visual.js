/* Forge's music visualiser styles, shared/forge_visual.js (S25, 2026-09-28).

   Moved out of hub/index.html so the hub's background visualiser, the
   Visualiser widget (shared/forge_widgets.js) and the desktop's visualiser
   (desktop/particles.html) draw with the same code. Adding a style is one
   entry in STYLES plus its label in shared/forge_theme.py VISUALISER_LABELS.

   A style is draw(c, w, h, v, size, accent, accent2, wave, st):
     c, w, h   the 2D context and its size in pixels
     v         levels 0..1, lows first
     size      the size setting (1 = normal)
     wave      512 bytes of waveform (128 = silence); synthWave() makes one
               from levels when only levels are known
     st        this canvas's own state: the styles that remember (orbit,
               terrain) keep it here, and st.anchor(w, h) -> {x, y, r} says
               where ring and orbit centre (the middle when absent)

   ForgeVisual.draw() is the usual way in: it applies the sensitivity
   (gain), the opacity and the colour scheme, then calls the style. */
(() => {
  // the owner (2026-09-28): "the visualiser, at least for terrain, like 20
  // percent more sensitive"
  const TERRAIN_GAIN = 1.2;
  const hexAlpha = (hex, alpha) => {
    const n = parseInt(String(hex).slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  };
  const mixHex = (a, b, t) => {
    const x = parseInt(String(a).slice(1), 16), y = parseInt(String(b).slice(1), 16);
    const m = (sh) => Math.round(((x >> sh) & 255) + (((y >> sh) & 255) - ((x >> sh) & 255)) * t);
    return "#" + ((m(16) << 16) | (m(8) << 8) | m(0)).toString(16).padStart(6, "0");
  };
  const anchorOf = (st, w, h) => (st && st.anchor ? st.anchor(w, h) : { x: w / 2, y: h / 2, r: Math.min(w, h) * 0.18 });
  const hslHex = (hue, s, l) => {
    const k = (n) => (n + hue / 30) % 12, a = s * Math.min(l, 1 - l);
    const f = (n) => Math.round(255 * (l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)))));
    return "#" + [f(0), f(8), f(4)].map((x) => x.toString(16).padStart(2, "0")).join("");
  };
  const SCHEMES = {
    neon: ["#ff4fd8", "#4ff0ff"],
    fire: ["#ff6a1a", "#ffd166"],
    ice: ["#7ad7ff", "#e0f7ff"],
    white: ["#f4f7ff", "#c9d6ff"],
  };

  // One soft glow, white fading out, recoloured and kept per colour: the
  // glowing styles stamp it instead of building a radial gradient per light
  // (v2, 2026-10-07: orbit built about 110 gradients a frame).
  const sprites = new Map();
  const glow = (col) => {
    let s = sprites.get(col);
    if (s) return s;
    s = document.createElement("canvas");
    s.width = s.height = 64;
    const g = s.getContext("2d"), grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0, "rgba(255,255,255,1)");
    grad.addColorStop(0.35, "rgba(255,255,255,.55)");
    grad.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, 64, 64);
    g.globalCompositeOperation = "source-in";
    g.fillStyle = col;
    g.fillRect(0, 0, 64, 64);
    if (sprites.size > 48) sprites.delete(sprites.keys().next().value); // rainbow walks through colours
    sprites.set(col, s);
    return s;
  };
  // Batched drawing: shapes are grouped by colour and one of ALPHAS steps of
  // opacity, and each group is one path and one fill or stroke instead of a
  // state change and a draw call per bar.
  const ALPHAS = 6;
  const step = (a) => Math.max(0, Math.min(ALPHAS - 1, Math.round(a * (ALPHAS - 1))));
  const groups = (n) => Array.from({ length: n }, () => []);
  const strokeGroups = (c, gs, colour) => gs.forEach((segs, k) => {
    if (!segs.length) return;
    c.strokeStyle = colour; c.globalAlpha = k / (ALPHAS - 1);
    c.beginPath();
    for (let i = 0; i < segs.length; i += 4) { c.moveTo(segs[i], segs[i + 1]); c.lineTo(segs[i + 2], segs[i + 3]); }
    c.stroke();
  });
  const fillGroups = (c, gs, colour) => gs.forEach((rects, k) => {
    if (!rects.length) return;
    c.fillStyle = colour; c.globalAlpha = k / (ALPHAS - 1);
    c.beginPath();
    for (let i = 0; i < rects.length; i += 4) c.rect(rects[i], rects[i + 1], rects[i + 2], rects[i + 3]);
    c.fill();
  });
  // a peak that holds for a moment and then falls, per bar
  const peaksOf = (st, n) => {
    const now = performance.now() / 1000, p = st.peaks && st.peaks.v.length === n ? st.peaks : (st.peaks = { v: new Float32Array(n), hold: new Float32Array(n), t: now });
    p.dt = Math.min(0.1, now - p.t); p.t = now;
    return p;
  };
  const peak = (p, i, a) => {
    if (a >= p.v[i]) { p.v[i] = a; p.hold[i] = 0.35; }
    else if ((p.hold[i] -= p.dt) < 0) p.v[i] = Math.max(a, p.v[i] - p.dt * 0.9);
    return p.v[i];
  };

  const STYLES = {
    // v2: one gradient and one path for every bar, so a louder bar is also
    // a brighter one, and a cap on each that holds the last peak
    bars(c, w, h, v, size, accent, accent2, wave, st = {}) {
      // narrow cards: the gap shrinks before a bar can (a negative width threw)
      const n = 64, gap = Math.min(3, (w * 0.7) / n / 3), bw = Math.max(1, (w * 0.7) / n - gap), x0 = w * 0.15, base = h - 24, tall = 4 + 140 * size;
      const p = peaksOf(st, n), caps = [];
      const g = c.createLinearGradient(0, base - tall, 0, base);
      g.addColorStop(0, hexAlpha(accent2, 0.95)); g.addColorStop(0.5, hexAlpha(accent2, 0.6)); g.addColorStop(1, hexAlpha(accent, 0.25));
      c.fillStyle = g;
      c.beginPath();
      for (let i = 0; i < n; i++) {
        const a = v[Math.floor((i / n) * v.length)] || 0, bh = 4 + a * 140 * size, x = x0 + i * (bw + gap);
        if (c.roundRect) c.roundRect(x, base - bh, bw, bh, Math.max(0, Math.min(3, bw / 2))); else c.rect(x, base - bh, bw, bh);
        const top = peak(p, i, a);
        if (top > 0.04) caps.push(x, base - 4 - top * 140 * size - 4, bw, 2);
      }
      c.fill();
      const cg = groups(ALPHAS);
      for (let i = 0; i < caps.length; i += 4) cg[step(0.7)].push(caps[i], caps[i + 1], caps[i + 2], caps[i + 3]);
      fillGroups(c, cg, accent2);
    },
    // around the clock that shows (home or zen); the middle of an empty
    // screen in background mode. The middle of home is under the tiles,
    // which is why the ring used to seem not to show at all. v2: a glow
    // breathing with the bass behind it, and the spokes batched.
    ring(c, w, h, v, size, accent, accent2, wave, st = {}) {
      const at = anchorOf(st, w, h), cx = at.x, cy = at.y, r0 = at.r * size, n = 96;
      const bass = ((v[0] || 0) + (v[1] || 0) + (v[2] || 0)) / 3;
      c.globalCompositeOperation = "lighter";
      const gr = r0 * (1.2 + bass * 0.5);
      c.globalAlpha = 0.16 + bass * 0.5;
      c.drawImage(glow(accent), cx - gr, cy - gr, gr * 2, gr * 2);
      c.globalCompositeOperation = "source-over";
      c.lineWidth = 3;
      c.lineCap = "round";
      const odd = groups(ALPHAS), even = groups(ALPHAS);
      for (let i = 0; i < n; i++) {
        const a = v[Math.floor((i < n / 2 ? i : n - i) / (n / 2) * (v.length - 1))] || 0;
        const ang = (i / n) * Math.PI * 2 - Math.PI / 2, len = 6 + a * 90 * size, cos = Math.cos(ang), sin = Math.sin(ang);
        (i % 2 ? odd : even)[step(0.35 + a * 0.6)].push(cx + cos * r0, cy + sin * r0, cx + cos * (r0 + len), cy + sin * (r0 + len));
      }
      strokeGroups(c, odd, accent);
      strokeGroups(c, even, accent2);
    },
    // a smooth line with a soft glow under it and a faint fill below
    wave(c, w, h, v, size, accent, accent2, wave, st = {}) {
      wave = wave || new Uint8Array(512).fill(128);
      const mid = h * 0.78, pts = [];
      for (let i = 0; i <= wave.length; i += 8) {
        const k = Math.min(i, wave.length - 1);
        pts.push([(i / wave.length) * w, mid + ((wave[k] - 128) / 128) * 120 * size]);
      }
      const path = (dy) => {
        c.beginPath();
        c.moveTo(pts[0][0], pts[0][1] + dy);
        for (let i = 1; i < pts.length - 1; i++) {
          const mx = (pts[i][0] + pts[i + 1][0]) / 2, my = (pts[i][1] + pts[i + 1][1]) / 2 + dy;
          c.quadraticCurveTo(pts[i][0], pts[i][1] + dy, mx, my);
        }
        c.lineTo(pts[pts.length - 1][0], pts[pts.length - 1][1] + dy);
      };
      path(0);
      c.lineTo(w, h); c.lineTo(0, h); c.closePath();
      const g = c.createLinearGradient(0, mid - 120 * size, 0, h);
      g.addColorStop(0, hexAlpha(accent, 0.22)); g.addColorStop(1, hexAlpha(accent, 0));
      c.fillStyle = g;
      c.fill();
      c.lineCap = c.lineJoin = "round";
      for (const [col, width, alpha, dy] of [[accent, 12, 0.12, 0], [accent2, 1.5, 0.45, 10], [accent, 3, 0.9, 0]]) {
        path(dy);
        c.strokeStyle = hexAlpha(col, alpha);
        c.lineWidth = width;
        c.stroke();
      }
    },
    // bars mirrored up and down from a line across the lower screen
    mirror(c, w, h, v, size, accent, accent2, wave, st = {}) {
      const n = 48, gap = Math.min(4, (w * 0.8) / n / 3), bw = Math.max(1, (w * 0.8) / n - gap), x0 = w * 0.1, mid = h * 0.8, up = groups(ALPHAS), down = groups(ALPHAS);
      for (let i = 0; i < n; i++) {
        const a = v[Math.floor(((i < n / 2 ? n / 2 - i : i - n / 2) / (n / 2)) * (v.length - 1))] || 0;
        const tall = 3 + a * 110 * size, x = x0 + i * (bw + gap);
        up[step(0.5 + a * 0.45)].push(x, mid - tall, bw, tall);
        down[step(0.18 + a * 0.2)].push(x, mid + 2, bw, tall * 0.55);
      }
      fillGroups(c, up, accent2);
      fillGroups(c, down, accent);
    },
    // lights circling the clock (the middle in background mode), each ring
    // swinging out with its part of the music; a beat sends a ring outwards
    orbit(c, w, h, v, size, accent, accent2, wave, st = {}) {
      const at = anchorOf(st, w, h), t = performance.now() / 1000, o = st.orb || (st.orb = { avg: 0, waves: [], last: t });
      const dt = Math.min(0.1, t - o.last); o.last = t;
      const bass = (v[0] + v[1] + v[2]) / 3 || 0;
      if (bass > 0.18 && bass > o.avg * 1.35 && !(o.waves.length && o.waves[o.waves.length - 1].age < 0.12)) o.waves.push({ age: 0, a: Math.min(1, bass * 1.3) });
      o.avg += (bass - o.avg) * 0.06;
      c.globalCompositeOperation = "lighter";
      const rings = 5, glow1 = glow(accent), glow2 = glow(accent2), alpha = c.globalAlpha;
      for (let j = 0; j < rings; j++) {
        const lv = v[Math.floor((j / rings) * (v.length - 1))] || 0;
        const n = 10 + j * 6, base = at.r * (0.8 + j * 0.34) * size, dir = j % 2 ? -1 : 1;
        for (let i = 0; i < n; i++) {
          const ang = (i / n) * Math.PI * 2 + t * (0.25 + j * 0.07) * dir;
          const r = base + lv * 70 * size * (0.6 + 0.4 * Math.sin(ang * 3 + t * 2));
          const x = at.x + Math.cos(ang) * r, y = at.y + Math.sin(ang) * r * 0.92;
          const rad = 1.6 + lv * 5 * size;
          c.globalAlpha = 0.5 + lv * 0.5;
          c.drawImage(j % 2 ? glow2 : glow1, x - rad * 4, y - rad * 4, rad * 8, rad * 8);
        }
      }
      c.globalAlpha = alpha;
      o.waves = o.waves.filter((wv) => (wv.age += dt) < 1.2);
      for (const wv of o.waves) {
        const p = wv.age / 1.2;
        c.strokeStyle = hexAlpha(accent2, wv.a * (1 - p) * 0.8);
        c.lineWidth = 2 + 8 * (1 - p);
        c.beginPath(); c.arc(at.x, at.y, at.r * size * (0.7 + p * 3.2), 0, Math.PI * 2); c.stroke();
      }
      c.globalCompositeOperation = "source-over";
    },
    // a glowing wireframe landscape, its hills the music (lows by the middle
    // valley, highs out at the edges): the newest sound rises right in front
    // and rolls away to the horizon
    terrain(c, w, h, v, size, accent, accent2, wave, st = {}) {
      const t = performance.now() / 1000, horizon = h * 0.56, rows = 26, cols = 44;
      const scroll = (t * 3) % 1;
      const hist = st.hills || (st.hills = []);
      const row = new Float32Array(cols + 1);
      for (let i = 0; i <= cols; i++) {
        const side = Math.abs(i / cols - 0.5) * 2; // 0 in the middle, 1 at the edges
        const lv = (v[Math.floor(side * (v.length - 1) * 0.8)] || 0) * TERRAIN_GAIN;
        row[i] = lv * side ** 0.7;
      }
      // the front row is always the sound right now; each time the rows move
      // back one step it is kept, so the ones behind are the moments before
      if (!hist.length || scroll < (st.hillsPrev ?? 1)) hist.unshift(row);
      st.hillsPrev = scroll;
      hist.length = Math.min(hist.length, rows + 1);
      const at = (r) => (r === 0 ? row : hist[Math.min(hist.length - 1, r - 1)]);
      const sun = c.createLinearGradient(0, horizon - h * 0.22, 0, horizon);
      sun.addColorStop(0, hexAlpha(accent2, 0.55)); sun.addColorStop(1, hexAlpha(accent, 0.05));
      c.fillStyle = sun;
      c.beginPath(); c.arc(w / 2, horizon, h * 0.2 * (0.9 + (v[0] || 0) * 0.25 * size), Math.PI, 0); c.fill();
      const pt = (i, z, lift) => {
        const d = 1 / (1 + z * 8); // z 0 at the bottom edge .. 1 at the horizon
        return [w / 2 + (i / cols - 0.5) * w * 3 * d, horizon + (h - horizon) * d - lift * 180 * size * d];
      };
      c.lineWidth = 1.2;
      for (let r = rows - 1; r >= 0; r--) {
        const z = (r + scroll) / rows;
        const hills = at(r);
        const near = 1 - z;
        c.strokeStyle = hexAlpha(r % 2 ? accent : accent2, 0.12 + near * 0.75);
        c.beginPath();
        for (let i = 0; i <= cols; i++) {
          const [x, y] = pt(i, z, hills[i]);
          if (i) c.lineTo(x, y); else c.moveTo(x, y);
        }
        c.stroke();
      }
      c.strokeStyle = hexAlpha(accent, 0.25);
      c.beginPath();
      for (let i = 0; i <= cols; i += 2) {
        for (let r = 0; r < rows; r++) {
          const z = (r + scroll) / rows, hills = at(r);
          const [x, y] = pt(i, z, hills[i]);
          if (r) c.lineTo(x, y); else c.moveTo(x, y);
        }
      }
      c.stroke();
    },
    // slow translucent bands across the top, each band as loud as its part
    aurora(c, w, h, v, size, accent, accent2, wave, st = {}) {
      const t = performance.now() / 1000, bands = [[accent, 0.18, 0], [accent2, 0.14, 2.1], [accent, 0.1, 4.2]];
      bands.forEach(([col, alpha, ph], j) => {
        const lv = v[Math.floor(((j + 1) / 4) * (v.length - 1))] || 0, base = h * (0.18 + j * 0.07);
        c.beginPath();
        c.moveTo(0, 0);
        for (let x = 0; x < w + 24; x += 24) {
          const y = base + Math.sin(x / (220 + j * 60) + t * (0.3 + j * 0.1) + ph) * (40 + lv * 160 * size);
          c.lineTo(x, y);
        }
        c.lineTo(w, 0);
        c.closePath();
        const g = c.createLinearGradient(0, 0, 0, base + 200);
        g.addColorStop(0, hexAlpha(col, 0));
        g.addColorStop(1, hexAlpha(col, alpha + lv * 0.25));
        c.fillStyle = g;
        c.fill();
      });
    },
    // v2 (2026-10-07): a smooth spectrum, mirrored round a line low on the
    // screen, lows in the middle; two fills and one stroke
    spectrum(c, w, h, v, size, accent, accent2, wave, st = {}) {
      const n = 40, mid = h * 0.78, x0 = w * 0.06, span = w * 0.88, ys = st.spec && st.spec.length === n + 1 ? st.spec : (st.spec = new Float32Array(n + 1));
      for (let i = 0; i <= n; i++) {
        const side = Math.abs(i / n - 0.5) * 2;
        // tapered to nothing at both ends, so the outline starts and ends on the line
        ys[i] = ((v[Math.floor(side * (v.length - 1))] || 0) * 120 * size * (1 - side * 0.35) + 2) * Math.sin((i / n) * Math.PI) ** 0.7;
      }
      const curve = (dir) => {
        c.moveTo(x0, mid);
        for (let i = 0; i < n; i++) {
          const xa = x0 + (i / n) * span, xb = x0 + ((i + 1) / n) * span;
          c.quadraticCurveTo(xa, mid - dir * ys[i], (xa + xb) / 2, mid - dir * (ys[i] + ys[i + 1]) / 2);
        }
        c.lineTo(x0 + span, mid);
      };
      const g = c.createLinearGradient(x0, 0, x0 + span, 0);
      g.addColorStop(0, hexAlpha(accent, 0.05)); g.addColorStop(0.5, hexAlpha(accent2, 0.6)); g.addColorStop(1, hexAlpha(accent, 0.05));
      c.fillStyle = g;
      c.beginPath(); curve(1); c.closePath(); c.fill();
      // the reflection fades out downwards
      const top = Math.max(4, ...ys), r = c.createLinearGradient(0, mid, 0, mid + top * 0.45);
      r.addColorStop(0, hexAlpha(accent2, 0.28)); r.addColorStop(1, hexAlpha(accent2, 0));
      c.fillStyle = r;
      c.beginPath(); curve(-0.45); c.closePath(); c.fill();
      c.strokeStyle = hexAlpha(accent2, 0.9);
      c.lineWidth = 2;
      c.lineJoin = "round";
      c.beginPath(); curve(1); c.stroke();
    },
    // v2 (2026-10-07): soft clouds of light round the clock (the middle in
    // background mode), one per band, swelling with it and drifting slowly
    nebula(c, w, h, v, size, accent, accent2, wave, st = {}) {
      // round the anchor on a page; spread over the whole card or empty screen
      const at = anchorOf(st, w, h), t = performance.now() / 1000, n = 18, free = !(st && st.anchor);
      const g1 = glow(mixHex(accent, "#ffffff", 0.3)), g2 = glow(mixHex(accent2, "#ffffff", 0.3));
      const white = glow("#ffffff"), base = c.globalAlpha;
      c.globalCompositeOperation = "lighter";
      for (let i = 0; i < n; i++) {
        const lv = v[Math.floor((i / n) * (v.length - 1))] || 0, ang = (i / n) * Math.PI * 2 + t * (0.05 + (i % 3) * 0.03);
        const swell = 0.75 + 0.25 * Math.sin(t * 0.21 + i * 1.7) + lv * 0.25;
        const rx = free ? w * 0.36 : at.r * 1.5 * size * Math.max(1.25, Math.min(3, (w / h) * 0.9));
        const ry = free ? h * 0.3 : at.r * 1.2 * size;
        const x = at.x + Math.cos(ang) * rx * swell, y = at.y + Math.sin(ang) * ry * swell;
        const r = (22 + lv * 80) * (free ? Math.max(0.35, Math.min(1.2, h / 500)) : size);
        c.globalAlpha = base * (0.24 + lv * 0.6);
        c.drawImage(i % 2 ? g2 : g1, x - r, y - r, r * 2, r * 2);
        if (lv > 0.2) { const k = r * 0.25; c.globalAlpha = base * Math.min(1, (lv - 0.2) * 1.4); c.drawImage(white, x - k, y - k, k * 2, k * 2); }
      }
      c.globalAlpha = base;
      c.globalCompositeOperation = "source-over";
    },
  };

  const V = {
    STYLES,
    glow,
    TERRAIN_GAIN,
    hexAlpha,
    names() { return Object.keys(STYLES); },
    /** Eases `shown` (a Float32Array, changed in place) toward the levels
        `v`, `dt` seconds after the last call (task 56). A rise lands within
        a frame and a fall eases out over about a sixth of a second, whatever
        the frame rate; the old fixed 35% a frame held every beat back by two
        or three frames. Two calls in one frame (dt 0) change nothing. */
    follow(shown, v, dt) {
      const up = 1 - Math.exp(-dt / 0.012), down = 1 - Math.exp(-dt / 0.16);
      for (let i = 0; i < shown.length; i++) {
        const d = (v[i] || 0) - shown[i];
        shown[i] += d * (d > 0 ? up : down);
      }
      return shown;
    },
    /** True when a style draws the same picture frame after frame in
        silence, so a page may redraw it only now and then; orbit, terrain,
        aurora and the rainbow colours move with time. */
    stillInSilence(style, scheme) {
      return scheme !== "rainbow" && ["bars", "ring", "wave", "mirror", "spectrum"].includes(style);
    },
    // [accent, accent2] for a colour scheme; rainbow turns slowly with time
    colours(scheme, accent, accent2, t = performance.now() / 1000) {
      if (scheme === "rainbow") { const h = Math.round((t * 12) % 360 / 3) * 3; return [hslHex(h, 0.85, 0.62), hslHex((h + 60) % 360, 0.85, 0.7)]; }
      return SCHEMES[scheme] || [accent || "#8ab4ff", accent2 || accent || "#8ab4ff"];
    },
    // the wave style from bar levels: a few slow sines, each as loud as its band
    synthWave(v, t, buf) {
      const n = buf.length, k = Math.min(8, v.length), step = v.length / k;
      for (let i = 0; i < n; i++) {
        let y = 0;
        for (let j = 0; j < k; j++) y += (v[Math.floor(j * step)] * Math.sin((i / n) * Math.PI * 2 * (j + 1) + t * (1.2 + j * 0.8))) / (1 + j * 0.5);
        buf[i] = 128 + Math.max(-127, Math.min(127, y * 70));
      }
      return buf;
    },
    // o: {size, gain, opacity, scheme, accent, accent2, wave, st}
    draw(c, w, h, style, v, o = {}) {
      // an unknown style name (an older setting) draws bars, not nothing
      const fn = STYLES[style] || (style && style !== "off" ? STYLES.bars : null);
      if (!fn || !v) return false;
      const st = o.st || {};
      const gain = Number(o.gain ?? 1);
      let lv = v;
      if (gain !== 1) {
        if (!st.gained || st.gained.length !== v.length) st.gained = new Float32Array(v.length);
        for (let i = 0; i < v.length; i++) st.gained[i] = Math.min(1.6, v[i] * gain);
        lv = st.gained;
      }
      const wave = o.wave || st.wave || (st.wave = new Uint8Array(512).fill(128));
      const [a, a2] = this.colours(o.scheme, o.accent, o.accent2);
      c.save();
      c.globalAlpha = Math.max(0.05, Math.min(1, Number(o.opacity ?? 1)));
      fn(c, w, h, lv, Number(o.size || 1), a, a2, wave, st);
      c.restore();
      return true;
    },
  };
  window.ForgeVisual = V;
})();
