/* Forge home widgets (B20), shared/forge_widgets.js with forge_widgets.css.

   the owner (2026-09-27): "make widgets and a customizable home and widget pages
   and a cool relax thing", then "just do what is cool, like MD3, or like 5
   made by you". So: Material 3 style cards (tonal surfaces from the accent,
   28 px corners), or the tiles' glass, on pages of a 12-column snapping grid,
   and a Relax mode for box breathing.

   Adding a widget is one ForgeWidgets.define(kind, spec) call:
     label, icon     for the Add menu
     size [w, h]     its size when added, in grid cells; min [w, h] the smallest
     render(el, ctx, item, W)  builds it in el; may return update(), which runs
                     once a second while the home screen is on screen
     options         more choices the widget's ⚙ shows in edit mode, each
                     {key, label, kind: choice|text|bool|number, choices,
                     labels, min, max, step, default}; every widget also has
                     a colour and a look. Read with W.opt(item, key).
   ctx comes from the page (the hub, or the desktop's desktop/particles.html):
   now() -> the main Now playing entry or null, media(op), seek(seconds),
   volume(0-100), position(entry), refresh() (asks for the players again),
   stats() -> {cpu, mem, battery, charging, net, disks, jan} or null,
   levels() -> the visualiser's levels or null, clock24(), notify(text).
   Pages and their widgets are kept by W.store: localStorage ("forge.widgets")
   in the hub; the desktop puts its own store in first, a file beside Forge's
   settings, so the two layouts are separate.
   S25 (2026-09-28): weather, media player, calendar, disk, network, Jan,
   alarms and visualiser widgets; the ⚙ options; the store.
   v2 (2026-10-07, the owner: "widgets v2"): Activity (CPU, memory and GPU
   over the last minutes, temperatures when the stats carry them), Battery
   and Countdown widgets; an analogue face for the Clock; a date option kind;
   smoother cards (animated gauges and meters, a page turn); the visualiser
   card draws only while it can be seen, at its own frame cap, slows to a
   few frames in silence and follows the shared power tier
   (theme.power_tier: full, reduced or paused). Its styles come from
   ForgeVisual, so a new style shows in its menu with no edit here. */
