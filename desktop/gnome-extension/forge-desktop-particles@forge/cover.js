// How much of a screen or window still shows under the windows above it
// (task 60). Plain rectangle sums on purpose: Mtk.Region.get_rectangle
// crashes GNOME Shell 51 from JavaScript (it hands back a struct by value).
// No GNOME imports, so tests/test_desktop_cover.py runs it under gjs.

// A screen or window shows when at least this share of it is uncovered.
export const SHOWN_LEAST = 0.1;

// More pieces than this and the windows are left as not covering.
const MOST_PIECES = 400;

function box(r) {
    return {x: r.x, y: r.y, width: r.width, height: r.height};
}

// The overlap of two rectangles, or null.
export function clip(a, b) {
    const x = Math.max(a.x, b.x), y = Math.max(a.y, b.y);
    const right = Math.min(a.x + a.width, b.x + b.width);
    const bottom = Math.min(a.y + a.height, b.y + b.height);
    return x < right && y < bottom ? {x, y, width: right - x, height: bottom - y} : null;
}

// The parts of `r` outside `c`: up to four rectangles that do not overlap.
function cut(r, c) {
    const o = clip(r, c);
    if (!o)
        return [r];
    const out = [];
    if (r.y < o.y)
        out.push({x: r.x, y: r.y, width: r.width, height: o.y - r.y});
    if (o.y + o.height < r.y + r.height)
        out.push({x: r.x, y: o.y + o.height, width: r.width, height: r.y + r.height - o.y - o.height});
    if (r.x < o.x)
        out.push({x: r.x, y: o.y, width: o.x - r.x, height: o.height});
    if (o.x + o.width < r.x + r.width)
        out.push({x: o.x + o.width, y: o.y, width: r.x + r.width - o.x - o.width, height: o.height});
    return out;
}

// The share (0..1) of `rect` that the rectangles in `covers` leave showing.
export function shownShare(rect, covers) {
    if (!rect || rect.width <= 0 || rect.height <= 0)
        return 0;
    let left = [box(rect)];
    for (const c of covers) {
        left = left.flatMap(r => cut(r, c));
        if (left.length > MOST_PIECES)
            return 1;
    }
    const area = left.reduce((sum, r) => sum + r.width * r.height, 0);
    return area / (rect.width * rect.height);
}
