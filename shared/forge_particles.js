/* Forge particles, shared by the hub's home screen (hub/index.html) and the
   desktop particles window (desktop/particles.html). Needs a
   <canvas id="particles"> on the page; call Particles.init(), then
   Particles.apply() whenever the settings change. A host page can replace
   look(), showing() and boost() below.

   Lights and weather over the wallpaper, drawn only while the host says they
   are showing and the window is visible. Every kind is one entry in KINDS: how
   many it draws, how one is born (spawn) and how the lot is drawn (draw).
   Adding a kind is one entry here plus its label in forge_theme.py.
     soft           a few dozen slow glowing motes in the accent colour
     lively         more of them, quicker
     embers         forge sparks rising and flickering, orange, gold and accent
     constellation  drifting stars joined by faint lines, and to the pointer
     petals         blossom petals tumbling down on a breeze
     rain           fine rain slanting down
     snow           snowflakes drifting down
     rays           soft beams of light with dust turning in them
     fireflies      wandering lights that blink, drawn to the pointer
     dust           warm specks floating sideways
     glitter        tiny four-point sparkles that flash as they fall
     lanterns       warm paper lanterns rising slowly and swaying
     wisps          spirit lights wandering on a slow current, trailing light
     galaxy         a slowly turning spiral of stars round a bright core
   A wallpaper's own pick (Wallpapers app, theme.wallpaper_particles) wins
   over the setting unless the setting is Off. On a bright picture the glows
   are drawn over the picture instead of added to it, so they still show.
   "auto" takes the kind forge_theme.py picked for the wallpaper (its name,
   then its colours), and the Particle amount setting scales every count.
   The pointer pushes them a little, and a press on the home screen throws a
   burst of sparks (burst()), which keeps drawing until they burn out even if
   the press opened an app. Each glow is one small pre-drawn sprite, stamped,
   so a frame costs a few hundred drawImage calls and no blur.

   v2 (2026-10-07, the owner: "desktop particles ... v2"):
   - Motion is per second, not per frame: at a 30 or 20 frame cap they move
     as fast as at 60 (they used to crawl at half speed, board note #931).
   - Depth: most kinds are born at a depth (d.z); nearer ones are bigger,
     quicker and brighter, and slide further when the pointer moves
     (parallax), so the layer reads as 3D.
   - Cheaper frames: shapes that were a path each (rain, bubbles, glitter,
     constellation lines) are batched into a few paths by opacity step;
     petals and lanterns are pre-drawn sprites, not a path and a gradient
     each; no save()/restore() per particle.
   - It stops completely while the page is hidden (and, inside the hub,
     while ForgeIdle says it cannot be seen), and comes back without a jump.
   - The shared power tier (theme.power_tier from ForgePower.apply()): full
     as set; reduced: the eased amounts and rates in the theme, and never
     above 30 frames a second; paused: nothing is drawn. Particles.apply()
     after a theme change (or a "forge-power" event) picks it up. */

