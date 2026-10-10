/* Forge's glass (S26, 2026-09-28), shared by the hub and the desktop page.

   the owner (S22): "more glass: liquid glass (bright edges, a slight bend) and
   frosted, on tiles, bar, widgets and app frames, with a strength slider".
   A glass kind is one stylesheet in shared/glass/ (its first line names it;
   forge_theme.list_glass() finds them), so a new kind is one new file.
   ForgeGlass.apply(theme) loads the chosen one, puts body.glass-<kind> on
   the page and turns Glass strength into the three variables the
   stylesheets read. "flat" is no stylesheet: the look's own panes.

   On a bright wallpaper (theme.wall_light, 0..1) the panes keep more of
   their dark fill and their white edges dim, whatever the strength: clear glass over a near-white
   picture put white text on near-white (QUICKSTART item 2, 2026-10-07).
   --glass-pane is the same floor for the widgets' own Glass look, and
   --glass-solid thickens the dark fill under Frosted and Liquid. */
(() => {
  const G = {
    link: null,
    // where shared/ is, seen from this page (both pages sit one folder down)
    base: "../shared/glass/",
    vars(strength, wallLight, pane) {
      const k = Math.max(0, Math.min(1, Number(strength ?? 0.6)));
      // 0 up to a mid-bright picture, 1 from a near-white one
      const light = Math.max(0, Math.min(1, ((Number(wallLight) || 0) - 0.3) / 0.5));
      const glass = Number(pane ?? 0.55);
      return {
        "--glass-blur": `${Math.round(k * 28)}px`,
        "--glass-keep": `${Math.round(Math.max(100 - k * 55, 45 + 55 * light))}%`,
        "--glass-edge": (k * 0.5 * (1 - 0.6 * light)).toFixed(3),
        "--glass-solid": (0.68 + 0.2 * light).toFixed(3),
        "--glass-pane": Math.max(Number.isFinite(glass) ? glass : 0.55, 0.3 + 0.3 * light).toFixed(3),
      };
    },
    apply(theme) {
      const t = theme || {};
      const kind = /^[a-z0-9-]+$/.test(t.glass_style || "") ? t.glass_style : "flat";
      if (!this.link) {
        this.link = document.createElement("link");
        this.link.rel = "stylesheet";
        this.link.id = "glass-css";
        document.head.appendChild(this.link);
      }
      const href = kind === "flat" ? "" : `${this.base}${kind}.css`;
      if (this.link.dataset.kind !== kind) {
        this.link.dataset.kind = kind;
        if (href) this.link.href = href;
        else this.link.removeAttribute("href");
      }
      const b = document.body.classList;
      [...b].filter((c) => c.startsWith("glass-") && c !== `glass-${kind}`).forEach((c) => b.remove(c));
      if (kind !== "flat") b.add(`glass-${kind}`);
      const root = document.documentElement.style;
      Object.entries(this.vars(t.glass_strength, t.wall_light, t.glass)).forEach(([name, value]) => root.setProperty(name, value));
      return kind;
    },
  };
  window.ForgeGlass = G;
})();