(() => {
  const KEY = "forge.widgets";
  const COLS = 12; // the hub's grid; a page may set W.cols (the desktop uses 24)
  const GAP = 12; // matches .wg-grid's gap
  const CSS = (document.currentScript && document.currentScript.src || "").replace(/\.js(\?.*)?$/, ".css");
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const button = (cls, text, title) => {
    const b = el("button", cls, text);
    b.type = "button";
    if (title) { b.title = title; b.setAttribute("aria-label", title); }
    return b;
  };
  // dark text on a light colour, light text on a dark one
  const onColour = (hex) => {
    const n = parseInt(hex.slice(1), 16), lum = (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
    return lum > 0.6 ? "#0b1220" : "#f6f8ff";
  };
  let serial = 0;
  const uid = () => `w${Date.now().toString(36)}${(serial++).toString(36)}`;

  const W = {
    KINDS: {}, HIDDEN: {}, pages: [], page: 0, editing: false, on: true, root: null, ctx: {}, theme: {},
    updates: [], relaxing: false, always: [], cache: {}, cols: COLS,
    define(kind, spec) { this.KINDS[kind] = spec; },
    // a kind this system can't feed (a Forge release cuts some) is neither
    // offered nor drawn, but its saved cards stay for the day it can
    hide(kind) {
      if (!this.KINDS[kind]) return;
      this.HIDDEN[kind] = this.KINDS[kind];
      delete this.KINDS[kind];
    },
    known(kind) { return !!(this.KINDS[kind] || this.HIDDEN[kind]); },
    store: {
      load() { try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { return null; } },
      save(data) { try { localStorage.setItem(KEY, JSON.stringify(data)); } catch (e) { /* private mode */ } },
    },
    // Every widget's own options come after these two.
    COMMON: [
      { key: "colour", label: "Colour", kind: "colour", default: "" },
      { key: "look", label: "Look", kind: "choice", default: "", choices: ["", "material", "glass", "clear"],
        labels: { "": "Like the others", material: "Material", glass: "Glass", clear: "See-through" } },
    ],
    options(kind) { return this.COMMON.concat((this.KINDS[kind] || {}).options || []); },
    opt(item, key) {
      const o = item.o || {};
      if (key in o) return o[key];
      const spec = this.options(item.kind).find((x) => x.key === key);
      return spec ? spec.default : undefined;
    },
    setOpt(item, key, value, quiet) {
      item.o = Object.assign({}, item.o, { [key]: value });
      this.save();
      if (!quiet) this.render();
    },

    starter() {
      return [
        { name: "Home", items: [
          { kind: "clock", x: 0, y: 0, w: 3, h: 2 },
          { kind: "nowplaying", x: 3, y: 0, w: 4, h: 2 },
          { kind: "system", x: 7, y: 0, w: 3, h: 2 },
          { kind: "breathe", x: 10, y: 0, w: 2, h: 2 },
        ] },
        { name: "Notes", items: [{ kind: "notes", x: 0, y: 0, w: 5, h: 3 }] },
      ];
    },
    load() {
      const saved = this.store.load();
      const ok = saved && Array.isArray(saved.pages) && saved.pages.length;
      this.pages = ok
        ? saved.pages.map((p) => ({ name: String(p.name || "Page"),
            items: (p.items || []).filter((i) => this.known(i.kind)).map((i) => this.fit({ ...i, id: i.id || uid() })) }))
        : (this.starterPages ? this.starterPages() : this.starter()).map((p) => ({ name: p.name,
            items: this.starterFit(p.items).map((i) => ({ id: uid(), ...i })) }));
      this.page = ok ? Math.min(Math.max(0, saved.page | 0), this.pages.length - 1) : 0;
      // a hand-edited or older layout may overlap; step cards down until none do
      this.pages.forEach((p) => this.settle(p.items));
    },
    save() { this.store.save({ pages: this.pages, page: this.page }); },
    // a starter widget this system has no numbers for (a Forge release cuts
    // some) leaves no hole: the cards after it in its row move left
    starterFit(items) {
      const keep = items.filter((i) => this.KINDS[i.kind]).map((i) => ({ ...i }));
      for (const gone of items.filter((i) => !this.KINDS[i.kind]))
        for (const i of keep) if (i.y === gone.y && i.x > gone.x) i.x -= gone.w;
      return keep;
    },
    // inside the grid, and no smaller than the widget allows
    fit(i) {
      const [mw0, mh] = (this.KINDS[i.kind] || this.HIDDEN[i.kind] || {}).min || [1, 1];
      const mw = Math.ceil((mw0 * this.cols) / COLS); // sizes are given for the hub's 12 columns
      i.w = Math.max(mw, Math.min(this.cols, Math.round(i.w) || mw));
      i.h = Math.max(mh, Math.min(8, Math.round(i.h) || mh));
      i.x = Math.max(0, Math.min(this.cols - i.w, Math.round(i.x) || 0));
      i.y = Math.max(0, Math.round(i.y) || 0);
      return i;
    },
    // the first free place a w×h card fits, row by row from the top left, so
    // added widgets fill the gaps instead of piling up in one column
    spot(items, w, h) {
      const bottom = items.reduce((m, i) => Math.max(m, i.y + i.h), 0);
      for (let y = 0; y <= bottom; y++) {
        for (let x = 0; x + w <= this.cols; x++) {
          const probe = { x, y, w, h };
          if (!items.some((i) => this.overlaps(i, probe))) return { x, y };
        }
      }
      return { x: 0, y: bottom };
    },
    overlaps(a, b) { return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h; },
    // `moved` keeps its place; the others step down until nothing overlaps
    settle(items, moved) {
      const placed = moved ? [moved] : [];
      items.filter((i) => i !== moved).sort((a, b) => a.y - b.y || a.x - b.x).forEach((i) => {
        while (placed.some((p) => this.overlaps(p, i))) i.y++;
        placed.push(i);
      });
    },
    place(item, x, y, w, h) {
      Object.assign(item, { x, y, w: w ?? item.w, h: h ?? item.h });
      this.fit(item);
      this.settle(this.pages[this.page].items, item);
      this.save();
      this.render();
    },
    add(kind) {
      const spec = this.KINDS[kind];
      if (!spec) return null;
      const items = this.pages[this.page].items;
      const [w0, h] = spec.size || [3, 2];
      const w = Math.round((w0 * this.cols) / COLS);
      const item = this.fit({ id: uid(), kind, x: 0, y: 0, w, h });
      Object.assign(item, this.spot(items, item.w, item.h));
      items.push(item);
      this.save();
      this.render();
      return item;
    },
    remove(id) {
      const p = this.pages[this.page];
      p.items = p.items.filter((i) => i.id !== id);
      this.save();
      this.render();
    },
    addPage() { this.pages.push({ name: `Page ${this.pages.length + 1}`, items: [] }); this.go(this.pages.length - 1); },
    removePage() {
      if (this.pages.length < 2) return;
      this.pages.splice(this.page, 1);
      this.go(Math.min(this.page, this.pages.length - 1));
    },
    go(n) {
      this.page = (n + this.pages.length) % this.pages.length;
      this.save();
      this.render();
    },
    edit(on) { this.editing = !!on; this.render(); },

    mount(root, ctx) {
      if (CSS && !document.querySelector("link[data-forge-widgets]")) {
        const link = el("link");
        link.rel = "stylesheet"; link.href = CSS; link.dataset.forgeWidgets = "1";
        document.head.append(link);
      }
      this.root = root;
      this.ctx = ctx || {};
      this.load();
      // a sideways two-finger swipe or wheel turns the page: a touchpad sends
      // many small steps, so they add up over one swipe, and one swipe (its
      // glide after the fingers lift included) turns one page
      const swipe = { sum: 0, last: 0, done: false };
      root.addEventListener("wheel", (e) => {
        if (this.pages.length < 2) return;
        const now = Date.now();
        if (now - swipe.last > 250) { swipe.sum = 0; swipe.done = false; }
        swipe.last = now;
        const unit = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? innerWidth : 1;
        const dx = e.deltaX * unit, dy = e.deltaY * unit;
        if (swipe.done || Math.abs(dx) <= Math.abs(dy)) return;
        swipe.sum += dx;
        if (Math.abs(swipe.sum) < 60) return;
        swipe.done = true;
        this.go(this.page + (swipe.sum > 0 ? 1 : -1));
      }, { passive: true });
      document.addEventListener("keydown", (e) => {
        if (e.key !== "Escape") return;
        if (this.relaxing) { e.preventDefault(); e.stopPropagation(); this.relax(false); }
        else if (this.editing) { e.preventDefault(); e.stopPropagation(); this.edit(false); }
      }, true);
      this.apply(this.theme);
      this.render();
      // one timer for both: the always-jobs (alarms, the Activity history)
      // run hidden or not; the cards' updates only while they can be seen
      setInterval(() => {
        this.always.forEach((f) => { try { f(); } catch (e) { /* the next second tries again */ } });
        this.tick();
      }, 1000);
      const wake = () => this.tick();
      document.addEventListener("visibilitychange", wake);
      document.addEventListener("forge-visible", wake);
      document.addEventListener("forge-power", wake);
    },
    apply(theme) {
      this.theme = theme || {};
      this.on = this.theme.widgets !== false;
      if (!this.root) return;
      this.root.hidden = !this.on;
      this.root.dataset.look = this.theme.widget_look === "glass" ? "glass" : "material";
      // reduced power tier: the glass cards drop their backdrop blur (redrawn
      // every frame while particles move under it) for a thicker flat fill
      this.root.classList.toggle("wg-lowpower", this.tier() !== "full");
      const scale = Math.max(0.5, Math.min(2, Number(this.theme.widget_scale) || 1));
      this.root.style.setProperty("--widget-row", `${Math.round(64 * scale)}px`);
      document.documentElement.style.setProperty("--breath", `${this.breath()}s`);
    },
    // widgets update once a second, and only while they can be seen
    visible() {
      return this.on && !!this.root && !document.hidden && !document.body.classList.contains("in-app")
        && !(window.ForgeVisible && window.ForgeVisible.on === false) && this.tier() !== "paused";
    },
    // the shared power tier (shared/forge_power.js), as the page's theme
    // carries it (power_tier, from ForgePower.apply()); full when absent
    tier() { const t = String(this.theme.power_tier || "").split(":")[0].trim(); return t === "reduced" || t === "paused" ? t : "full"; },
    tick() {
      if (!this.visible()) return;
      this.updates.forEach((f) => { try { f(); } catch (e) { /* one widget must not stop the rest */ } });
    },

    render() {
      const root = this.root;
      if (!root) return;
      root.replaceChildren();
      root.classList.toggle("editing", this.editing);
      const head = el("div", "wg-head"), dots = el("div", "wg-dots"), tools = el("div", "wg-tools");
      this.pages.forEach((p, n) => {
        const d = button("wg-dot" + (n === this.page ? " on" : ""), "", `Widget page ${n + 1}: ${p.name}`);
        d.onclick = () => this.go(n);
        dots.append(d);
      });
      if (this.editing) {
        // drawn by the page, not a <select>: the system draws a select's list,
        // and on the desktop it came out white text on white (2026-09-29)
        const pick = el("div", "wg-addwrap"), open = button("wg-add", "+ Add a widget", "Add a widget to this page");
        const list = el("div", "wg-addlist");
        list.hidden = true;
        list.setAttribute("role", "menu");
        Object.entries(this.KINDS).forEach(([k, s]) => {
          const b = button("wg-addkind", `${s.icon || ""} ${s.label}`.trim());
          b.dataset.kind = k;
          b.setAttribute("role", "menuitem");
          b.onclick = () => { list.hidden = true; this.add(k); };
          list.append(b);
        });
        open.setAttribute("aria-haspopup", "menu");
        open.onclick = () => { list.hidden = !list.hidden; if (!list.hidden) list.firstChild.focus(); };
        pick.addEventListener("focusout", (e) => { if (!pick.contains(e.relatedTarget)) list.hidden = true; });
        pick.append(open, list);
        const page = button("wg-btn", "+ Page", "Add a widget page");
        page.onclick = () => this.addPage();
        const drop = button("wg-btn", "Remove page", "Remove this widget page");
        drop.disabled = this.pages.length < 2;
        drop.onclick = () => this.removePage();
        const done = button("wg-btn primary", "Done", "Stop editing (Esc)");
        done.onclick = () => this.edit(false);
        tools.append(pick, page, drop, done);
      } else {
        const b = button("wg-btn ghost", "✎ Edit", "Move, resize, add and remove widgets, and add pages");
        b.onclick = () => this.edit(true);
        tools.append(b);
      }
      head.append(dots, tools);
      const grid = el("div", "wg-grid");
      // a page turn eases the new cards in (once; transform and opacity only)
      if (this.shownPage !== undefined && this.shownPage !== this.page) grid.classList.add("wg-enter");
      this.shownPage = this.page;
      if (this.cols !== COLS) grid.style.gridTemplateColumns = `repeat(${this.cols}, minmax(0, 1fr))`;
      this.updates = [];
      const items = this.pages[this.page].items.filter((i) => this.KINDS[i.kind]);
      items.forEach((item) => grid.append(this.card(item, grid)));
      if (!items.length) grid.append(el("div", "wg-empty", this.editing ? "Add a widget from the menu above." : "An empty page: ✎ Edit adds widgets."));
      root.append(head, grid);
      this.tick();
      document.dispatchEvent(new CustomEvent("forge-widgets-layout"));
    },
    card(item, grid) {
      const spec = this.KINDS[item.kind];
      const card = el("section", `wg wg-${item.kind}`);
      card.dataset.id = item.id;
      const colour = this.opt(item, "colour"), look = this.opt(item, "look");
      if (/^#[0-9a-f]{6}$/i.test(colour || "")) {
        card.style.setProperty("--accent", colour);
        card.style.setProperty("--on-accent", onColour(colour));
      }
      if (look) card.dataset.look = look;
      card.setAttribute("aria-label", spec.label);
      card.style.gridColumn = `${item.x + 1} / span ${item.w}`;
      card.style.gridRow = `${item.y + 1} / span ${item.h}`;
      const body = el("div", "wg-body");
      card.append(body);
      try {
        const up = spec.render(body, this.ctx, item, this);
        if (typeof up === "function") this.updates.push(up);
      } catch (e) {
        body.textContent = `${spec.label} could not start.`;
      }
      if (this.editing) {
        const x = button("wg-x", "×", `Remove ${spec.label}`);
        x.onclick = () => this.remove(item.id);
        const cfg = button("wg-cfg", "⚙", `${spec.label}: colour, look and what it shows`);
        cfg.onclick = (e) => { e.stopPropagation(); this.configure(card, item); };
        const grip = el("div", "wg-grip");
        grip.title = "Drag to resize";
        card.append(x, cfg, grip);
        this.drag(card, card, item, grid, false);
        this.drag(grip, card, item, grid, true);
      }
      return card;
    },
    // the ⚙ panel: every option as a control, over the card
    configure(card, item) {
      const open = card.querySelector(".wg-opts");
      if (open) { open.remove(); return; }
      const box = el("div", "wg-opts");
      box.addEventListener("pointerdown", (e) => e.stopPropagation());
      box.addEventListener("keydown", (e) => e.stopPropagation());
      box.append(el("div", "wg-opts-head", `${this.KINDS[item.kind].label}`));
      for (const o of this.options(item.kind)) {
        const row = el("label", "wg-opt"), name = el("span", null, o.label);
        let input;
        const v = this.opt(item, o.key);
        if (o.kind === "choice") {
          input = el("select");
          o.choices.forEach((c) => input.append(new Option((o.labels || {})[c] ?? c, c)));
          input.value = v ?? "";
          input.onchange = () => this.setOpt(item, o.key, input.value, true);
        } else if (o.kind === "bool") {
          input = el("input");
          input.type = "checkbox";
          input.checked = !!v;
          input.onchange = () => this.setOpt(item, o.key, input.checked, true);
        } else if (o.kind === "number") {
          input = el("input");
          input.type = "number";
          Object.assign(input, { min: o.min, max: o.max, step: o.step || 1 });
          input.value = v;
          input.onchange = () => this.setOpt(item, o.key, Math.max(o.min, Math.min(o.max, Number(input.value) || o.default)), true);
        } else if (o.kind === "date") {
          input = el("input");
          input.type = "date";
          input.value = v || "";
          input.onchange = () => this.setOpt(item, o.key, input.value, true);
        } else if (o.kind === "colour") {
          input = el("span", "wg-colours");
          const pick = el("input"), auto = button("wg-btn", "Auto", "The wallpaper's colour");
          pick.type = "color";
          pick.value = /^#[0-9a-f]{6}$/i.test(v || "") ? v : "#8ab4ff";
          pick.oninput = () => this.setOpt(item, "colour", pick.value, true);
          auto.onclick = (e) => { e.preventDefault(); this.setOpt(item, "colour", "", true); };
          input.append(pick, auto);
        } else {
          input = el("input");
          input.type = "text";
          input.value = v || "";
          input.placeholder = o.placeholder || "";
          input.onchange = () => this.setOpt(item, o.key, input.value.trim().slice(0, 200), true);
        }
        row.append(name, input);
        box.append(row);
      }
      const done = button("wg-btn primary", "Done", "Close these options");
      done.onclick = () => { box.remove(); this.render(); };
      box.append(done);
      card.append(box);
    },
    // drag to move (or, on the corner grip, to resize), snapping to cells
    drag(handle, card, item, grid, resize) {
      handle.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        if (!resize && e.target.closest(".wg-x, .wg-grip, .wg-cfg, .wg-opts")) return;
        e.preventDefault();
        e.stopPropagation();
        const r = grid.getBoundingClientRect();
        const cw = (r.width + GAP) / this.cols;
        const rh = (parseFloat(getComputedStyle(this.root).getPropertyValue("--widget-row")) || 64) + GAP;
        const cx = e.clientX, cy = e.clientY, i0 = { ...item };
        const target = (ev) => {
          const dx = Math.round((ev.clientX - cx) / cw), dy = Math.round((ev.clientY - cy) / rh);
          return this.fit(resize ? { ...i0, w: i0.w + dx, h: i0.h + dy } : { ...i0, x: i0.x + dx, y: i0.y + dy });
        };
        card.classList.add("moving");
        try { handle.setPointerCapture(e.pointerId); } catch (x) { /* a synthetic pointer */ }
        const move = (ev) => {
          const t = target(ev);
          card.style.gridColumn = `${t.x + 1} / span ${t.w}`;
          card.style.gridRow = `${t.y + 1} / span ${t.h}`;
        };
        const up = (ev) => {
          ["pointermove", "pointerup", "pointercancel"].forEach((type, k) => handle.removeEventListener(type, k ? up : move));
          const t = target(ev);
          this.place(item, t.x, t.y, t.w, t.h);
        };
        handle.addEventListener("pointermove", move);
        handle.addEventListener("pointerup", up);
        handle.addEventListener("pointercancel", up);
      });
    },

    // --- Relax: full-screen box breathing (in, hold, out, hold) ---------------
    PHASES: ["Breathe in", "Hold", "Breathe out", "Hold"],
    breath() { return Math.max(2, Math.min(10, Number(this.theme.breath_seconds) || 4)); },
    phase(ms) {
      const s = this.breath() * 1000, n = Math.floor(ms / s);
      return { n: n % 4, word: this.PHASES[n % 4], left: Math.ceil((s - (ms % s)) / 1000) };
    },
    relax(on) {
      this.relaxing = !!on;
      let box = document.getElementById("relax");
      if (on && !box) {
        box = el("div");
        box.id = "relax";
        box.setAttribute("role", "dialog");
        box.setAttribute("aria-label", "Relax");
        box.append(el("div", "rx-ring"), el("div", "rx-word"), el("div", "rx-count"), el("div", "rx-hint", "Esc or a click to leave"));
        box.firstChild.append(el("div", "rx-ball"));
        box.onclick = () => this.relax(false);
        document.body.append(box);
      }
      clearInterval(this.rxTimer);
      if (box) box.classList.toggle("on", this.relaxing);
      document.body.classList.toggle("relaxing", this.relaxing);
      if (!this.relaxing) return;
      this.relaxStart = Date.now();
      this.relaxTick();
      this.rxTimer = setInterval(() => this.relaxTick(), 200);
    },
    relaxTick() {
      const box = document.getElementById("relax");
      if (!box) return;
      const p = this.phase(Date.now() - this.relaxStart);
      box.querySelector(".rx-word").textContent = p.word;
      box.querySelector(".rx-count").textContent = String(p.left);
      box.dataset.phase = String(p.n);
    },
  };

  // --- the first five widgets (B20) -------------------------------------------

  W.define("clock", {
    label: "Clock", icon: "🕒", size: [3, 2], min: [2, 2],
    options: [
      { key: "face", label: "Face", kind: "choice", default: "digital", choices: ["digital", "analogue"],
        labels: { digital: "Digital", analogue: "Analogue" } },
      { key: "seconds", label: "Seconds hand", kind: "bool", default: true },
    ],
    render(body, ctx, item, w) {
      const time = el("div", "wg-big"), date = el("div", "wg-sub"), meter = el("div", "wg-meter"), fill = el("i");
      meter.append(fill);
      meter.title = "How much of the day has gone";
      if (w && w.opt(item, "face") === "analogue") {
        // hands turned by transform once a second: no canvas, no repaint of the card
        const dial = el("div", "wg-dial"), hh = el("i", "wg-hand h"), mm = el("i", "wg-hand m"), ss = el("i", "wg-hand s");
        ss.hidden = w.opt(item, "seconds") === false;
        dial.append(hh, mm, ss, el("b"));
        const side = el("div", "wg-np-text");
        side.append(time, date, meter);
        time.classList.add("small");
        body.classList.add("wg-analogue");
        body.append(dial, side);
        return () => {
          const now = new Date(), sec = now.getSeconds(), min = now.getMinutes() + sec / 60, hr = (now.getHours() % 12) + min / 60;
          hh.style.transform = `rotate(${hr * 30}deg)`;
          mm.style.transform = `rotate(${min * 6}deg)`;
          ss.style.transform = `rotate(${sec * 6}deg)`;
          const h24 = !ctx.clock24 || ctx.clock24();
          time.textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: !h24 });
          const done = Math.round(((now.getHours() * 60 + now.getMinutes()) / 1440) * 100);
          date.textContent = now.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
          fill.style.width = `${done}%`;
        };
      }
      body.append(time, date, meter);
      return () => {
        const now = new Date(), h24 = !ctx.clock24 || ctx.clock24();
        time.textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: !h24 });
        const done = Math.round(((now.getHours() * 60 + now.getMinutes()) / 1440) * 100);
        date.textContent = `${now.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })} · ${done}% of the day`;
        fill.style.width = `${done}%`;
      };
    },
  });

  W.define("nowplaying", {
    label: "Now playing", icon: "🎵", size: [4, 2], min: [3, 2],
    render(body, ctx) {
      const art = el("div", "wg-art", "♪"), text = el("div", "wg-np-text");
      const title = el("div", "wg-title"), who = el("div", "wg-sub"), row = el("div", "wg-row");
      const prev = button("wg-round", "⏮", "Previous"), play = button("wg-round primary", "▶", "Play or pause"), next = button("wg-round", "⏭", "Next");
      prev.onclick = () => ctx.media && ctx.media("prev");
      play.onclick = () => ctx.media && ctx.media("toggle");
      next.onclick = () => ctx.media && ctx.media("next");
      row.append(prev, play, next);
      text.append(title, who, row);
      body.append(art, text);
      let asked = 0;
      return () => {
        // every player on the laptop, not only Forge's: desktop players
        // (Firefox, QBZ, mpv...) are asked for again every 3 s while shown
        if (ctx.refresh && Date.now() - asked > 3000) { asked = Date.now(); ctx.refresh(); }
        const m = ctx.now && ctx.now();
        title.textContent = m ? m.title || "Something is playing" : "Nothing playing";
        who.textContent = m ? [m.artist, m.app].filter(Boolean).join(" · ") : "Play something in any app or player";
        row.hidden = !m;
        play.textContent = m && m.state === "playing" ? "⏸" : "▶";
        prev.hidden = !!m && m.canPrev === false;
        next.hidden = !!m && m.canNext === false;
        const pic = m && m.artwork ? `url("${String(m.artwork).replace(/"/g, "%22")}")` : "";
        if (art.style.backgroundImage !== pic) art.style.backgroundImage = pic;
        art.textContent = pic ? "" : "♪";
      };
    },
  });

  W.define("system", {
    label: "System", icon: "📊", size: [3, 2], min: [3, 2],
    render(body, ctx, item, w) {
      const gauges = el("div", "wg-gauges"), note = el("div", "wg-sub", "Waiting for the numbers…");
      const make = (label) => {
        const wrap = el("div", "wg-gauge-wrap"), g = el("div", "wg-gauge"), b = el("b", null, "–");
        g.append(b);
        wrap.append(g, el("span", null, label));
        gauges.append(wrap);
        return { g, b };
      };
      const cpu = make("CPU"), mem = make("Memory"), bat = make("Battery");
      const more = el("div", "wg-sub");
      more.hidden = true;
      body.append(gauges, note, more);
      const show = (x, v, text, hot) => {
        x.g.style.setProperty("--v", String(v ?? 0));
        x.b.textContent = v == null ? "–" : text;
        x.g.classList.toggle("hot", !!hot);
      };
      return () => {
        const s = ctx.stats && ctx.stats();
        note.hidden = !!s;
        if (!s) return;
        const c = pct(s.cpu), m = pct(s.mem), b = pct(s.battery), charging = s.charging === true;
        show(cpu, c, `${c}%`, c >= 85);
        show(mem, m, `${m}%`, m >= 85);
        show(bat, b, `${charging ? "⚡" : ""}${b}%`, !charging && b != null && b <= 20);
        more.hidden = !w.opt(item, "more");
        if (!more.hidden) {
          const sys = (diskList(s) || []).find((d) => d.mount === "/"), n = (s.net && typeof s.net === "object" && s.net) || {};
          more.textContent = [num(n.down) != null ? `↓ ${rate(num(n.down))} ↑ ${rate(num(n.up))}` : "", sys && num(sys.free_gb) != null ? `${num(sys.free_gb)} GB free` : "",
            s.plugged === false ? "on battery" : s.plugged ? "plugged in" : ""].filter(Boolean).join(" · ");
        }
      };
    },
  });

  W.define("notes", {
    label: "Notes", icon: "📝", size: [4, 3], min: [2, 2],
    render(body, ctx, item, w) {
      const area = el("textarea");
      area.placeholder = "A note to yourself…";
      area.spellcheck = true;
      area.setAttribute("aria-label", "Notes");
      // the text lives in the layout (W.store), so the desktop's notes are
      // kept in its own file; older hub notes are read from localStorage once
      const key = `forge.widget.notes.${item.id}`;
      let text = w.opt(item, "text");
      if (text == null) { try { text = localStorage.getItem(key) || ""; } catch (e) { text = ""; } }
      area.value = text;
      let later = 0;
      area.addEventListener("input", () => { clearTimeout(later); later = setTimeout(() => w.setOpt(item, "text", area.value, true), 400); });
      // typing here is for the note, not the hub's type-to-search
      area.addEventListener("keydown", (e) => e.stopPropagation());
      body.append(area);
    },
  });

  W.define("breathe", {
    label: "Relax", icon: "🌙", size: [2, 2], min: [2, 2],
    render(body, ctx, item, w) {
      const orb = el("div", "wg-orb"), go = button("wg-btn primary", "Relax", "Full-screen box breathing (Esc leaves)");
      go.onclick = () => w.relax(true);
      body.append(orb, el("div", "wg-sub", "Box breathing"), go);
    },
  });

  // --- S25 (2026-09-28): the widgets that were planned and never made -----------
  // the owner: "actually make the new widgets that never got made". Each is one
  // define(); what it shows is in its options (the ⚙ in edit mode).

  const pad2 = (n) => String(n).padStart(2, "0");
  const dayKey = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  const rate = (b) => (b == null ? "–" : b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB/s` : b >= 1e3 ? `${Math.round(b / 1e3)} KB/s` : `${b} B/s`);
  // stats as numbers: a missing or broken field reads as null, never NaN or text
  const num = (v) => (v == null || v === "" || typeof v === "boolean" || !Number.isFinite(Number(v)) ? null : Number(v));
  const pct = (v) => { const n = num(v); return n == null ? null : Math.max(0, Math.min(100, Math.round(n))); };
  const diskList = (s) => (Array.isArray(s && s.disks) ? s.disks.filter((d) => d && typeof d === "object" && num(d.used) != null) : null);
  const clockOf = (sec) => { sec = Math.max(0, Math.floor(sec || 0)); return `${Math.floor(sec / 60)}:${pad2(sec % 60)}`; };

  // weather from open-meteo.com (no key, no account); a place is looked up once
  const WMO = {
    0: ["☀️", "Clear"], 1: ["🌤️", "Mostly clear"], 2: ["⛅", "Partly cloudy"], 3: ["☁️", "Cloudy"],
    45: ["🌫️", "Fog"], 48: ["🌫️", "Icy fog"], 51: ["🌦️", "Light drizzle"], 53: ["🌦️", "Drizzle"], 55: ["🌧️", "Heavy drizzle"],
    56: ["🌧️", "Freezing drizzle"], 57: ["🌧️", "Freezing drizzle"], 61: ["🌦️", "Light rain"], 63: ["🌧️", "Rain"], 65: ["🌧️", "Heavy rain"],
    66: ["🌧️", "Freezing rain"], 67: ["🌧️", "Freezing rain"], 71: ["🌨️", "Light snow"], 73: ["🌨️", "Snow"], 75: ["❄️", "Heavy snow"],
    77: ["🌨️", "Snow grains"], 80: ["🌦️", "Showers"], 81: ["🌧️", "Showers"], 82: ["⛈️", "Heavy showers"], 85: ["🌨️", "Snow showers"],
    86: ["❄️", "Snow showers"], 95: ["⛈️", "Thunderstorm"], 96: ["⛈️", "Thunder and hail"], 99: ["⛈️", "Thunder and hail"],
  };
  // the city in the laptop's time zone ("Europe/Paris" -> "Paris"), so
  // the card shows weather before anyone types a place
  const tzCity = () => {
    try {
      const parts = (Intl.DateTimeFormat().resolvedOptions().timeZone || "").split("/");
      return parts.length > 1 && parts[0] !== "Etc" ? parts[parts.length - 1].replace(/_/g, " ") : "";
    } catch (e) { return ""; }
  };
  const Weather = {
    FRESH: 20 * 60 * 1000,
    async json(url) {
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    },
    async place(name) {
      const key = `geo:${name.toLowerCase()}`;
      if (W.cache[key]) return W.cache[key];
      const d = await this.json(`https://geocoding-api.open-meteo.com/v1/search?count=1&language=en&name=${encodeURIComponent(name)}`);
      const hit = (d.results || [])[0];
      if (!hit) throw new Error(`No place called ${name}`);
      return (W.cache[key] = { lat: hit.latitude, lon: hit.longitude, name: [hit.name, hit.country_code].filter(Boolean).join(", ") });
    },
    // the forecast for a place, fetched at most every 20 minutes; null while it comes
    get(name, units) {
      const key = `wx:${name.toLowerCase()}:${units}`, c = W.cache[key];
      if (c && (c.busy || Date.now() - c.at < (c.error ? 60000 : this.FRESH))) return c;
      const next = W.cache[key] = Object.assign({}, c, { busy: true });
      (async () => {
        try {
          const p = await this.place(name), f = units === "f";
          const d = await this.json(`https://api.open-meteo.com/v1/forecast?latitude=${p.lat}&longitude=${p.lon}`
            + "&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day"
            + "&hourly=temperature_2m,weather_code&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            + `&forecast_days=6&timezone=auto${f ? "&temperature_unit=fahrenheit&wind_speed_unit=mph" : ""}`);
          W.cache[key] = { at: Date.now(), place: p.name, d };
        } catch (e) {
          W.cache[key] = Object.assign({}, next, { busy: false, at: Date.now(), error: String(e.message || e) });
        }
      })();
      return next;
    },
  };

  W.define("weather", {
    label: "Weather", icon: "🌤️", size: [3, 2], min: [2, 2],
    options: [
      { key: "place", label: "Place", kind: "text", default: "", placeholder: "A town or city" },
      { key: "show", label: "Shows", kind: "choice", default: "now", choices: ["now", "hours", "days"],
        labels: { now: "Now only", hours: "Now and the next hours", days: "Now and the next days" } },
    ],
    render(body, ctx, item, w) {
      const chosen = w.opt(item, "place"), place = chosen || tzCity();
      if (!place) {
        const ask = el("input", "wg-ask");
        ask.placeholder = "Type a town, then Enter";
        ask.setAttribute("aria-label", "Weather place");
        ask.addEventListener("keydown", (e) => {
          e.stopPropagation();
          if (e.key === "Enter" && ask.value.trim()) w.setOpt(item, "place", ask.value.trim().slice(0, 80));
        });
        body.append(el("div", "wg-sub", "Weather for where?"), ask);
        return null;
      }
      const top = el("div", "wg-wx-top"), icon = el("div", "wg-wx-icon", "…"), temp = el("div", "wg-big", "–");
      const what = el("div", "wg-sub"), where = el("div", "wg-sub"), strip = el("div", "wg-wx-strip");
      const text = el("div", "wg-wx-text");
      text.append(temp, what);
      top.append(icon, text);
      body.append(top, where, strip);
      let drawn = 0;
      return () => {
        const units = w.theme.weather_units === "f" ? "f" : "c";
        const c = Weather.get(place, units);
        if (c.error && !c.d) { what.textContent = c.error.startsWith("No place") ? c.error : "No weather: offline?"; where.textContent = place; return; }
        if (!c.d || c.at === drawn) return;
        drawn = c.at;
        const d = c.d, now = d.current || {}, deg = units === "f" ? "°F" : "°C";
        const [emo, word] = WMO[now.weather_code] || ["🌡️", ""];
        icon.textContent = now.is_day === 0 && now.weather_code <= 1 ? "🌙" : emo;
        temp.textContent = `${Math.round(now.temperature_2m)}${deg}`;
        what.textContent = `${word} · feels ${Math.round(now.apparent_temperature)}° · ${Math.round(now.wind_speed_10m)} ${units === "f" ? "mph" : "km/h"}`;
        where.textContent = `${c.place} · ${now.relative_humidity_2m}% humid${chosen ? "" : " · from the time zone, ⚙ to change"}`;
        strip.replaceChildren();
        const show = w.opt(item, "show");
        if (show === "hours" && d.hourly) {
          const at = d.hourly.time.findIndex((t) => t >= (now.time || ""));
          for (let i = Math.max(0, at + 1); i < Math.min(d.hourly.time.length, at + 1 + 6 * 2); i += 2) {
            const cell = el("div", "wg-wx-cell");
            cell.append(el("span", null, d.hourly.time[i].slice(11, 16)), el("b", null, (WMO[d.hourly.weather_code[i]] || ["·"])[0]),
              el("span", null, `${Math.round(d.hourly.temperature_2m[i])}°`));
            strip.append(cell);
          }
        } else if (show === "days" && d.daily) {
          d.daily.time.slice(1, 6).forEach((t, k) => {
            const i = k + 1, cell = el("div", "wg-wx-cell");
            cell.title = `${d.daily.precipitation_probability_max[i]}% chance of rain`;
            cell.append(el("span", null, new Date(`${t}T12:00`).toLocaleDateString([], { weekday: "short" })),
              el("b", null, (WMO[d.daily.weather_code[i]] || ["·"])[0]),
              el("span", null, `${Math.round(d.daily.temperature_2m_max[i])}° ${Math.round(d.daily.temperature_2m_min[i])}°`));
            strip.append(cell);
          });
        }
        strip.hidden = !strip.childElementCount;
      };
    },
  });

  W.define("player", {
    label: "Media player", icon: "🎧", size: [5, 3], min: [4, 3],
    options: [
      { key: "art", label: "Show the cover", kind: "bool", default: true },
      { key: "volume", label: "Volume slider", kind: "bool", default: true },
    ],
    render(body, ctx, item, w) {
      const art = el("div", "wg-art big", "♪"), side = el("div", "wg-np-text");
      const title = el("div", "wg-title"), who = el("div", "wg-sub");
      const seekRow = el("div", "wg-seek"), at = el("span", null, "0:00"), seek = el("input"), len = el("span", null, "0:00");
      seek.type = "range"; seek.min = 0; seek.max = 1; seek.step = 1; seek.value = 0; seek.setAttribute("aria-label", "Position");
      seekRow.append(at, seek, len);
      const row = el("div", "wg-row");
      const prev = button("wg-round", "⏮", "Previous"), play = button("wg-round primary", "▶", "Play or pause"), next = button("wg-round", "⏭", "Next");
      const go = button("wg-btn ghost", "Go to player", "Show the app or window that is playing");
      row.append(prev, play, next, go);
      const volRow = el("div", "wg-vol"), vol = el("input");
      vol.type = "range"; vol.min = 0; vol.max = 100; vol.step = 1; vol.setAttribute("aria-label", "Volume");
      volRow.append(el("span", null, "🔊"), vol);
      if (w.opt(item, "art") === false) art.hidden = true;
      if (w.opt(item, "volume") === false) volRow.hidden = true;
      side.append(title, who, seekRow, row, volRow);
      body.append(art, side);
      const media = (op, v) => { if (op === "seek" && ctx.seek) ctx.seek(v); else if (op === "volume" && ctx.volume) ctx.volume(v); else if (ctx.media) ctx.media(op); };
      prev.onclick = () => media("prev");
      play.onclick = () => media("toggle");
      next.onclick = () => media("next");
      go.onclick = () => media("go");
      let seeking = false, voling = 0, asked = 0;
      seek.addEventListener("pointerdown", () => { seeking = true; });
      seek.addEventListener("change", () => { seeking = false; media("seek", Number(seek.value)); });
      vol.addEventListener("input", () => { voling = Date.now(); media("volume", Number(vol.value)); });
      return () => {
        if (ctx.refresh && Date.now() - asked > 3000) { asked = Date.now(); ctx.refresh(); }
        const m = ctx.now && ctx.now();
        title.textContent = m ? m.title || "Something is playing" : "Nothing playing";
        who.textContent = m ? [m.artist, m.app].filter(Boolean).join(" · ") : "Play something in any app or player";
        [seekRow, row].forEach((x) => (x.hidden = !m));
        // a player the desktop can't set the volume of or bring forward
        // (Windows' media controls) says so with canVolume / canRaise false
        volRow.hidden = !m || w.opt(item, "volume") === false || m.canVolume === false;
        if (!m) return;
        go.hidden = m.canRaise === false;
        play.textContent = m.state === "playing" ? "⏸" : "▶";
        prev.hidden = m.canPrev === false;
        next.hidden = m.canNext === false;
        const dur = Number(m.duration) || 0, pos = ctx.position ? ctx.position(m) : Number(m.current) || 0;
        seekRow.hidden = !dur;
        seek.disabled = m.canSeek === false;
        if (!seeking && dur) { seek.max = Math.round(dur); seek.value = Math.round(pos); }
        at.textContent = clockOf(pos); len.textContent = clockOf(dur);
        if (Date.now() - voling > 1500 && m.volume != null) vol.value = Math.round(m.volume <= 1 ? m.volume * 100 : m.volume);
        const pic = m.artwork ? `url("${String(m.artwork).replace(/"/g, "%22")}")` : "";
        if (art.style.backgroundImage !== pic) art.style.backgroundImage = pic;
        art.textContent = pic ? "" : "♪";
      };
    },
  });

  W.define("calendar", {
    label: "Calendar", icon: "📅", size: [4, 4], min: [3, 4],
    options: [
      { key: "week", label: "Weeks start on", kind: "choice", default: "mon", choices: ["mon", "sun"], labels: { mon: "Monday", sun: "Sunday" } },
    ],
    render(body, ctx, item, w) {
      const head = el("div", "wg-cal-head"), back = button("wg-round small", "‹", "Last month"), fwd = button("wg-round small", "›", "Next month");
      const name = el("div", "wg-title"), grid = el("div", "wg-cal"), note = el("input", "wg-ask");
      head.append(back, name, fwd);
      note.setAttribute("aria-label", "A note for the day picked");
      body.append(head, grid, note);
      const today = new Date();
      let month = new Date(today.getFullYear(), today.getMonth(), 1), picked = dayKey(today), shownDay = "";
      const marks = () => w.opt(item, "marks") || {};
      const draw = () => {
        grid.replaceChildren();
        name.textContent = month.toLocaleDateString([], { month: "long", year: "numeric" });
        const mon = w.opt(item, "week") !== "sun";
        const names = [...Array(7)].map((_, i) => new Date(2024, 0, (mon ? 1 : 7) + i).toLocaleDateString([], { weekday: "narrow" }));
        names.forEach((n) => grid.append(el("span", "wg-cal-dow", n)));
        const lead = (month.getDay() + 7 - (mon ? 1 : 0)) % 7;
        for (let i = 0; i < lead; i++) grid.append(el("span"));
        const days = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
        for (let d = 1; d <= days; d++) {
          const date = new Date(month.getFullYear(), month.getMonth(), d), key = dayKey(date);
          const b = button("wg-cal-day", String(d), date.toDateString());
          if (key === dayKey(new Date())) b.classList.add("today");
          if (key === picked) b.classList.add("on");
          if (marks()[key]) { b.classList.add("marked"); b.title += ` · ${marks()[key]}`; }
          b.onclick = () => { picked = key; draw(); };
          grid.append(b);
        }
        note.placeholder = `A note for ${new Date(`${picked}T12:00`).toLocaleDateString([], { day: "numeric", month: "short" })}, then Enter`;
        note.value = marks()[picked] || "";
      };
      back.onclick = () => { month = new Date(month.getFullYear(), month.getMonth() - 1, 1); draw(); };
      fwd.onclick = () => { month = new Date(month.getFullYear(), month.getMonth() + 1, 1); draw(); };
      note.addEventListener("keydown", (e) => {
        e.stopPropagation();
        if (e.key !== "Enter") return;
        const m = Object.assign({}, marks());
        if (note.value.trim()) m[picked] = note.value.trim().slice(0, 140); else delete m[picked];
        w.setOpt(item, "marks", m, true);
        draw();
      });
      draw();
      // past midnight the "today" ring moves
      return () => { const k = dayKey(new Date()); if (k !== shownDay) { shownDay = k; if (!note.matches(":focus")) draw(); } };
    },
  });

  W.define("disk", {
    label: "Disks", icon: "💽", size: [3, 2], min: [3, 2],
    options: [{ key: "which", label: "Shows", kind: "choice", default: "all", choices: ["all", "system"], labels: { all: "Every drive", system: "Only the system drive" } }],
    render(body, ctx, item, w) {
      const list = el("div", "wg-bars"), note = el("div", "wg-sub", "Waiting for the numbers…");
      body.append(list, note);
      let last = "";
      return () => {
        const s = ctx.stats && ctx.stats();
        let disks = diskList(s);
        note.hidden = !!disks;
        if (!disks) return;
        if (w.opt(item, "which") === "system") disks = disks.filter((d) => d.mount === "/" || d.system);
        const sig = JSON.stringify(disks);
        if (sig === last) return;
        last = sig;
        list.replaceChildren(...disks.map((d) => {
          const r = el("div", "wg-barrow"), m = el("div", "wg-meter"), f = el("i");
          f.style.width = `${pct(d.used)}%`;
          if (d.used >= 90) m.classList.add("hot");
          m.append(f);
          r.title = d.mount;
          r.append(el("div", "wg-barhead", `${d.name || d.mount || "Drive"}`), m,
            el("div", "wg-sub", num(d.free_gb) != null && num(d.total_gb) != null ? `${num(d.free_gb)} GB free of ${num(d.total_gb)} GB` : `${pct(d.used)}% used`));
          return r;
        }));
      };
    },
  });

  W.define("network", {
    label: "Network", icon: "📶", size: [3, 2], min: [3, 2],
    options: [{ key: "graph", label: "Show the last minute", kind: "bool", default: true }],
    render(body, ctx, item, w) {
      const nums = el("div", "wg-net"), down = el("div", "wg-net-n"), up = el("div", "wg-net-n");
      nums.append(down, up);
      const cv = el("canvas", "wg-spark"), wifi = el("div", "wg-sub", "Waiting for the numbers…");
      body.append(nums, cv, wifi);
      const hist = W.cache.netHist || (W.cache.netHist = []);
      let seen = null;
      return () => {
        const s = ctx.stats && ctx.stats(), n = s && s.net && typeof s.net === "object" ? { ...s.net, down: num(s.net.down), up: num(s.net.up) } : null;
        if (!n) return;
        down.innerHTML = ""; up.innerHTML = "";
        down.append(el("small", null, "↓ down"), el("b", null, rate(n.down)));
        up.append(el("small", null, "↑ up"), el("b", null, rate(n.up)));
        wifi.textContent = n.wifi ? `Wi-Fi: ${n.wifi}` : n.ifaces && n.ifaces.length ? `On ${n.ifaces.join(", ")}` : "Not connected";
        if (s.net !== seen && n.down != null) { seen = s.net; hist.push([n.down, n.up || 0]); if (hist.length > 60) hist.shift(); }
        cv.hidden = w.opt(item, "graph") === false;
        if (cv.hidden) return;
        const wpx = cv.clientWidth, hpx = cv.clientHeight;
        if (!wpx || !hpx) return;
        if (cv.width !== wpx) cv.width = wpx;
        if (cv.height !== hpx) cv.height = hpx;
        const c = cv.getContext("2d"), top = Math.max(50e3, ...hist.map((x) => Math.max(x[0], x[1])));
        c.clearRect(0, 0, wpx, hpx);
        if (hist.length < 2) return; // one reading is a dot, not a line
        const accent = getComputedStyle(cv).getPropertyValue("--accent").trim() || "#8ab4ff";
        [[0, accent, 0.9], [1, "#ffffff", 0.45]].forEach(([k, col, a]) => {
          c.beginPath();
          // the newest reading at the right edge, like Activity
          const x0 = wpx - ((hist.length - 1) / 59) * wpx;
          hist.forEach((x, i) => { const px = x0 + (i / 59) * wpx, py = hpx - (x[k] / top) * (hpx - 2) - 1; if (i) c.lineTo(px, py); else c.moveTo(px, py); });
          c.globalAlpha = a; c.strokeStyle = col; c.lineWidth = 1.6; c.stroke();
        });
        c.globalAlpha = 1;
      };
    },
  });

  W.define("jan", {
    label: "Jan", icon: "🤖", size: [3, 2], min: [2, 2],
    render(body, ctx) {
      const state = el("div", "wg-title", "…"), models = el("div", "wg-chips"), note = el("div", "wg-sub");
      body.append(state, models, note);
      let last = "";
      return () => {
        const s = ctx.stats && ctx.stats(), j = s && s.jan;
        if (!j) { state.textContent = "Jan"; note.textContent = "Waiting for the numbers…"; return; }
        const sig = JSON.stringify(j);
        if (sig === last) return;
        last = sig;
        state.textContent = j.running ? "● Jan is running" : "○ Jan is closed";
        state.classList.toggle("ok", !!j.running);
        models.replaceChildren(...(j.loaded || []).map((m) => el("span", "wg-chip", m)));
        note.textContent = j.loaded && j.loaded.length ? `${j.loaded.length} model${j.loaded.length > 1 ? "s" : ""} in memory`
          : j.running ? "No model loaded: memory is free" : "Apps start it when they need it";
      };
    },
  });

  // --- alarms and timers: they ring whether or not the widget is on screen ------
  const Ring = {
    actx: null, timer: 0, until: 0,
    tone(kind) {
      try {
        const A = this.actx || (this.actx = new (window.AudioContext || window.webkitAudioContext)());
        if (A.state === "suspended") A.resume();
        const notes = kind === "beep" ? [[880, 0, 0.12], [880, 0.22, 0.12], [880, 0.44, 0.12]]
          : kind === "bell" ? [[659, 0, 1.4], [988, 0.03, 1.1], [1319, 0.05, 0.8]]
          : [[784, 0, 0.5], [988, 0.18, 0.5], [1319, 0.36, 0.9]];
        for (const [f, t, d] of notes) {
          const o = A.createOscillator(), g = A.createGain(), at = A.currentTime + t;
          o.type = kind === "beep" ? "square" : "sine";
          o.frequency.value = f;
          g.gain.setValueAtTime(0.0001, at);
          g.gain.exponentialRampToValueAtTime(kind === "beep" ? 0.08 : 0.22, at + 0.02);
          g.gain.exponentialRampToValueAtTime(0.0001, at + d);
          o.connect(g).connect(A.destination);
          o.start(at); o.stop(at + d + 0.05);
        }
      } catch (e) { /* no sound: the banner still shows */ }
    },
    start(text, item) {
      let box = document.getElementById("wg-ring");
      if (!box) {
        box = el("div");
        box.id = "wg-ring";
        box.setAttribute("role", "alertdialog");
        document.body.append(box);
      }
      const snooze = button("wg-btn", "Snooze 5 min", "Ring again in five minutes"), stop = button("wg-btn primary", "Stop", "Stop ringing");
      box.replaceChildren(el("b", null, "⏰"), el("span", null, text), snooze, stop);
      box.classList.add("on");
      stop.onclick = () => this.stop();
      snooze.onclick = () => { this.stop(); Alarms.timer(item, 5, `${text} (snoozed)`); };
      if (W.ctx.notify) W.ctx.notify(text);
      clearInterval(this.timer);
      this.until = Date.now() + 60000;
      this.tone(W.theme.alarm_sound || "chime");
      this.timer = setInterval(() => { if (Date.now() > this.until) this.stop(); else this.tone(W.theme.alarm_sound || "chime"); }, 2600);
      document.dispatchEvent(new CustomEvent("forge-widgets-layout"));
    },
    stop() {
      clearInterval(this.timer);
      const box = document.getElementById("wg-ring");
      if (box) box.classList.remove("on");
      document.dispatchEvent(new CustomEvent("forge-widgets-layout"));
    },
  };
  const Alarms = {
    items() { return W.pages.flatMap((p) => p.items).filter((i) => i.kind === "alarms"); },
    timer(item, minutes, label) {
      const list = (W.opt(item, "timers") || []).concat([{ end: Date.now() + minutes * 60000, label: label || `${minutes} minute timer` }]);
      W.setOpt(item, "timers", list, true);
      W.render();
    },
    check(now = new Date()) {
      const hm = `${pad2(now.getHours())}:${pad2(now.getMinutes())}`, today = dayKey(now);
      for (const item of this.items()) {
        const alarms = W.opt(item, "alarms") || [], timers = W.opt(item, "timers") || [];
        let changed = false;
        for (const a of alarms) {
          if (a.on && a.t === hm && a.last !== today) { a.last = today; changed = true; Ring.start(a.label || `Alarm ${a.t}`, item); }
        }
        const due = timers.filter((t) => t.end <= now.getTime());
        if (due.length) {
          due.forEach((t) => Ring.start(t.label || "Timer", item));
          W.setOpt(item, "timers", timers.filter((t) => t.end > now.getTime()), true);
          W.render();
        } else if (changed) W.setOpt(item, "alarms", alarms, true);
      }
    },
  };
  W.always.push(() => Alarms.check());
  W.Alarms = Alarms;
  W.Ring = Ring;

  W.define("alarms", {
    label: "Alarms and timers", icon: "⏰", size: [4, 3], min: [3, 3],
    render(body, ctx, item, w) {
      const list = el("div", "wg-alarms"), quick = el("div", "wg-row wrap"), add = el("form", "wg-alarm-add");
      [1, 5, 10, 25, 60].forEach((m) => { const b = button("wg-btn", m < 60 ? `${m} min` : "1 hour", `A ${m} minute timer`); b.onclick = () => Alarms.timer(item, m); quick.append(b); });
      const time = el("input"), label = el("input"), go = button("wg-btn primary", "Add alarm", "Add this alarm");
      time.type = "time"; time.required = true; time.setAttribute("aria-label", "Alarm time");
      label.placeholder = "Label (optional)"; label.setAttribute("aria-label", "Alarm label");
      go.type = "submit";
      add.append(time, label, go);
      add.addEventListener("keydown", (e) => e.stopPropagation());
      add.addEventListener("submit", (e) => {
        e.preventDefault();
        if (!time.value) return;
        const alarms = (w.opt(item, "alarms") || []).concat([{ t: time.value, label: label.value.trim().slice(0, 60), on: true }]);
        alarms.sort((a, b) => a.t.localeCompare(b.t));
        w.setOpt(item, "alarms", alarms);
      });
      body.append(list, quick, add);
      const draw = () => {
        const alarms = w.opt(item, "alarms") || [], timers = w.opt(item, "timers") || [];
        list.replaceChildren();
        timers.forEach((t, k) => {
          const r = el("div", "wg-alarm timer"), left = el("b", "wg-left"), x = button("wg-round small", "×", "Cancel this timer");
          left.dataset.end = String(t.end);
          x.onclick = () => { w.setOpt(item, "timers", timers.filter((_, j) => j !== k)); };
          r.append(el("span", null, "⏳"), left, el("span", "wg-sub", t.label), x);
          list.append(r);
        });
        alarms.forEach((a, k) => {
          const r = el("div", "wg-alarm"), on = el("input"), x = button("wg-round small", "×", "Delete this alarm");
          on.type = "checkbox"; on.checked = a.on !== false; on.setAttribute("aria-label", `Alarm ${a.t} on`);
          on.onchange = () => { const all = alarms.slice(); all[k] = Object.assign({}, a, { on: on.checked }); w.setOpt(item, "alarms", all, true); };
          x.onclick = () => w.setOpt(item, "alarms", alarms.filter((_, j) => j !== k));
          r.append(on, el("b", null, a.t), el("span", "wg-sub", a.label || ""), x);
          list.append(r);
        });
        if (!alarms.length && !timers.length) list.append(el("div", "wg-sub", "No alarms. Pick a timer or add an alarm below."));
      };
      draw();
      return () => {
        list.querySelectorAll(".wg-left").forEach((n) => { n.textContent = clockOf((Number(n.dataset.end) - Date.now()) / 1000); });
      };
    },
  });

  // the visualiser as a card: its own style, colours and size. v2: the styles
  // are ForgeVisual's own list; it draws only while the card can be seen, at
  // its frame cap (20 on the reduced power tier), a few frames a second in
  // silence, and asks for no frames at all while hidden or paused.
  const visStyles = () => (window.ForgeVisual ? ForgeVisual.names() : ["bars", "mirror", "wave", "ring", "orbit", "aurora", "terrain"]);
  const titled = (k) => k.charAt(0).toUpperCase() + k.slice(1);
  W.define("visualiser", {
    label: "Visualiser", icon: "🎚️", size: [4, 2], min: [2, 2],
    options: [
      { key: "style", label: "Style", kind: "choice", default: "bars",
        get choices() { return visStyles(); }, get labels() { return Object.fromEntries(visStyles().map((k) => [k, titled(k)])); } },
      { key: "scheme", label: "Colours", kind: "choice", default: "accent", choices: ["accent", "rainbow", "neon", "fire", "ice", "white"],
        labels: { accent: "The card's colour", rainbow: "Rainbow", neon: "Neon", fire: "Fire", ice: "Ice", white: "White" } },
      { key: "size", label: "Size", kind: "number", default: 1, min: 0.5, max: 2, step: 0.1 },
      { key: "gain", label: "Sensitivity", kind: "number", default: 1.2, min: 0.5, max: 2.5, step: 0.1 },
      { key: "fps", label: "Frame rate", kind: "choice", default: "30", choices: ["20", "30", "60"],
        labels: { 20: "20 a second (lightest)", 30: "30 a second", 60: "60 a second (smoothest)" } },
    ],
    render(body, ctx, item, w) {
      const cv = el("canvas", "wg-vis"), hint = el("div", "wg-sub wg-vis-hint", "Play something to see it move");
      body.append(cv, hint);
      const st = {}, card = () => body.closest(".wg");
      let raf = 0, timer = 0, drawn = 0, colours = null;
      const stop = () => { if (raf) cancelAnimationFrame(raf); clearTimeout(timer); raf = timer = 0; };
      const loop = (t) => {
        raf = 0;
        if (!body.isConnected || !w.visible()) return; // the 1 s tick starts it again
        const cap = w.tier() === "reduced" ? 20 : Number(w.opt(item, "fps")) || 30;
        if (t - drawn < 1000 / cap - 2) { raf = requestAnimationFrame(loop); return; }
        drawn = t;
        const lv = ctx.levels && ctx.levels(), wpx = cv.clientWidth, hpx = cv.clientHeight;
        const silent = !(lv && lv.some((x) => x > 0.02));
        hint.hidden = !silent;
        if (wpx && hpx && window.ForgeVisual) {
          if (cv.width !== wpx) cv.width = wpx;
          if (cv.height !== hpx) cv.height = hpx;
          const c = cv.getContext("2d");
          c.clearRect(0, 0, wpx, hpx);
          if (lv) {
            if (!colours) {
              const cs = getComputedStyle(card() || cv), accent = cs.getPropertyValue("--accent").trim() || "#8ab4ff";
              colours = [accent, cs.getPropertyValue("--accent-2").trim() || accent];
            }
            // the styles are laid out for a whole screen; a card is drawn a little bigger in step with its size
            const scale = Math.max(0.35, Math.min(1, hpx / 400)) * Number(w.opt(item, "size") || 1);
            ForgeVisual.draw(c, wpx, hpx, w.opt(item, "style"), lv, { size: scale, gain: w.opt(item, "gain"), scheme: w.opt(item, "scheme"),
              accent: colours[0], accent2: colours[1], st });
          }
        }
        // a still style in silence: four frames a second until sound comes
        if (silent && ForgeVisual && ForgeVisual.stillInSilence(w.opt(item, "style"), w.opt(item, "scheme"))) {
          timer = setTimeout(() => { timer = 0; raf = requestAnimationFrame(loop); }, 250);
        } else raf = requestAnimationFrame(loop);
      };
      // the once-a-second update: (re)starts the loop when the card can be
      // seen again, stops it when not, and picks up a changed colour
      return () => {
        colours = null;
        if (!body.isConnected || !w.visible()) { stop(); return; }
        if (!raf && !timer) raf = requestAnimationFrame(loop);
      };
    },
  });

  // the System card with more to say (the owner: "the CPU/battery widget with more info")
  W.KINDS.system.options = [{ key: "more", label: "Also network and disk", kind: "bool", default: false }];

  // --- v2 (2026-10-07): Activity, Battery and Countdown -----------------------

  // The last five minutes of CPU, memory and GPU, kept whether or not a card
  // shows them, so an Activity card added later starts with a full graph.
  const HIST = 300;
  const Hist = { cpu: [], mem: [], gpu: [], seen: null };
  W.always.push(() => {
    const s = W.ctx.stats && W.ctx.stats();
    if (!s || s === Hist.seen) return;
    Hist.seen = s;
    const push = (k, v) => { Hist[k].push(v == null || !Number.isFinite(Number(v)) ? null : Number(v)); if (Hist[k].length > HIST) Hist[k].shift(); };
    push("cpu", pct(s.cpu));
    push("mem", pct(s.mem));
    push("gpu", s.gpu && num(s.gpu.busy) != null ? pct(num(s.gpu.busy) * 100) : null);
  });
  W.Hist = Hist;

  W.define("activity", {
    label: "Activity", icon: "📈", size: [4, 2], min: [3, 2],
    options: [
      { key: "span", label: "Shows the last", kind: "choice", default: "120", choices: ["60", "120", "300"],
        labels: { 60: "Minute", 120: "Two minutes", 300: "Five minutes" } },
      { key: "gpu", label: "GPU line", kind: "bool", default: true },
    ],
    render(body, ctx, item, w) {
      const nums = el("div", "wg-act-nums"), cv = el("canvas", "wg-spark wg-act"), note = el("div", "wg-sub", "Waiting for the numbers…");
      const cell = (label, cls) => { const c = el("div", `wg-net-n ${cls}`), b = el("b", null, "–"); c.append(el("small", null, label), b); nums.append(c); return { c, b }; };
      const cpu = cell("CPU", "k-cpu"), mem = cell("Memory", "k-mem"), gpu = cell("GPU", "k-gpu"), temp = cell("CPU heat", "k-temp");
      body.append(nums, cv, note);
      let colours = null;
      return () => {
        const s = ctx.stats && ctx.stats();
        note.hidden = !!s;
        if (!s) return;
        cpu.b.textContent = pct(s.cpu) == null ? "–" : `${pct(s.cpu)}%`;
        mem.b.textContent = pct(s.mem) == null ? "–" : `${pct(s.mem)}%`;
        const g = Hist.gpu[Hist.gpu.length - 1];
        gpu.c.hidden = w.opt(item, "gpu") === false || g == null;
        gpu.b.textContent = g == null ? "–" : `${g}%`;
        const tc = s.temps && num(s.temps.cpu);
        temp.c.hidden = !Number.isFinite(tc) || !tc;
        if (!temp.c.hidden) { temp.b.textContent = `${Math.round(tc)}°`; temp.c.classList.toggle("hot", tc >= 85); }
        const wpx = cv.clientWidth, hpx = cv.clientHeight;
        if (!wpx || !hpx) return;
        if (cv.width !== wpx) cv.width = wpx;
        if (cv.height !== hpx) cv.height = hpx;
        const c = cv.getContext("2d"), span = Number(w.opt(item, "span")) || 120;
        if (!colours) {
          const cs = getComputedStyle(cv), accent = cs.getPropertyValue("--accent").trim() || "#8ab4ff";
          colours = [accent, cs.getPropertyValue("--accent-2").trim() || "#ff9ad5"];
        }
        c.clearRect(0, 0, wpx, hpx);
        // faint quarter lines
        c.globalAlpha = 0.12; c.strokeStyle = "#ffffff"; c.lineWidth = 1;
        c.beginPath();
        for (const q of [0.25, 0.5, 0.75]) { c.moveTo(0, Math.round(hpx * q) + 0.5); c.lineTo(wpx, Math.round(hpx * q) + 0.5); }
        c.stroke();
        const line = (vals, col, alpha, fillIt) => {
          const v = vals.slice(-span);
          if (v.filter((x) => x != null).length < 2) return;
          const x0 = wpx - ((v.length - 1) / (span - 1)) * wpx, y = (x) => hpx - 1 - (x / 100) * (hpx - 3);
          c.beginPath();
          let started = false, lastX = x0;
          v.forEach((x, i) => {
            if (x == null) return;
            const px = x0 + (i / (span - 1)) * wpx;
            if (started) c.lineTo(px, y(x)); else { c.moveTo(px, y(x)); started = true; }
            lastX = px;
          });
          if (fillIt) {
            c.save();
            c.lineTo(lastX, hpx); c.lineTo(x0, hpx); c.closePath();
            const g2 = c.createLinearGradient(0, 0, 0, hpx);
            g2.addColorStop(0, col); g2.addColorStop(1, "rgba(0,0,0,0)");
            c.globalAlpha = 0.28; c.fillStyle = g2; c.fill();
            c.restore();
          } else { c.globalAlpha = alpha; c.strokeStyle = col; c.lineWidth = 1.6; c.stroke(); }
        };
        c.setLineDash([4, 3]);
        line(Hist.mem, "#ffffff", 0.4);
        c.setLineDash([]);
        if (w.opt(item, "gpu") !== false) line(Hist.gpu, colours[1], 0.85);
        line(Hist.cpu, colours[0], 1, true);
        line(Hist.cpu, colours[0], 1);
        c.globalAlpha = 1;
      };
    },
  });

  W.define("battery", {
    label: "Battery", icon: "🔋", size: [3, 2], min: [2, 2],
    render(body, ctx) {
      const ring = el("div", "wg-gauge big"), pctEl = el("b", null, "–"), text = el("div", "wg-np-text");
      const title = el("div", "wg-title", "Battery"), sub = el("div", "wg-sub", "Waiting for the numbers…");
      ring.append(pctEl);
      text.append(title, sub);
      body.classList.add("wg-battery-body");
      body.append(ring, text);
      return () => {
        const s = ctx.stats && ctx.stats();
        if (!s) return;
        if (pct(s.battery) == null) { pctEl.textContent = "–"; title.textContent = "No battery"; sub.textContent = "Running on mains power"; ring.style.setProperty("--v", "0"); return; }
        const b = pct(s.battery), charging = s.charging === true, plugged = s.plugged === true;
        ring.style.setProperty("--v", String(b));
        ring.classList.toggle("hot", !charging && b <= 20);
        ring.classList.toggle("charging", charging);
        pctEl.textContent = `${b}%`;
        title.textContent = charging ? "⚡ Charging" : plugged ? "Plugged in" : b <= 20 ? "Running low" : "On battery";
        sub.textContent = charging ? "Filling up" : plugged ? "Full or holding" : b <= 20 ? "Plug in soon" : "Unplugged";
      };
    },
  });

  W.define("countdown", {
    label: "Countdown", icon: "⏳", size: [3, 2], min: [2, 2],
    options: [
      { key: "what", label: "For", kind: "text", default: "", placeholder: "An exam, a trip…" },
      { key: "date", label: "Date", kind: "date", default: "" },
      { key: "time", label: "Time (optional)", kind: "text", default: "", placeholder: "09:00" },
    ],
    render(body, ctx, item, w) {
      const big = el("div", "wg-big"), what = el("div", "wg-title"), sub = el("div", "wg-sub"), meter = el("div", "wg-meter"), fill = el("i");
      meter.append(fill);
      body.append(big, what, sub, meter);
      return () => {
        const day = String(w.opt(item, "date") || ""), tm = /^(\d{1,2}):(\d{2})$/.exec(String(w.opt(item, "time") || "").trim());
        const time = tm && Number(tm[1]) < 24 && Number(tm[2]) < 60 ? `${tm[1].padStart(2, "0")}:${tm[2]}` : "00:00";
        what.textContent = w.opt(item, "what") || "Countdown";
        // a real calendar day only: 2026-02-31 or month 13 is not one
        const [yy, mo, dd] = day.split("-").map(Number), check = new Date(yy, mo - 1, dd);
        if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || yy < 1970 || check.getFullYear() !== yy || check.getMonth() !== mo - 1 || check.getDate() !== dd) {
          big.textContent = "–";
          sub.textContent = "✎ Edit, then ⚙, to pick a date";
          meter.hidden = true;
          return;
        }
        const end = new Date(`${day}T${time}`), left = end - Date.now(), dayMs = 86400000;
        meter.hidden = false;
        if (left <= 0) {
          const ago = Math.floor(-left / dayMs);
          big.textContent = ago < 1 ? "Today" : `${ago} day${ago > 1 ? "s" : ""} ago`;
          fill.style.width = "100%";
        } else if (left < 2 * dayMs) {
          const h = Math.floor(left / 3600000), m = Math.floor((left % 3600000) / 60000);
          big.textContent = `${h} h ${pad2(m)} min`;
        } else big.textContent = `${Math.ceil(left / dayMs)} days`;
        sub.textContent = end.toLocaleDateString([], { weekday: "short", day: "numeric", month: "long" }) + (time !== "00:00" ? ` · ${time}` : "");
        // the meter fills over the last 30 days
        if (left > 0) fill.style.width = `${Math.round(Math.max(0, Math.min(1, 1 - left / (30 * dayMs))) * 100)}%`;
        meter.classList.toggle("hot", left > 0 && left < 3 * dayMs);
      };
    },
  });

  // --- more v2 widgets (the owner, 2026-10-07: "love more widgets") ------------

  // the times in other places; the first row is here
  W.define("worldclock", {
    label: "World clocks", icon: "🌏", size: [3, 2], min: [3, 2],
    options: [
      { key: "zones", label: "Places (time zones, comma between)", kind: "text", default: "Asia/Tokyo, Europe/London, America/New_York",
        placeholder: "Asia/Tokyo, Europe/London" },
    ],
    render(body, ctx, item, w) {
      const list = el("div", "wg-zones");
      body.append(list);
      const zones = String(w.opt(item, "zones") || "").split(",").map((z) => z.trim()).filter(Boolean).slice(0, 5);
      const rows = zones.map((zone) => {
        const r = el("div", "wg-zone"), name = el("span", "wg-sub", zone.split("/").pop().replace(/_/g, " ")), time = el("b"), diff = el("small");
        r.append(name, diff, time);
        list.append(r);
        return { zone, time, diff, r };
      });
      if (!rows.length) list.append(el("div", "wg-sub", "⚙ to add places, like Asia/Tokyo"));
      const offset = (zone, now) => {
        try {
          const p = Object.fromEntries(new Intl.DateTimeFormat("en-GB", { timeZone: zone, hour12: false, year: "numeric", month: "2-digit", day: "2-digit",
            hour: "2-digit", minute: "2-digit" }).formatToParts(now).map((x) => [x.type, x.value]));
          return (Date.UTC(p.year, p.month - 1, p.day, p.hour % 24, p.minute) - Date.UTC(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours(), now.getMinutes())) / 3600000;
        } catch (e) { return null; }
      };
      return () => {
        const now = new Date(), h24 = !ctx.clock24 || ctx.clock24();
        for (const row of rows) {
          let text = "?";
          try { text = now.toLocaleTimeString([], { timeZone: row.zone, hour: "2-digit", minute: "2-digit", hour12: !h24 }); } catch (e) { row.r.title = "Not a time zone name"; }
          row.time.textContent = text;
          const d = offset(row.zone, now);
          row.diff.textContent = d == null ? "" : d === 0 ? "same time" : `${d > 0 ? "+" : "−"}${Math.abs(d)} h`;
        }
      };
    },
  });

  // a focus timer: a ring that empties, work then a break
  const Focus = { at: {} }; // item id -> {end, phase, running, left}
  W.define("focus", {
    label: "Focus timer", icon: "🎯", size: [3, 2], min: [2, 2],
    options: [
      { key: "work", label: "Focus minutes", kind: "number", default: 25, min: 5, max: 120, step: 5 },
      { key: "rest", label: "Break minutes", kind: "number", default: 5, min: 1, max: 30, step: 1 },
    ],
    render(body, ctx, item, w) {
      const ring = el("div", "wg-gauge big"), left = el("b", null, ""), side = el("div", "wg-np-text");
      const what = el("div", "wg-title"), row = el("div", "wg-row");
      const go = button("wg-btn primary", "Start", "Start or pause"), reset = button("wg-btn ghost", "Reset", "Back to the start");
      ring.append(left);
      row.append(go, reset);
      side.append(what, row);
      body.classList.add("wg-battery-body");
      body.append(ring, side);
      const mins = (phase) => Number(w.opt(item, phase === "rest" ? "rest" : "work")) || (phase === "rest" ? 5 : 25);
      const st = Focus.at[item.id] || (Focus.at[item.id] = { phase: "work", running: false, left: mins("work") * 60, end: 0 });
      const remaining = () => (st.running ? Math.max(0, (st.end - Date.now()) / 1000) : st.left);
      go.onclick = () => {
        if (st.running) { st.left = remaining(); st.running = false; } else { st.end = Date.now() + st.left * 1000; st.running = true; }
        draw();
      };
      reset.onclick = () => { st.phase = "work"; st.running = false; st.left = mins("work") * 60; draw(); };
      const draw = () => {
        let sec = remaining();
        if (st.running && sec <= 0) {
          const done = st.phase;
          st.phase = done === "work" ? "rest" : "work";
          st.left = mins(st.phase) * 60;
          st.end = Date.now() + st.left * 1000;
          sec = st.left;
          Ring.start(done === "work" ? "Focus time is up: take a break" : "Break is over: back to it", item);
        }
        ring.style.setProperty("--v", String(Math.round((sec / (mins(st.phase) * 60)) * 100)));
        ring.classList.toggle("charging", st.phase === "rest");
        left.textContent = clockOf(sec);
        what.textContent = st.phase === "rest" ? "Break" : st.running ? "Focusing" : "Focus";
        go.textContent = st.running ? "Pause" : "Start";
      };
      draw();
      return draw;
    },
  });
  // a running focus timer rings even when its page is not on screen
  W.always.push(() => {
    for (const [id, st] of Object.entries(Focus.at)) {
      if (!st.running || st.end > Date.now()) continue;
      const item = W.pages.flatMap((p) => p.items).find((i) => i.id === id);
      if (!item) { delete Focus.at[id]; continue; }
      const minutes = (k, d) => Number(W.opt(item, k)) || d;
      const done = st.phase;
      st.phase = done === "work" ? "rest" : "work";
      st.left = (st.phase === "rest" ? minutes("rest", 5) : minutes("work", 25)) * 60;
      st.end = Date.now() + st.left * 1000;
      Ring.start(done === "work" ? "Focus time is up: take a break" : "Break is over: back to it", item);
    }
  });

  // a short to-do list, kept in the layout
  W.define("todo", {
    label: "To do", icon: "✅", size: [3, 3], min: [2, 2],
    render(body, ctx, item, w) {
      const list = el("div", "wg-todo-list"), add = el("input", "wg-ask");
      add.placeholder = "Something to do, then Enter";
      add.setAttribute("aria-label", "A new thing to do");
      body.append(list, add);
      const items = () => w.opt(item, "todo") || [];
      const save = (next) => { w.setOpt(item, "todo", next, true); draw(); };
      const draw = () => {
        list.replaceChildren();
        const all = items();
        all.forEach((t, k) => {
          const r = el("label", "wg-todo-row" + (t.done ? " done" : "")), box = el("input"), x = button("wg-round small", "×", "Remove");
          box.type = "checkbox"; box.checked = !!t.done;
          box.onchange = () => { const next = all.slice(); next[k] = { ...t, done: box.checked }; save(next); };
          x.onclick = (e) => { e.preventDefault(); save(all.filter((_, j) => j !== k)); };
          r.append(box, el("span", null, t.text), x);
          list.append(r);
        });
        if (!all.length) list.append(el("div", "wg-sub", "Nothing on the list."));
      };
      add.addEventListener("keydown", (e) => {
        e.stopPropagation();
        if (e.key === "Enter" && add.value.trim()) { save(items().concat([{ text: add.value.trim().slice(0, 120), done: false }])); add.value = ""; }
      });
      draw();
    },
  });

  // the moon tonight: its phase drawn, how full, and the next full moon
  const moonAge = (d) => {
    const synodic = 29.530588853, known = Date.UTC(2000, 0, 6, 18, 14) / 86400000;
    return (((d.getTime() / 86400000 - known) % synodic) + synodic) % synodic;
  };
  W.define("moon", {
    label: "Moon", icon: "🌙", size: [3, 2], min: [2, 2],
    render(body) {
      const disc = el("canvas", "wg-moon-disc"), side = el("div", "wg-np-text");
      const name = el("div", "wg-title"), sub = el("div", "wg-sub"), next = el("div", "wg-sub");
      side.append(name, sub, next);
      body.classList.add("wg-battery-body");
      body.append(disc, side);
      let shownDay = "";
      // drawn once a day: the dark disc, the lit half, and the terminator as
      // an ellipse that is dark (crescent) or lit (gibbous)
      const paint = (f) => {
        const n = 192, r = n / 2 - 4, c = disc.getContext("2d"), waxing = f < 0.5, k = Math.cos(f * 2 * Math.PI);
        disc.width = disc.height = n;
        c.clearRect(0, 0, n, n);
        c.translate(n / 2, n / 2);
        c.fillStyle = "rgba(255,255,255,.08)";
        c.beginPath(); c.arc(0, 0, r, 0, Math.PI * 2); c.fill();
        const light = c.createRadialGradient(-r * 0.3, -r * 0.3, r * 0.1, 0, 0, r);
        light.addColorStop(0, "#fffaf0"); light.addColorStop(1, "#d9d2c3");
        c.fillStyle = light;
        c.beginPath(); c.arc(0, 0, r, -Math.PI / 2, Math.PI / 2, !waxing); c.fill(); // the lit half
        c.beginPath(); c.ellipse(0, 0, Math.abs(k) * r, r, 0, 0, Math.PI * 2);
        if (k > 0) { c.fillStyle = "#14161c"; c.fill(); c.fillStyle = "rgba(255,255,255,.08)"; c.fill(); } else c.fill();
        // a few soft maria
        c.globalAlpha = 0.12; c.fillStyle = "#6b6458";
        for (const [x, y, rr] of [[-0.25, -0.2, 0.18], [0.2, 0.1, 0.14], [-0.05, 0.35, 0.1]]) { c.beginPath(); c.arc(x * r, y * r, rr * r, 0, Math.PI * 2); c.fill(); }
        c.setTransform(1, 0, 0, 1, 0, 0); c.globalAlpha = 1;
      };
      return () => {
        const now = new Date(), key = dayKey(now);
        if (key === shownDay) return;
        shownDay = key;
        const age = moonAge(now), f = age / 29.530588853, lit = Math.round(((1 - Math.cos(f * 2 * Math.PI)) / 2) * 100);
        const names = ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous", "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"];
        name.textContent = names[Math.round(f * 8) % 8];
        sub.textContent = `${lit}% lit`;
        const toFull = (14.765 - age + 29.530588853) % 29.530588853;
        next.textContent = toFull < 1 ? "Full tonight" : `Full in ${Math.round(toFull)} days`;
        paint(f);
      };
    },
  });

  window.ForgeWidgets = W;
})();