const Particles = {
  // What the page hosting it supplies: its settings (window.FORGE_THEME
  // unless told otherwise), whether the particles are on show right now
  // (the hub's home screen), and how many more to draw than usual.
  look: () => window.FORGE_THEME || {},
  showing: () => true,
  boost: () => 1,
  canvas: null, ctx: null, dots: [], sparks: [], raf: 0, level: "off", built: "", sprites: {},
  mouse: { x: -1, y: -1 },
  // k: this frame's length in 60ths of a second; ox/oy: the parallax shift
  k: 1, last: 0, ox: 0, oy: 0,
  EMBER: ["#ff8a3d", "#ffc56b"],
  PETAL: ["#ffc6d9", "#ffb0c8", "#ffe3ec", "#f7a1bd"],
  KINDS: {
    soft: { count: 45, spawn: "spawnMote", draw: "drawMotes" },
    lively: { count: 110, spawn: "spawnMote", draw: "drawMotes" },
    embers: { count: 90, spawn: "spawnEmber", draw: "drawEmbers" },
    constellation: { count: 95, spawn: "spawnStar", draw: "drawStars", flat: true },
    petals: { count: 55, spawn: "spawnPetal", draw: "drawPetals" },
    rain: { count: 170, spawn: "spawnRain", draw: "drawRain" },
    snow: { count: 130, spawn: "spawnSnow", draw: "drawSnow", flat: true },
    rays: { count: 40, spawn: "spawnMote", draw: "drawRays" },
    fireflies: { count: 40, spawn: "spawnFly", draw: "drawFlies" },
    dust: { count: 80, spawn: "spawnDust", draw: "drawDust" },
    leaves: { count: 45, spawn: "spawnPetal", draw: "drawPetals", palette: ["#d9822b", "#c2571a", "#e8b04a", "#8f4a1c"], size: 1.6 },
    petalstorm: { count: 170, spawn: "spawnPetal", draw: "drawPetals", gust: 2.2 },
    bubbles: { count: 50, spawn: "spawnBubble", draw: "drawBubbles" },
    stars: { count: 160, spawn: "spawnTwinkle", draw: "drawTwinkle", flat: true },
    emberstorm: { count: 200, spawn: "spawnEmber", draw: "drawEmbers" },
    glitter: { count: 90, spawn: "spawnGlitter", draw: "drawGlitter" },
    lanterns: { count: 26, spawn: "spawnLantern", draw: "drawLanterns", flat: true },
    wisps: { count: 34, spawn: "spawnWisp", draw: "drawWisps" },
    galaxy: { count: 320, spawn: "spawnGalaxy", draw: "drawGalaxy", flat: true },
  },
  // Alt+V's beat (the hub's Pulse): every particle moves further for a moment
  surge: 0,
  kick(k) { this.surge = Math.max(this.surge, Math.min(1.5, k)); },
  init() {
    this.canvas = document.getElementById("particles");
    this.ctx = this.canvas.getContext("2d");
    addEventListener("resize", () => this.size());
    addEventListener("pointermove", (e) => { this.mouse.x = e.clientX; this.mouse.y = e.clientY; });
    document.addEventListener("pointerleave", () => { this.mouse.x = -1; });
    document.addEventListener("visibilitychange", () => this.sync());
    // inside the hub: another app or the home screen in front (forge_idle.js)
    document.addEventListener("forge-visible", () => this.sync());
    // the shared power tier (shared/forge_power.js): reduced draws fewer at a
    // lower rate, paused draws nothing until it changes back
    document.addEventListener("forge-power", () => this.apply());
    this.size();
  },
  size() {
    if (!this.canvas) return;
    if (this.canvas.width !== innerWidth) this.canvas.width = innerWidth;
    if (this.canvas.height !== innerHeight) this.canvas.height = innerHeight;
  },
  // The kind to draw: the wallpaper's own pick, else the setting, and for
  // "auto" the kind that suits the wallpaper. Only an Off stops them.
  chosen() {
    // Alt+P and Alt+E turn them off with particles_shown; the kind stays picked
    if (this.look().animations === false || this.look().particles_shown === false) return "off";
    let kind = this.look().particles || "soft";
    const own = this.look().wallpaper_particles;
    if (kind !== "off" && own && (own === "off" || own in this.KINDS)) kind = own;
    if (kind === "auto") kind = this.look().ambience || "soft";
    if (kind === "off") return "off";
    return kind in this.KINDS ? kind : "soft";
  },
  apply() {
    this.level = this.chosen();
    this.bright = Number(this.look().wall_light) > 0.6;
    document.body.classList.toggle("no-particles", this.level === "off");
    const { size, speed } = this.scale(), build = `${this.level} ${size} ${speed}`;
    if (this.built !== build) { this.dots = []; this.built = build; }
    const boost = this.boost();
    // a broken setting (text, NaN, huge) is read as 1, and never above 5
    const raw = Number(this.look().particle_amount ?? 1), amount = Math.min(5, Number.isFinite(raw) ? Math.max(0, raw) : 1) * (Number(boost) || 1);
    const want = this.level === "off" ? 0 : Math.round(this.KINDS[this.level].count * amount);
    while (this.dots.length < want) this.dots.push(this.spawn(true));
    this.dots.length = want;
    this.sync();
  },
  mix(a, b, t) {
    const x = parseInt(String(a).slice(1), 16), y = parseInt(String(b).slice(1), 16);
    const ch = (n, sh) => (n >> sh) & 255;
    const m = (sh) => Math.round(ch(x, sh) + (ch(y, sh) - ch(x, sh)) * t);
    return "#" + ((m(16) << 16) | (m(8) << 8) | m(0)).toString(16).padStart(6, "0");
  },
  hexAlpha(hex, alpha) {
    const n = parseInt(String(hex).slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  },
  // One glow, white fading out, recoloured: stamped at any size and colour.
  sprite(color) {
    if (this.sprites[color]) return this.sprites[color];
    const c = document.createElement("canvas");
    c.width = c.height = 64;
    const g = c.getContext("2d");
    const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0, "rgba(255,255,255,1)");
    grad.addColorStop(0.18, "rgba(255,255,255,.75)");
    grad.addColorStop(0.45, "rgba(255,255,255,.18)");
    grad.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, 64, 64);
    g.globalCompositeOperation = "source-in";
    g.fillStyle = color;
    g.fillRect(0, 0, 64, 64);
    return (this.sprites[color] = c);
  },
  // A pre-drawn shape (petal, lantern), kept by name: drawn once, stamped after.
  shape(key, w, h, paint) {
    const name = `shape:${key}`;
    if (this.sprites[name]) return this.sprites[name];
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    paint(c.getContext("2d"), w, h);
    return (this.sprites[name] = c);
  },
  spawn(anywhere) {
    const kind = this.KINDS[this.level];
    const d = this[kind.spawn](anywhere, innerWidth, innerHeight, Math.random);
    // depth: near ones bigger, quicker and brighter; flat kinds have their own
    if (d.z == null) {
      d.z = kind.flat ? 1 : 0.55 + Math.random() * 0.75;
      if (!kind.flat) { d.r *= d.z ** 0.8; d.vx *= d.z; d.vy *= d.z; d.a = Math.min(1, d.a * (0.75 + 0.25 * d.z)); }
    }
    // Home screen > Particle size and Particle speed scale every kind
    const { size, speed } = this.scale();
    if (size !== 1) d.r *= size;
    if (speed !== 1) { d.vx *= speed; d.vy *= speed; d.sp = (d.sp || 1) * speed; }
    return d;
  },
  scale() {
    const n = (v) => (Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : 1);
    return { size: n(this.look().particle_size), speed: n(this.look().particle_speed) };
  },
  spawnMote(anywhere, w, h, r) {
    const y = anywhere ? r() * h : h + 10;
    const speed = this.level === "lively" ? 0.35 : 0.14;
    return { x: r() * w, y, r: 1 + r() * 2.2, vx: (r() - 0.5) * speed, vy: -(0.2 + r()) * speed,
      a: 0.25 + r() * 0.5, tw: r() * 6.28 };
  },
  spawnEmber(anywhere, w, h, r) {
    const y = anywhere ? r() * h : h + 10;
    return { x: r() * w, y, r: 1.4 + r() * 3, vx: (r() - 0.5) * 0.4, vy: -(0.35 + r() * 1.1),
      a: 0.55 + r() * 0.45, tw: r() * 6.28, life: 0.6 + r() * 0.4, c: r() < 0.2 ? -1 : r() < 0.6 ? 0 : 1 };
  },
  spawnStar(anywhere, w, h, r) {
    return { x: r() * w, y: r() * h, r: 1.2 + r() * 2, vx: (r() - 0.5) * 0.3, vy: (r() - 0.5) * 0.3,
      a: 0.35 + r() * 0.5, tw: r() * 6.28 };
  },
  spawnPetal(anywhere, w, h, r) {
    const k = this.KINDS[this.level] || {}, size = k.size || 1, gust = k.gust || 1;
    return { x: r() * (w + 200) - 100, y: anywhere ? r() * h : -20, r: (5 + r() * 5) * size, vx: (0.3 + r() * 0.5) * gust, vy: (0.5 + r() * 0.7) * (1 + (gust - 1) * 0.4),
      a: 0.6 + r() * 0.35, tw: r() * 6.28, spin: (r() - 0.5) * 0.05, rot: r() * 6.28, c: Math.floor(r() * 4) };
  },
  spawnRain(anywhere, w, h, r) {
    return { x: r() * (w + 300), y: anywhere ? r() * h : -30, r: 12 + r() * 18, vx: -2.2, vy: 13 + r() * 6, a: 0.12 + r() * 0.2, tw: 0 };
  },
  spawnSnow(anywhere, w, h, r) {
    const depth = 0.4 + r() * 0.8;
    return { x: r() * w, y: anywhere ? r() * h : -10, r: 1 + depth * 2.4, vx: 0, vy: 0.4 + depth * 0.9,
      a: 0.4 + depth * 0.45, tw: r() * 6.28, z: depth };
  },
  spawnFly(anywhere, w, h, r) {
    return { x: r() * w, y: h * 0.3 + r() * h * 0.7, r: 1.4 + r() * 1.6, vx: (r() - 0.5) * 0.6, vy: (r() - 0.5) * 0.6,
      a: 0.5 + r() * 0.5, tw: r() * 6.28 };
  },
  spawnDust(anywhere, w, h, r) {
    return { x: anywhere ? r() * w : -10, y: r() * h, r: 0.6 + r() * 1.4, vx: 0.15 + r() * 0.35, vy: (r() - 0.5) * 0.12,
      a: 0.25 + r() * 0.45, tw: r() * 6.28 };
  },
  burst(x, y, color) {
    if (this.level === "off") return;
    const tint = color || this.look().accent || "#8ab4ff";
    for (let i = 0; i < 34; i++) {
      const ang = Math.random() * Math.PI * 2, sp = 1.5 + Math.random() * 5;
      this.sparks.push({ x, y, vx: Math.cos(ang) * sp, vy: Math.sin(ang) * sp - 1.4, life: 1,
        r: 1.5 + Math.random() * 2.5, color: Math.random() < 0.35 ? this.EMBER[i % 2] : tint });
    }
    this.sync();
  },
  // full, reduced or paused: forge_power.js's tier(), which the host's
  // ForgePower.apply() puts in the theme as power_tier; full when absent.
  // The reduced amounts and rates are already in that theme (eased()), so
  // here reduced only adds a 30 fps ceiling, and paused draws nothing.
  tier() { const t = String(this.look().power_tier || "").split(":")[0].trim(); return t === "reduced" || t === "paused" ? t : "full"; },
  REDUCED: { fps: 30 },
  // seen: the page is visible, and inside the hub nothing is in front of it
  seen() { return window.ForgeIdle ? ForgeIdle.shown() : !document.hidden; },
  // running only when there is something to see keeps an idle Forge idle
  sync() {
    // nothing to draw (amount 0) asks for no frames either
    const run = this.level !== "off" && this.seen() && this.tier() !== "paused" && (this.dots.length > 0 || this.sparks.length > 0) && (this.showing() || this.sparks.length > 0);
    // readable from a DOM dump, which is how the browser checks see a canvas
    this.canvas.dataset.dots = String(this.dots.length);
    this.canvas.dataset.kind = this.level;
    this.canvas.dataset.running = run ? "1" : "0";
    if (run && !this.raf) { this.last = 0; this.raf = requestAnimationFrame((t) => this.frame(t)); }
    if (!run && this.raf) { cancelAnimationFrame(this.raf); this.raf = 0; }
  },
  push(d, m, reach, force) {
    if (m.x < 0) return;
    const dx = d.x - m.x, dy = d.y - m.y, d2 = dx * dx + dy * dy;
    if (d2 < reach * reach && d2 > 0.01) { const dist = Math.sqrt(d2), f = force * this.k / dist; d.vx += dx * f; d.vy += dy * f; }
  },
  // per-frame factor for a damping written as "per 60th of a second"
  damp(f) { return this.k === 1 ? f : f ** this.k; },
  // Glows add light on a dark picture; on a bright one added light is lost
  // in the white, so they are painted over it instead.
  blend(c) { c.globalCompositeOperation = this.bright ? "source-over" : "lighter"; },
  stamp(c, img, x, y, radius, alpha) {
    c.globalAlpha = alpha;
    c.drawImage(img, x - radius, y - radius, radius * 2, radius * 2);
  },
  frame(t) {
    this.raf = 0;
    if (!this.seen() || this.tier() === "paused") return this.sync();
    // Home screen > Particle frame rate: between draws the frame is skipped,
    // and drawing is where the power goes
    let fps = Math.round(Number(this.look().particle_fps ?? 60));
    if (this.tier() === "reduced") fps = Math.min(fps >= 1 ? fps : 60, this.REDUCED.fps);
    if (fps >= 1 && fps < 60 && this.drawn && t - this.drawn < 1000 / fps - 2 && t >= this.drawn) {
      if (!this.showing() && !this.sparks.length) return this.sync();
      this.raf = requestAnimationFrame((t2) => this.frame(t2));
      return;
    }
    this.drawn = t;
    // the frame's length, so a capped frame rate moves them as far a second;
    // after a rest (hidden, resumed) it counts as one ordinary frame
    // a gap over a quarter second is a rest, not motion; up to that, a slow
    // frame cap (down to 4 a second) still moves them in real time
    const gap = this.last ? t - this.last : 0;
    this.k = gap > 0 && gap <= 250 ? Math.max(0.25, gap / (1000 / 60)) : 1;
    this.last = t;
    const c = this.ctx, w = innerWidth, h = innerHeight, m = this.mouse;
    const accent = this.look().accent || "#8ab4ff";
    // parallax: the pointer's place, eased, moves the near layer furthest
    const px = m.x >= 0 ? (m.x / w - 0.5) * -18 : 0, py = m.x >= 0 ? (m.y / h - 0.5) * -12 : 0;
    const ease = 1 - 0.94 ** this.k;
    this.ox += (px - this.ox) * ease; this.oy += (py - this.oy) * ease;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.clearRect(0, 0, w, h);
    if (this.surge > 0.02) {
      const s = this.surge * 3 * this.k;
      for (const d of this.dots) { d.x += d.vx * s; d.y += d.vy * s; }
      this.surge *= this.damp(0.88);
    } else this.surge = 0;
    if (this.showing() && this.level !== "off") this[this.KINDS[this.level].draw](c, t, w, h, m, accent);
    c.setTransform(1, 0, 0, 1, 0, 0);
    this.drawSparks(c);
    c.globalAlpha = 1;
    c.globalCompositeOperation = "source-over";
    if (!this.showing() && !this.sparks.length) return this.sync();
    this.raf = requestAnimationFrame((t2) => this.frame(t2));
  },
  // where a dot is drawn: its place plus the parallax for its depth
  X(d) { return d.x + this.ox * d.z; },
  Y(d) { return d.y + this.oy * d.z; },
  // Move every dot by its own rule; one that leaves the screen is born again.
  each(move) {
    for (let i = 0; i < this.dots.length; i++) if (move(this.dots[i]) === false) this.dots[i] = this.spawn(false);
  },
  // opacity steps for batched paths: STEPS paths at most, one draw call each
  STEPS: 6,
  buckets() { return Array.from({ length: this.STEPS }, () => []); },
  step(a) { return Math.max(0, Math.min(this.STEPS - 1, Math.round(a * (this.STEPS - 1)))); },
  strokeLines(c, groups, colour, width) {
    c.strokeStyle = colour;
    c.lineWidth = width;
    groups.forEach((seg, k) => {
      if (!seg.length || !k) return;
      c.globalAlpha = k / (this.STEPS - 1);
      c.beginPath();
      for (let i = 0; i < seg.length; i += 4) { c.moveTo(seg[i], seg[i + 1]); c.lineTo(seg[i + 2], seg[i + 3]); }
      c.stroke();
    });
  },
  drawMotes(c, t, w, h, m, accent) {
    const img = this.sprite(accent), white = this.sprite("#ffffff"), k = this.k, f = this.damp(0.995);
    this.blend(c);
    for (let i = 0; i < this.dots.length; i++) {
      const d = this.dots[i];
      this.push(d, m, 140, 0.02);
      d.vx *= f; d.vy = d.vy * f - 0.0005 * k;
      d.x += d.vx * k; d.y += d.vy * k;
      if (d.y < -10 || d.x < -10 || d.x > w + 10) { this.dots[i] = this.spawn(false); continue; }
      const glow = d.a * (0.6 + 0.4 * Math.sin(t / 900 + d.tw)), x = this.X(d), y = this.Y(d);
      this.stamp(c, img, x, y, d.r * 5, glow);
      this.stamp(c, white, x, y, d.r * 1.2, glow);
    }
  },
  drawEmbers(c, t, w, h, m, accent) {
    const imgs = [this.sprite(this.EMBER[0]), this.sprite(this.EMBER[1]), this.sprite(accent)], core = this.sprite("#fff4d6");
    const k = this.k, fx = this.damp(0.985), fy = this.damp(0.992);
    this.blend(c);
    for (let i = 0; i < this.dots.length; i++) {
      const d = this.dots[i];
      this.push(d, m, 160, 0.05);
      d.vx = d.vx * fx + Math.sin(t / 700 + d.tw) * 0.012 * k;
      d.vy = d.vy * fy - 0.004 * k;
      d.x += d.vx * k; d.y += d.vy * k;
      d.life -= 0.0016 * k;
      if (d.life <= 0 || d.y < -20 || d.x < -20 || d.x > w + 20) { this.dots[i] = this.spawn(false); continue; }
      const flicker = 0.55 + 0.45 * Math.sin(t / 90 + d.tw * 7);
      const fade = Math.min(1, d.life * 3), x = this.X(d), y = this.Y(d);
      this.stamp(c, imgs[d.c < 0 ? 2 : d.c], x, y, d.r * 7, d.a * fade * flicker);
      this.stamp(c, core, x, y, d.r * 1.8, d.a * fade);
    }
  },
  // v2: squared distances, and the lines in six opacity steps: six strokes
  // a frame instead of one per pair
  drawStars(c, t, w, h, m, accent) {
    const dots = this.dots, reach = 165, hand = 200, k = this.k, f = this.damp(0.999);
    for (const d of dots) {
      d.vx *= f; d.vy *= f;
      if (d.vx * d.vx + d.vy * d.vy < 0.0025) { d.vx += (Math.random() - 0.5) * 0.05; d.vy += (Math.random() - 0.5) * 0.05; }
      this.push(d, m, 90, 0.015);
      d.x += d.vx * k; d.y += d.vy * k;
      if (d.x < -5) d.x = w + 5; else if (d.x > w + 5) d.x = -5;
      if (d.y < -5) d.y = h + 5; else if (d.y > h + 5) d.y = -5;
    }
    const lines = this.buckets(), r2 = reach * reach, h2 = hand * hand;
    for (let i = 0; i < dots.length; i++) {
      const a = dots[i];
      for (let j = i + 1; j < dots.length; j++) {
        const b = dots[j], dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
        if (d2 < r2) lines[this.step((1 - Math.sqrt(d2) / reach) * 0.6)].push(a.x, a.y, b.x, b.y);
      }
      if (m.x >= 0) {
        const dx = a.x - m.x, dy = a.y - m.y, d2 = dx * dx + dy * dy;
        if (d2 < h2) lines[this.step((1 - Math.sqrt(d2) / hand) * 0.8)].push(a.x, a.y, m.x, m.y);
      }
    }
    this.strokeLines(c, lines, accent, 1.2);
    const img = this.sprite(accent), white = this.sprite("#ffffff");
    this.blend(c);
    for (const d of dots) {
      const tw = 0.6 + 0.4 * Math.sin(t / 700 + d.tw);
      this.stamp(c, img, d.x, d.y, d.r * 6, d.a * tw);
      this.stamp(c, white, d.x, d.y, d.r * 1.6, Math.min(1, d.a * tw * 1.4));
    }
  },
  // A petal is two curves, pre-drawn once per colour, turning as it falls
  // and swaying on the breeze (v2: a stamped sprite, not a path each).
  petal(col) {
    return this.shape(`petal${col}`, 32, 64, (g, w, h) => {
      g.fillStyle = col;
      g.beginPath();
      g.moveTo(16, 1);
      g.quadraticCurveTo(31, 26, 16, 63);
      g.quadraticCurveTo(1, 26, 16, 1);
      g.fill();
      // a faint vein down the middle
      g.globalAlpha = 0.25;
      g.strokeStyle = "#ffffff";
      g.lineWidth = 1.5;
      g.beginPath(); g.moveTo(16, 6); g.lineTo(16, 54); g.stroke();
    });
  },
  drawPetals(c, t, w, h, m, accent) {
    const tints = (this.KINDS[this.level].palette || this.PETAL).map((p, i) => this.petal(i === 3 ? this.mix(p, accent, 0.4) : p));
    const k = this.k, f = this.damp(0.99);
    this.each((d) => {
      this.push(d, m, 120, 0.04);
      d.vx = d.vx * f + (0.006 + Math.sin(t / 1300 + d.tw) * 0.012) * k;
      d.vy = d.vy * f + 0.006 * k;
      d.x += d.vx * k; d.y += d.vy * k; d.rot += (d.spin + Math.sin(t / 900 + d.tw) * 0.01) * k;
      if (d.y > h + 20 || d.x > w + 40) return false;
      const flat = 0.35 + 0.65 * Math.abs(Math.cos(t / 700 + d.tw)); // the petal turning edge-on
      const cos = Math.cos(d.rot), sin = Math.sin(d.rot);
      c.setTransform(cos, sin, -sin * flat, cos * flat, this.X(d), this.Y(d));
      c.globalAlpha = d.a;
      c.drawImage(tints[d.c], -d.r * 0.5, -d.r, d.r, d.r * 2);
    });
    c.setTransform(1, 0, 0, 1, 0, 0);
  },
  drawRain(c, t, w, h, m, accent) {
    const thin = this.buckets(), thick = this.buckets(), k = this.k;
    this.each((d) => {
      d.x += d.vx * k; d.y += d.vy * k;
      if (d.y > h + 30 || d.x < -40) return false;
      const x = this.X(d), y = this.Y(d);
      (d.r > 22 ? thick : thin)[this.step(d.a)].push(x, y, x - d.vx * d.r / d.vy, y - d.r);
    });
    c.lineCap = "round";
    this.strokeLines(c, thin, "#dce8ff", 0.9);
    this.strokeLines(c, thick, "#dce8ff", 1.3);
  },
  drawSnow(c, t, w, h, m, accent) {
    const img = this.sprite("#ffffff"), k = this.k, f = this.damp(0.98);
    this.each((d) => {
      this.push(d, m, 110, 0.03);
      d.vx = d.vx * f + Math.sin(t / 1600 + d.tw) * 0.02 * k;
      d.x += d.vx * k; d.y += d.vy * k;
      if (d.y > h + 10 || d.x < -20 || d.x > w + 20) return false;
      this.stamp(c, img, this.X(d), this.Y(d), d.r * 2.2, d.a);
    });
  },
  // Beams from the top corner, slowly swinging, with dust turning in them.
  drawRays(c, t, w, h, m, accent) {
    this.blend(c);
    const warm = this.mix("#fff2cf", accent, 0.3);
    for (let n = 0; n < 5; n++) {
      const swing = Math.sin(t / 6000 + n * 1.7) * 0.05;
      const ang = 0.55 + n * 0.11 + swing, spread = 0.03 + (n % 2) * 0.025;
      const len = Math.hypot(w, h) * 1.1, ox = -w * 0.05, oy = -h * 0.1;
      const g = c.createLinearGradient(ox, oy, ox + Math.cos(ang) * len, oy + Math.sin(ang) * len);
      const a = 0.05 + 0.03 * Math.sin(t / 2500 + n);
      g.addColorStop(0, this.hexAlpha(warm, Math.min(1, a * 4)));
      g.addColorStop(1, "rgba(255,255,255,0)");
      c.fillStyle = g;
      c.globalAlpha = 1;
      c.beginPath();
      c.moveTo(ox, oy);
      c.lineTo(ox + Math.cos(ang - spread) * len, oy + Math.sin(ang - spread) * len);
      c.lineTo(ox + Math.cos(ang + spread) * len, oy + Math.sin(ang + spread) * len);
      c.fill();
    }
    const img = this.sprite("#fff4d6"), k = this.k, f = this.damp(0.99);
    this.each((d) => {
      d.vx = d.vx * f + Math.sin(t / 2000 + d.tw) * 0.004 * k;
      d.vy = d.vy * f + Math.cos(t / 2300 + d.tw) * 0.004 * k;
      d.x += d.vx * k; d.y += d.vy * k;
      if (d.y < -10 || d.x < -10 || d.x > w + 10 || d.y > h + 10) return false;
      this.stamp(c, img, this.X(d), this.Y(d), d.r * 2.5, d.a * (0.5 + 0.5 * Math.sin(t / 800 + d.tw)));
    });
  },
  drawFlies(c, t, w, h, m, accent) {
    const img = this.sprite(this.mix("#d9ff7a", accent, 0.25)), core = this.sprite("#fbffe0"), k = this.k, f = this.damp(0.985);
    this.blend(c);
    this.each((d) => {
      if (m.x >= 0) {
        const dx = m.x - d.x, dy = m.y - d.y, dist = Math.hypot(dx, dy);
        if (dist < 260 && dist > 30) { d.vx += dx / dist * 0.01 * k; d.vy += dy / dist * 0.01 * k; }
      }
      d.vx = d.vx * f + Math.sin(t / 1100 + d.tw * 3) * 0.012 * k;
      d.vy = d.vy * f + Math.cos(t / 1300 + d.tw * 2) * 0.012 * k;
      d.x += d.vx * k; d.y += d.vy * k;
      if (d.y < -20 || d.x < -20 || d.x > w + 20 || d.y > h + 20) return false;
      const blink = Math.max(0, Math.sin(t / 700 + d.tw * 5)) ** 3, x = this.X(d), y = this.Y(d);
      this.stamp(c, img, x, y, d.r * 9, d.a * blink);
      this.stamp(c, core, x, y, d.r * 1.6, d.a * (0.3 + blink * 0.7));
    });
  },
  // rising bubbles: a thin rim, a highlight, a wobble, and they part round the pointer
  spawnBubble(anywhere, w, h, r) {
    return { x: r() * w, y: anywhere ? r() * h : h + 30, r: 3 + r() ** 2 * 16, vx: 0, vy: -(0.3 + r() * 0.6), a: 0.25 + r() * 0.35, tw: r() * 6.28 };
  },
  drawBubbles(c, t, w, h, m, accent) {
    const rim = this.mix("#bfefff", accent, 0.3), rims = this.buckets(), shines = this.buckets(), k = this.k;
    const fx = this.damp(0.95), fy = this.damp(0.995);
    this.each((d) => {
      this.push(d, m, 110, 0.05);
      d.vx = d.vx * fx + Math.sin(t / 600 + d.tw) * 0.03 * k;
      d.vy = d.vy * fy - 0.002 * k;
      d.x += d.vx * k; d.y += d.vy * k;
      if (d.y < -40) return false;
      rims[this.step(d.a)].push(this.X(d), this.Y(d), d.r);
      shines[this.step(d.a * 0.8)].push(this.X(d) - d.r * 0.35, this.Y(d) - d.r * 0.35, Math.max(0.8, d.r * 0.18));
    });
    c.lineWidth = 1.2;
    c.strokeStyle = rim;
    c.fillStyle = "#ffffff";
    for (let n = 1; n < this.STEPS; n++) {
      const a = n / (this.STEPS - 1), rs = rims[n], ss = shines[n];
      c.globalAlpha = a;
      if (rs.length) {
        c.beginPath();
        for (let i = 0; i < rs.length; i += 3) { c.moveTo(rs[i] + rs[i + 2], rs[i + 1]); c.arc(rs[i], rs[i + 1], rs[i + 2], 0, Math.PI * 2); }
        c.stroke();
      }
      if (ss.length) {
        c.beginPath();
        for (let i = 0; i < ss.length; i += 3) { c.moveTo(ss[i] + ss[i + 2], ss[i + 1]); c.arc(ss[i], ss[i + 1], ss[i + 2], 0, Math.PI * 2); }
        c.fill();
      }
    }
  },
  // still stars that twinkle, and now and then one shoots across
  spawnTwinkle(anywhere, w, h, r) {
    return { x: r() * w, y: r() * h, r: 0.5 + r() ** 3 * 2.2, vx: 0, vy: 0, a: 0.3 + r() * 0.6, tw: r() * 6.28, sp: 0.4 + r() * 1.6 };
  },
  drawTwinkle(c, t, w, h, m, accent) {
    const img = this.sprite(this.mix("#ffffff", accent, 0.15)), k = this.k;
    this.blend(c);
    this.each((d) => {
      if (d.shoot) {
        d.x += d.vx * k; d.y += d.vy * k; d.life -= k;
        if (d.life <= 0 || d.x > w + 50 || d.y > h + 50) return false;
        c.globalAlpha = Math.min(1, d.life / 20);
        c.strokeStyle = "#ffffff"; c.lineWidth = 1.5;
        c.beginPath(); c.moveTo(d.x, d.y); c.lineTo(d.x - d.vx * 12, d.y - d.vy * 12); c.stroke();
        return;
      }
      const tw = 0.55 + 0.45 * Math.sin(t / 1000 * d.sp + d.tw);
      this.stamp(c, img, d.x, d.y, d.r * 4, d.a * tw);
      if (Math.random() < 0.00004 * k) Object.assign(d, { shoot: true, vx: 9 + Math.random() * 6, vy: 3 + Math.random() * 3, life: 60 });
    });
  },
  drawDust(c, t, w, h, m, accent) {
    const img = this.sprite(this.mix("#ffd8a8", accent, 0.2)), k = this.k, fx = this.damp(0.995), fy = this.damp(0.99);
    this.blend(c);
    this.each((d) => {
      this.push(d, m, 120, 0.02);
      d.vx = d.vx * fx + 0.001 * k; d.vy = d.vy * fy + Math.sin(t / 1800 + d.tw) * 0.003 * k;
      d.x += d.vx * k; d.y += d.vy * k;
      if (d.x > w + 10 || d.y < -10 || d.y > h + 10) return false;
      this.stamp(c, img, this.X(d), this.Y(d), d.r * 3, d.a * (0.6 + 0.4 * Math.sin(t / 1100 + d.tw)));
    });
  },
  // glitter: tiny sparkles falling slowly, each flashing into a four-point star
  spawnGlitter(anywhere, w, h, r) {
    return { x: r() * w, y: anywhere ? r() * h : -10, r: 0.8 + r() * 1.8, vx: (r() - 0.5) * 0.2, vy: 0.15 + r() * 0.35,
      a: 0.5 + r() * 0.5, tw: r() * 6.28, sp: 0.8 + r() * 2.5, c: Math.floor(r() * 4) };
  },
  drawGlitter(c, t, w, h, m, accent) {
    const tints = ["#ffffff", this.mix("#ffd6f5", accent, 0.3), this.mix("#c9f1ff", accent, 0.3), accent];
    const rays = tints.map(() => this.buckets()), k = this.k, f = this.damp(0.99);
    this.blend(c);
    this.each((d) => {
      this.push(d, m, 120, 0.03);
      d.vx = d.vx * f + Math.sin(t / 1400 + d.tw) * 0.004 * k;
      d.x += d.vx * k; d.y += d.vy * k;
      if (d.y > h + 10 || d.x < -10 || d.x > w + 10) return false;
      const flash = Math.max(0, Math.sin(t / 1000 * d.sp + d.tw)) ** 6, x = this.X(d), y = this.Y(d);
      this.stamp(c, this.sprite(tints[d.c]), x, y, d.r * (2 + flash * 4), d.a * (0.35 + flash * 0.65));
      if (flash > 0.25) {
        const len = d.r * (3 + flash * 7), b = rays[d.c][this.step(d.a * flash)];
        b.push(x - len, y, x + len, y, x, y - len, x, y + len);
      }
    });
    rays.forEach((groups, n) => this.strokeLines(c, groups, tints[n], 1));
  },
  // lanterns: warm paper lanterns drifting up, swaying, their light breathing
  spawnLantern(anywhere, w, h, r) {
    const depth = 0.4 + r() * 0.8;
    return { x: r() * w, y: anywhere ? r() * h : h + 40, r: 7 + depth * 9, vx: 0, vy: -(0.12 + depth * 0.3),
      a: 0.45 + depth * 0.4, tw: r() * 6.28, z: depth };
  },
  // one lantern body, pre-drawn: 64 wide, 96 tall, the paper in 64 x 86
  lantern() {
    return this.shape("lantern", 64, 96, (g) => {
      const grad = g.createLinearGradient(0, 5, 0, 91);
      grad.addColorStop(0, "#ffcf7a"); grad.addColorStop(0.5, "#ff9a3c"); grad.addColorStop(1, "#d8582a");
      g.fillStyle = grad;
      g.beginPath();
      if (g.roundRect) g.roundRect(0, 5, 64, 86, 22); else g.rect(0, 5, 64, 86);
      g.fill();
      // ribs, and the dark rims top and bottom
      g.globalAlpha = 0.18;
      g.fillStyle = "#7a3010";
      for (let y = 18; y < 86; y += 12) g.fillRect(2, y, 60, 1.5);
      g.globalAlpha = 1;
      g.fillStyle = "#5a2a14";
      g.fillRect(14, 0, 36, 8);
      g.fillRect(14, 88, 36, 8);
    });
  },
  drawLanterns(c, t, w, h, m, accent) {
    const glow = this.sprite(this.mix("#ffb45c", accent, 0.2)), core = this.sprite("#fff1c9"), body = this.lantern();
    const k = this.k, f = this.damp(0.98);
    this.each((d) => {
      this.push(d, m, 130, 0.02);
      d.vx = d.vx * f + Math.sin(t / 2200 + d.tw) * 0.01 * k;
      d.x += d.vx * k; d.y += d.vy * k;
      if (d.y < -60 || d.x < -40 || d.x > w + 40) return false;
      const breathe = 0.8 + 0.2 * Math.sin(t / 900 + d.tw), x = this.X(d), y = this.Y(d);
      this.blend(c);
      this.stamp(c, glow, x, y, d.r * 4.5, d.a * 0.55 * breathe);
      c.globalCompositeOperation = "source-over";
      const tilt = Math.sin(t / 1600 + d.tw) * 0.08, cos = Math.cos(tilt), sin = Math.sin(tilt), s = d.r / 32;
      c.setTransform(cos * s, sin * s, -sin * s, cos * s, x, y);
      c.globalAlpha = d.a * breathe;
      c.drawImage(body, -32, -48);
      c.setTransform(1, 0, 0, 1, 0, 0);
      this.blend(c);
      this.stamp(c, core, x, y, d.r * 1.3, d.a * breathe);
    });
  },
  // wisps: spirit lights wandering on a slow current, each trailing light
  spawnWisp(anywhere, w, h, r) {
    const x = anywhere ? r() * w : r() < 0.5 ? -20 : w + 20, y = r() * h;
    return { x, y, r: 2 + r() * 2.5, vx: 0, vy: 0, a: 0.5 + r() * 0.45, tw: r() * 6.28, ang: r() * 6.28,
      trail: new Float32Array(16).fill(NaN), tick: 0, c: r() < 0.5 ? 0 : 1 };
  },
  drawWisps(c, t, w, h, m, accent) {
    const tints = [this.sprite(accent), this.sprite(this.mix("#9ffcff", accent, 0.35))], core = this.sprite("#ffffff"), k = this.k;
    this.blend(c);
    this.each((d) => {
      // a smooth current that turns them; the pointer draws them in a little
      const turn = Math.sin(d.x * 0.0035 + t * 0.00025 + d.tw) + Math.cos(d.y * 0.004 - t * 0.0002);
      d.ang += turn * 0.018 * k;
      const sp = 0.9 * d.z;
      d.x += (Math.cos(d.ang) * sp + d.vx) * k; d.y += (Math.sin(d.ang) * sp * 0.7 + d.vy) * k;
      d.vx *= this.damp(0.95); d.vy *= this.damp(0.95);
      this.push(d, m, 180, -0.012);
      if (d.x < -60 || d.x > w + 60 || d.y < -60 || d.y > h + 60) return false;
      // the trail keeps a point every few frames, oldest last
      if ((d.tick += k) >= 7) {
        d.tick = 0;
        d.trail.copyWithin(2, 0, 14);
        d.trail[0] = d.x; d.trail[1] = d.y;
      }
      const img = tints[d.c], pulse = 0.75 + 0.25 * Math.sin(t / 600 + d.tw);
      for (let i = 5; i >= 1; i--) {
        const tx = d.trail[i * 2], ty = d.trail[i * 2 + 1];
        if (tx !== tx) continue; // not filled yet (NaN)
        const fade = 1 - i / 6;
        this.stamp(c, img, tx + this.ox * d.z, ty + this.oy * d.z, d.r * (2.5 + 3.5 * fade), d.a * 0.55 * fade * pulse);
      }
      const x = this.X(d), y = this.Y(d);
      this.stamp(c, img, x, y, d.r * 8, Math.min(1, d.a * 0.9 * pulse));
      this.stamp(c, core, x, y, d.r * 1.4, d.a * pulse);
    });
  },
  // galaxy: a slowly turning two-armed spiral of stars, tilted, round a bright core
  spawnGalaxy(anywhere, w, h, r) {
    // most stars out along the two arms, a dense few in the core
    const core = r() < 0.18, R = core ? r() ** 2 * 0.18 : 0.1 + r() ** 1.15 * 0.9, arm = r() < 0.5 ? 0 : Math.PI;
    return { x: 0, y: 0, r: 0.6 + r() ** 2 * 1.8, vx: 0, vy: 0, a: 0.35 + r() * 0.6, tw: r() * 6.28, z: 1,
      R, th: core ? r() * 6.28 : arm + R * 5.6 + (r() - 0.5) * (0.28 + R * 0.35), lift: (r() - 0.5) * 0.12 * (1 - R), c: r() < 0.25 + (1 - R) * 0.5 ? 0 : r() < 0.6 ? 1 : 2 };
  },
  drawGalaxy(c, t, w, h, m, accent) {
    const cx = w * 0.5 + this.ox * 2, cy = h * 0.46 + this.oy * 2, size = Math.min(w, h) * 0.62 * (this.scale().size ** 0.5);
    const tints = [this.sprite("#fff3dd"), this.sprite(accent), this.sprite(this.mix("#ff9ad5", accent, 0.3))];
    const k = this.k, tilt = -0.38, ct = Math.cos(tilt), stt = Math.sin(tilt), spd = this.scale().speed;
    this.blend(c);
    this.stamp(c, tints[0], cx, cy, size * 0.28, 0.24);
    for (const d of this.dots) {
      d.th += (0.0009 / (0.25 + d.R)) * k * spd; // inner stars turn faster
      const ex = Math.cos(d.th) * d.R * size, ey = (Math.sin(d.th) * d.R * 0.42 + d.lift) * size;
      const x = cx + ex * ct - ey * stt, y = cy + ex * stt + ey * ct;
      const tw = 0.6 + 0.4 * Math.sin(t / 900 + d.tw);
      this.stamp(c, tints[d.c], x, y, d.r * 4.2, Math.min(1, d.a * tw * 1.2));
    }
  },
  drawSparks(c) {
    if (!this.sparks.length) return;
    const k = this.k, f = this.damp(0.955);
    this.blend(c);
    for (const p of this.sparks) {
      p.vx *= f; p.vy = p.vy * f + 0.09 * k;
      p.x += p.vx * k; p.y += p.vy * k;
      p.life -= 0.022 * k;
      if (p.life > 0) this.stamp(c, this.sprite(p.color), p.x, p.y, p.r * 5 * (0.5 + p.life / 2), p.life);
    }
    this.sparks = this.sparks.filter((p) => p.life > 0);
  },
};
