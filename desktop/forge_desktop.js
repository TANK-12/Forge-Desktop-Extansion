/* The desktop's widgets and visualiser (S25, 2026-09-28), beside its particles.

   the owner: "bring over to the extension for desktop particles, the widgets, and
   a way to actually add the widgets", "let me also add visualisers to the
   desktop extension", "let it be fairly separate from Forge so it can run even
   without Forge running". desktop/particles.py shows this page behind every
   window and answers it through the "desktop" message handler; nothing here
   needs Forge's window or its apps:
     stats     CPU, memory, battery, network, disks, Jan (shared/forge_sysinfo.py)
     players   every media player on the laptop (shared/forge_mpris.py)
     levels    the sound, from cava (shared/forge_music.SystemLevels), only
               while the visualiser or a Visualiser widget draws
     save      the desktop's own widget layout, kept in
               ~/.config/forge/desktop-widgets.json (the hub keeps its own)
     rects     the parts of the screen that take clicks; the rest go straight
               through to the desktop below
   The widgets are shared/forge_widgets.js on a 24-column grid over the whole
   screen; the visualiser styles are shared/forge_visual.js.

   Windows and macOS (Relay task 130): desktop/host/ shows this same page
   through pywebview and answers the same messages. Its start-up data says
   which part of the desktop this window is ("scenery": particles and
   visualiser behind the desktop icons, "widgets": the widgets above them,
   "all": everything, as on Linux) and what this OS or machine can't do
   (caps, from desktop/platforms.json): those widgets are not offered. */
