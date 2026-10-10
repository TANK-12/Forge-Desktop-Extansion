/* What applies now for the settings with a battery twin: Settings > Speed and
   detail, table POWER_PAIRS in shared/forge_theme.py, which the theme carries
   as power_pairs. Plugged in, each setting as it is; on battery, "min" takes
   the lower of the two and "scale" multiplies by the battery share. The same
   rules as forge_theme.power_values (tests/test_speed_detail.py runs both).

   Easing off (the owner, 2026-09-29): while other programs keep the GPU busy
   (shared/forge_gpuload.py's level, 0 to 3), or the battery is nearly flat,
   the effects step down in his order: the visualiser goes off, then half the
   particles go, then the frame rates come down to 25, the particles never
   under 20. On battery the visualiser also goes off unless Settings says to
   keep it. The third argument carries { gpu: level, battery: percent,
   shown: share }.

   Tiers (the owner, 2026-10-07: "more intelligent power on and off"): one
   decision for every effect, from the plug, the charge, the other programs'
   GPU load and how much of the page anyone can see.
     full     level 0: everything as set (plugged in and visible stays here)
     reduced  level 1-3: the steps above; a window or screen with less than
              COVERED_SHARE of it showing (the GNOME extension measures it,
              desktop/gnome-extension, shared/forge_seen.py) goes straight to 3
              and its frame rates to COVERED_FPS: a sliver needs no more
     paused   nothing drawn: under a tenth showing, minimised or on another
              workspace. forge.py and desktop/particles.py unmap the page for
              that, so it never reaches this file; tier() names it from
              state.hidden for anyone who asks.
   tier() says why: "reduced: machine busy" (forge_gpuload.py's CpuBusy,
   state.reason "cpu"), "reduced: GPU busy", "reduced: battery low",
   "reduced: mostly covered", "paused: hidden".

   Loaded by the hub and the desktop particles page with a script tag; node
   loads it in the tests. */
(function (root) {
  // A theme.js written before power_pairs existed still gets its battery cap.
  const FALLBACK = { max_fps: ["battery_fps", "min"] };
  const LOAD_FPS = 25;
  const PARTICLE_FLOOR = 20;
  // less than this share of the page showing: the reduced tier's last step
  const COVERED_SHARE = 0.35;
  const COVERED_FPS = 15;
  function paired(theme, onBattery) {
    const out = {};
    Object.entries((theme && theme.power_pairs) || FALLBACK).forEach(([key, pair]) => {
      const [twin, rule] = pair;
      let v = theme[key];
      if (onBattery && twin in theme) {
        const w = theme[twin];
        // only the battery value saved: it stands alone (a share has nothing to scale)
        if (!(key in theme)) v = rule === "scale" ? undefined : w;
        else if (rule === "scale") v = Math.round(Number(v) * Number(w) * 1000) / 1000;
        else if (typeof v === "boolean") v = v && !!w;
        else if (typeof v === "string") v = Number(w) < Number(v) ? w : v;
        else v = Math.min(Number(v), Number(w));
      }
      if (v !== undefined) out[key] = v;
    });
    return out;
  }
  /** How far to ease off: the GPU's level, or all the way on a battery at or
      under Settings' low-battery mark. */
  function level(theme, state, onBattery) {
    const s = state || {};
    let n = theme.gpu_ease === false ? 0 : Math.round(Number(s.gpu) || 0);
    const saver = Number(theme.battery_saver ?? 20);
    if (onBattery && saver > 0 && s.battery != null && Number(s.battery) <= saver) n = 3;
    if (s.shown != null && Number(s.shown) < COVERED_SHARE) n = 3;
    return Math.max(0, Math.min(3, n));
  }
  function covered(state) {
    return !!state && state.shown != null && Number(state.shown) < COVERED_SHARE;
  }
  /** "full", or "reduced: <why>" / "paused: <why>" (see the top of this file). */
  function tier(theme, state, onBattery) {
    const s = state || {}, t = theme || {};
    if (s.hidden) return "paused: hidden";
    if (!level(t, s, onBattery)) return "full";
    const saver = Number(t.battery_saver ?? 20);
    if (onBattery && saver > 0 && s.battery != null && Number(s.battery) <= saver) return "reduced: battery low";
    if (t.gpu_ease !== false && Number(s.gpu) > 0) return s.reason === "cpu" ? "reduced: machine busy" : "reduced: GPU busy";
    return "reduced: mostly covered";
  }
  function eased(theme, now, n, onBattery) {
    const out = {};
    const get = (k, d) => Number(now[k] ?? theme[k] ?? d);
    if (n >= 1 || (onBattery && theme.battery_visualiser === false)) {
      out.visualiser_shown = false;
      out.desktop_visualiser = "off";
    }
    if (n >= 2) {
      out.particle_amount = Math.round(get("particle_amount", 1) * 500) / 1000;
      out.desktop_particle_amount = Math.round(get("desktop_particle_amount", 1) * 500) / 1000;
    }
    if (n >= 3) {
      out.max_fps = Math.min(get("max_fps", 60), LOAD_FPS);
      // down to 25, never under 20 on the eased side (a lower saved rate stays)
      for (const k of ["particle_fps", "desktop_particle_fps"]) {
        const v = get(k, 30);
        out[k] = Math.min(v, Math.max(PARTICLE_FLOOR, LOAD_FPS));
      }
      out.desktop_visualiser_fps = Math.min(get("desktop_visualiser_fps", 30), LOAD_FPS);
    }
    return out;
  }
  function values(theme, onBattery, state) {
    const now = paired(theme || {}, onBattery);
    Object.assign(now, eased(theme || {}, now, level(theme || {}, state, onBattery), onBattery));
    if (covered(state)) {
      // mostly covered: the strip that shows keeps moving, slower
      for (const k of ["max_fps", "particle_fps", "desktop_particle_fps", "desktop_visualiser_fps"]) {
        const v = Number(now[k] ?? (theme || {})[k] ?? (k === "max_fps" ? 60 : 30));
        now[k] = Math.min(v, COVERED_FPS);
      }
    }
    // the tier the effects follow (helper-6's particles, widgets and depth)
    now.power_tier = tier(theme, state, onBattery);
    return now;
  }
  /** The theme with every paired setting replaced by what applies now. */
  function apply(theme, onBattery, state) {
    return Object.assign({}, theme, values(theme, onBattery, state));
  }
  const api = { values, apply, paired, level, tier, LOAD_FPS, PARTICLE_FLOOR, COVERED_SHARE, COVERED_FPS };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ForgePower = api;
})(this);
