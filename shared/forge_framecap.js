/* Forge's frame-rate cap (B39): Settings > Speed and detail > "Frame rate cap", also a
   slider in the Power app, setting `max_fps`.

   Wraps requestAnimationFrame so a page draws at most `fps` frames a second.
   Everything that animates through it (particles, the visualiser, canvas
   games) slows together, and between frames the page sleeps on a timer rather
   than waking every screen refresh, which is where the power goes. 60 or more
   means no cap: every call goes straight through. CSS animations are not
   touched (the browser runs those itself).

   Loaded by the hub and the desktop particles page with a script tag, and by
   every app through shared/forge_chrome.js. It wraps whatever
   requestAnimationFrame it finds, so the rest wrapper in forge_chrome.js (B18)
   keeps working underneath it. */
(() => {
  if (window.ForgeFrameCap) return;
  const realRaf = window.requestAnimationFrame.bind(window);
  const realCancel = window.cancelAnimationFrame.bind(window);
  const queue = new Map();
  let fps = 60, nextId = 2e9, last = -1e9, timer = 0, pending = 0;

  function run(t) {
    last = t;
    const waiting = [...queue.values()];
    queue.clear();
    for (const cb of waiting) {
      try { cb(t); } catch (err) { setTimeout(() => { throw err; }); }
    }
  }
  // A screen refresh arrived: draw if a frame is due, otherwise wait for the
  // next refresh (the timer lands just before the one that is due).
  function tick(t) {
    pending = 0;
    if (!queue.size) return;
    if (fps < 60 && t - last < 1000 / fps - 2) { pending = realRaf(tick); return; }
    run(t);
  }
  function pump() {
    if (timer || pending || !queue.size) return;
    const wait = last + 1000 / fps - performance.now() - 6;
    if (wait > 0) timer = setTimeout(() => { timer = 0; pending = realRaf(tick); }, wait);
    else pending = realRaf(tick);
  }

  window.requestAnimationFrame = (cb) => {
    if (fps >= 60) return realRaf(cb);
    const id = nextId++;
    queue.set(id, cb);
    pump();
    return id;
  };
  window.cancelAnimationFrame = (id) => {
    if (!queue.delete(id)) realCancel(id);
  };

  window.ForgeFrameCap = {
    get fps() { return fps; },
    /** The browser's own requestAnimationFrame, past the cap, for the few
        things that must stay on time (the Alt+V pulse); cancelAnimationFrame
        above cancels its frames too. */
    raw: realRaf,
    /** Any number; below 1 or not a number counts as no cap. */
    set(value) {
      const n = Math.round(Number(value));
      fps = Number.isFinite(n) && n >= 1 ? Math.min(60, n) : 60;
      if (timer) { clearTimeout(timer); timer = 0; }
      // Frames already waiting run at the next refresh under the new rate.
      if (queue.size && !pending) pending = realRaf(tick);
    },
  };
})();