(() => {
  // Linux: WebKitGTK's "desktop" message handler (desktop/particles.py).
  // Windows and macOS: pywebview's bridge, which only exists once pywebview
  // says it is ready, so what is sent before that waits for it. pywebview
  // hands each call to a thread of its own, so they are numbered (with this
  // load's id) and the host never lets an older one undo a newer one
  // (desktop/host/webview_shell.py Bridge).
  const waiting = [];
  const load = Math.random().toString(36).slice(2);
  let seq = 0;
  const flush = () => {
    const api = window.pywebview && window.pywebview.api;
    while (api && api.message && waiting.length) api.message(waiting.shift(), ++seq, load);
  };
  const send = (msg) => {
    const text = JSON.stringify(msg);
    try { window.webkit.messageHandlers.desktop.postMessage(text); return; } catch (e) { /* not WebKitGTK */ }
    if (waiting.length < 200) waiting.push(text);
    flush();
  };
  addEventListener("pywebviewready", flush);
  const $ = (id) => document.getElementById(id);
  const W = window.ForgeWidgets;
  const BOOT = window.FORGE_DESKTOP || {};
  const PART = ["scenery", "widgets"].includes(BOOT.part) ? BOOT.part : "all";
  const CAPS = BOOT.caps || {};
  // a widget whose data this OS or machine can't give is not offered or
  // drawn; its saved cards stay (a sound device plugged in brings them back)
  if (W) for (const kind of CAPS.hide_widgets || []) W.hide(kind);

  const D = {
    theme: window.FORGE_THEME || {},
    stats: null, players: [], raw: null, smooth: null, levelsAt: 0, wantLevelsAt: 0, askedLevels: 0,
    playersAt: 0, mounted: false, lastRects: "",

    // --- what Python sends -----------------------------------------------------
    receive(m) {
      if (!m || typeof m !== "object") return;
      if (m.stats) this.stats = m.stats;
      if (Array.isArray(m.players)) {
        const now = Date.now();
        this.players = m.players.filter((p) => p && typeof p.bus === "string").map((p) => ({
          id: "sys:" + p.bus, kind: "sys", bus: p.bus, app: String(p.name || "Player"),
          state: p.state === "playing" ? "playing" : p.state === "paused" ? "paused" : "stopped",
          title: String(p.title || ""), artist: String(p.artist || ""),
          artwork: /^(https?|file):\/\//.test(p.artwork || "") ? p.artwork : "",
          current: Number(p.position) || 0, duration: Number(p.duration) || 0, volume: Number(p.volume) || 0,
          canNext: !!p.canNext, canPrev: !!p.canPrev, at: now,
          // what a player can't be asked for here (Windows: no volume, no
          // going to it), so its widget hides that control; Linux sends none
          canVolume: p.canVolume !== false, canRaise: p.canRaise !== false, canSeek: p.canSeek !== false,
        }));
        if (W && this.mounted) W.tick();
      }
      if (Array.isArray(m.levels)) {
        this.raw = m.levels;
        this.levelsAt = Date.now();
        // sound after a silence: draw now, not at the next slow silent frame
        if (this.idle && m.levels.some((x) => x > 0.003)) {
          clearTimeout(this.idle);
          this.idle = 0;
          this.raf = requestAnimationFrame(() => this.frame());
        }
      }
      if (m.edit !== undefined) this.edit(!!m.edit);
      if (m.wallpaper) this.wallpaper(m.wallpaper);
    },
    // Behind the desktop icons on Windows nothing else shows through, so
    // the scenery paints the wallpaper itself, under the particles.
    wallpaper(w) {
      if (PART !== "scenery" || !w || typeof w !== "object") return;
      const fit = { fit: "contain", stretch: "100% 100%", center: "auto", tile: "auto" }[w.style] || "cover";
      const s = document.body.style;
      s.backgroundColor = /^#[0-9a-f]{6}$/i.test(w.color || "") ? w.color : "#000";
      s.backgroundImage = w.url ? `url("${String(w.url).replace(/"/g, "%22")}")` : "none";
      s.backgroundSize = fit;
      s.backgroundRepeat = w.style === "tile" ? "repeat" : "no-repeat";
      s.backgroundPosition = w.style === "tile" ? "0 0" : "center";
    },

    // --- the widgets' ctx ------------------------------------------------------
    main() {
      const live = this.players.filter((p) => p.state !== "stopped" || p.title);
      return live.find((p) => p.state === "playing") || live[0] || null;
    },
    position(m) {
      const p = m.current + (m.state === "playing" ? (Date.now() - m.at) / 1000 : 0);
      return m.duration ? Math.min(p, m.duration) : p;
    },
    media(op, value) {
      const m = this.main();
      if (!m) return;
      const msg = { op: op === "go" ? "raise" : op, bus: m.bus };
      if (value !== undefined) msg.value = value;
      send({ media: msg });
      if (op === "toggle") { m.current = this.position(m); m.at = Date.now(); m.state = m.state === "playing" ? "paused" : "playing"; }
      setTimeout(() => send({ players: true }), 400);
    },
    // smoothed levels for whoever draws; asking keeps cava running
    levels() {
      this.wantLevelsAt = Date.now();
      if (Date.now() - this.askedLevels > 2000) { this.askedLevels = Date.now(); send({ levels: { on: true, fps: this.visFps() } }); }
      // cava sends silence once and then nothing, so an old silent frame
      // still holds: the visualiser rests at zero instead of vanishing until
      // the next sound (the owner, 2026-09-29: it "pops in and out"). An old
      // frame with sound in it means cava has gone.
      if (!this.raw || (Date.now() - this.levelsAt > 3000 && this.raw.some(Boolean))) return null;
      if (!this.smooth || this.smooth.length !== this.raw.length) this.smooth = new Float32Array(this.raw.length);
      const now = performance.now(), dt = Math.min(0.25, (now - (this.smoothAt || now)) / 1000);
      this.smoothAt = now;
      return ForgeVisual.follow(this.smooth, this.raw, dt);
    },
    ctx() {
      return {
        now: () => this.main(),
        media: (op) => this.media(op),
        seek: (sec) => this.media("seek", sec),
        volume: (v) => this.media("volume", v / 100),
        position: (m) => this.position(m),
        refresh: () => { if (Date.now() - this.playersAt > 1500) { this.playersAt = Date.now(); send({ players: true }); } },
        stats: () => this.stats,
        levels: () => this.levels(),
        clock24: () => this.theme.clock_24h !== false,
        notify: (text) => send({ notify: text }),
      };
    },

    // --- the widgets -----------------------------------------------------------
    widgetsOn() { return PART !== "scenery" && this.theme.desktop_widgets === true; },
    starter() {
      // a column down the right of the screen: clock, weather, music
      // (a kind this OS can't show is left out: CAPS above)
      return [{ name: "Desktop", items: [
        { kind: "clock", x: 17, y: 0, w: 7, h: 2 },
        { kind: "weather", x: 17, y: 2, w: 7, h: 2 },
        { kind: "player", x: 17, y: 4, w: 7, h: 3 },
      ].filter((i) => W.KINDS[i.kind]) }];
    },
    mount() {
      if (!W || this.mounted) return;
      this.mounted = true;
      W.cols = 24;
      W.store = {
        load: () => (window.FORGE_DESKTOP || {}).layout || null,
        save: (data) => send({ save: data }),
      };
      W.starterPages = () => this.starter();
      W.mount($("widgets"), this.ctx());
      document.addEventListener("forge-widgets-layout", () => this.clickable());
      addEventListener("resize", () => this.clickable());
      // Esc or Done ends editing; the window then lets clicks through again
      const done = W.edit.bind(W);
      W.edit = (on) => { done(on); document.body.classList.toggle("editing", !!on); send({ editing: !!on }); this.clickable(); };
    },
    edit(on) {
      if (!this.widgetsOn()) return;
      this.mount();
      W.edit(on);
    },
    // The parts that take clicks: every card, the page dots, the corner
    // button, a ringing alarm and, with two pages or more, the widget area;
    // all of the screen while editing.
    clickable() {
      let rects = [];
      if (PART === "scenery") rects = [];
      else if (this.mounted && W.editing) rects = [[0, 0, innerWidth, innerHeight]];
      else {
        const parts = [...document.querySelectorAll("#widgets:not([hidden]) .wg, #widgets:not([hidden]) .wg-dots, #wg-ring.on")];
        // two or more pages: the whole widget area, so a two-finger swipe
        // over bare desktop turns the page (three fingers are GNOME's)
        if (this.mounted && W.pages.length > 1 && !$("widgets").hidden) parts.push($("widgets"));
        if (this.widgetsOn() && this.theme.desktop_edit_button !== false) parts.push($("dk-edit"));
        if (this.theme.desktop_edit_button !== false) parts.push($("dk-settings"));
        rects = parts.map((n) => n.getBoundingClientRect()).filter((r) => r.width && r.height)
          .map((r) => [Math.floor(r.left), Math.floor(r.top), Math.ceil(r.width), Math.ceil(r.height)]);
      }
      const sig = JSON.stringify(rects);
      if (sig !== this.lastRects) { this.lastRects = sig; send({ rects }); }
    },

    // --- the visualiser --------------------------------------------------------
    st: {}, raf: 0, style: "off",
    visFps() { return Number(this.now.desktop_visualiser_fps) || 30; },
    visWidgets() { return this.mounted && this.widgetsOn() && W.pages.some((p) => p.items.some((i) => i.kind === "visualiser")); },
    release() {
      if (!this.askedLevels) return;
      this.askedLevels = 0;
      send({ levels: { on: false } });
    },
    visual() {
      // off on battery and under GPU load too (shared/forge_power.js)
      // the scenery behind the icons draws it when the desktop is split in
      // two; with no sound to read on this machine it is not drawn at all
      const cut = PART === "widgets" || !!(CAPS.cut || {}).levels;
      const want = cut ? "off" : this.now.desktop_visualiser || "off";
      this.style = window.ForgeVisual && want in ForgeVisual.STYLES && this.theme.animations !== false ? want : "off";
      const cv = $("visual");
      if (this.style === "off") {
        if (this.raf) cancelAnimationFrame(this.raf);
        clearTimeout(this.idle);
        this.raf = this.idle = 0;
        cv.getContext("2d").clearRect(0, 0, cv.width, cv.height);
        // cava is let go at once unless a Visualiser widget still draws
        if (!this.visWidgets()) this.release();
        return;
      }
      if (!this.raf) this.raf = requestAnimationFrame(() => this.frame());
    },
    frame() {
      this.raf = 0;
      if (this.style === "off") return;
      const cv = $("visual");
      if (cv.width !== innerWidth) cv.width = innerWidth;
      if (cv.height !== innerHeight) cv.height = innerHeight;
      const c = cv.getContext("2d"), t = this.theme;
      c.clearRect(0, 0, cv.width, cv.height);
      const lv = this.levels();
      if (lv) {
        ForgeVisual.draw(c, cv.width, cv.height, this.style, lv, { size: Number(t.desktop_visualiser_size || 1), gain: t.visualiser_gain,
          opacity: t.visualiser_opacity, scheme: t.visualiser_colours, accent: t.accent || "#8ab4ff", accent2: t.accent_2 || t.accent, st: this.st });
      }
      // silence with a still style: ten frames a second, not the full rate,
      // so a quiet room does not keep the desktop redrawing (task 56)
      if (lv && ForgeVisual.stillInSilence(this.style, t.visualiser_colours) && lv.every((x) => x < 0.003)) {
        this.raf = -1;
        this.idle = setTimeout(() => { this.idle = 0; this.raf = requestAnimationFrame(() => this.frame()); }, 100);
      } else this.raf = requestAnimationFrame(() => this.frame());
    },

    // --- settings --------------------------------------------------------------
    apply(theme) {
      this.theme = theme || {};
      const t = this.theme;
      this.now = window.ForgePower ? ForgePower.apply(t, !!t.on_battery, { gpu: t.gpu_level, reason: t.gpu_reason, battery: t.battery, shown: t.shown }) : t;
      if (this.widgetsOn()) this.mount();
      if (this.mounted) {
        // the Widgets settings, with the desktop's own switch and size
        W.apply(Object.assign({}, t, { widgets: this.widgetsOn(), widget_scale: t.desktop_widget_scale ?? 1, power_tier: this.now.power_tier || "full" }));
        if (!this.widgetsOn() && W.editing) W.edit(false);
      }
      $("dk-edit").hidden = !(this.widgetsOn() && t.desktop_edit_button !== false);
      // settings are wanted with only particles or a visualiser on, too
      // (the scenery behind the icons takes no clicks)
      $("dk-settings").hidden = PART === "scenery" || t.desktop_edit_button === false;
      // the widgets wear the same glass as on the home screen
      if (window.ForgeGlass) ForgeGlass.apply(t);
      document.documentElement.style.setProperty("--accent", t.accent || "#8ab4ff");
      document.documentElement.style.setProperty("--accent-2", t.accent_2 || t.accent || "#8ab4ff");
      document.documentElement.style.setProperty("--on-accent", t.on_accent || "#0b1220");
      this.visual();
      // frame rate: the fastest thing that moves, never above the Speed page's cap
      const moving = [];
      if (PART !== "widgets" && t.desktop_particles !== false) moving.push(Number(this.now.desktop_particle_fps) || 30);
      if (this.style !== "off" || this.visWidgets()) moving.push(this.visFps());
      if (window.ForgeFrameCap && moving.length) ForgeFrameCap.set(Math.min(Number(this.now.max_fps) || 60, Math.max(...moving)));
      this.clickable();
    },
    init() {
      $("dk-edit").addEventListener("click", () => this.edit(true));
      // desktop/particles.py opens the settings: the extension's own page on
      // GNOME (no Forge needed), Forge's Settings elsewhere
      $("dk-settings").addEventListener("click", () => send({ settings: true }));
      const base = window.forgeTheme;
      window.forgeTheme = (next) => { if (base) base(next); this.apply(window.FORGE_THEME); };
      window.forgeDesktop = (m) => this.receive(m);
      // asking keeps cava running; it stops a few seconds after nobody asks
      setInterval(() => { if (Date.now() - this.wantLevelsAt > 2500) this.release(); }, 2000);
      setInterval(() => this.clickable(), 2000);
      this.wallpaper(BOOT.wallpaper);
      this.apply(window.FORGE_THEME || {});
      send({ ready: true });
    },
  };
  window.ForgeDesktop = D;
  D.init();
})();
