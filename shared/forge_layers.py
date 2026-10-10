"""2.5D wallpapers (S23): the picture split into a subject and a sky, once.

the owner (2026-09-27): "separate things from the wallpaper like people to make
2.5d or 3d effects". Once per wallpaper, on the CPU at low priority, this
makes three files beside the wallpaper's cached copy
(~/.cache/forge/wallpaper/<key>.jpg, shared/forge_theme.render):

    <key>-fg.webp     the subject with see-through everywhere else
    <key>-bg.jpg      the picture with the subject filled in, so nothing is
                      seen twice when the two layers move apart
    <key>-depth.png   how near each part is (light = near), for later use

The hub stacks them: the background, the particles, then the subject, and
moves the subject further than the background as the pointer moves (and on
the beat in Alt+V), so the particles fly between the character and the sky.

What counts as the subject: the anime cut-out model's mask when it finds a
character of a sensible size, else the nearest part of the depth map (a
tree, a ledge), else nothing (the wallpaper stays flat).

Nothing is downloaded or installed. The models are the ones PanelForge
already keeps (~/PanelForgeData/models: skytnt/anime-seg isnetis.onnx, the
model rembg ships as isnet-anime, and Depth Anything V2 from
onnx-community), and the work runs in whichever Forge app's .venv already
has onnxruntime, numpy and Pillow (find_python), so no copies are made.

    python3 shared/forge_layers.py make <image> <key> <folder>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

FORGE = Path(__file__).resolve().parents[1]
MODELS = Path(
    os.environ.get("FORGE_LAYER_MODELS", Path.home() / "PanelForgeData" / "models")
)
# nearest first: the small depth models are quicker, the base one sharper
SEG_MODELS = ("scene-seg/isnetis.onnx",)
DEPTH_MODELS = (
    "scene-depth_small/model.onnx",
    "scene-depth/model_int8.onnx",
    "scene-depth_base/model.onnx",
)
NEEDS = ("onnxruntime", "numpy", "PIL")
# a character this share of the picture is a subject; smaller is a speck,
# larger is most likely the model mistaking the scene for a person
SUBJECT_MIN, SUBJECT_MAX = 0.015, 0.6
# with no character, the nearest this share of the depth map is the subject
NEAR_SHARE = 0.18
THREADS = 2  # onnxruntime threads: the laptop stays usable while it works


def layer_paths(key: str, folder: Path) -> dict[str, Path]:
    return {
        "fg": folder / f"{key}-fg.webp",
        "bg": folder / f"{key}-bg.jpg",
        "depth": folder / f"{key}-depth.png",
        "info": folder / f"{key}-layers.json",
        "lock": folder / f"{key}-layers.lock",
    }


def ready(key: str, folder: Path) -> dict | None:
    """The finished layers' info ({"subject": "character" | "near" | "none"}),
    or None while they are not made (or making them failed)."""
    p = layer_paths(key, folder)
    try:
        info = json.loads(p["info"].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if info.get("subject") == "none":
        return info
    return info if p["fg"].is_file() and p["bg"].is_file() else None


def find_models(root: Path = MODELS) -> dict[str, Path] | None:
    seg = next((root / m for m in SEG_MODELS if (root / m).is_file()), None)
    depth = next((root / m for m in DEPTH_MODELS if (root / m).is_file()), None)
    return {"seg": seg, "depth": depth} if seg and depth else None


def find_python(apps: Path = FORGE / "apps") -> str | None:
    """A Forge app's own Python that already has onnxruntime, numpy and
    Pillow, so nothing is installed twice."""
    for py in sorted(apps.glob("*/.venv/bin/python")):
        libs = list((py.parent.parent / "lib").glob("python3*/site-packages"))
        if libs and all((libs[0] / name).is_dir() for name in NEEDS):
            return str(py)
    return None


def start(
    image: Path, key: str, folder: Path, popen: Callable[..., Any] = subprocess.Popen
) -> bool:
    """Make the layers in the background unless they exist, are being made,
    or failed before (then the picture stays flat, never retried in a loop).
    Returns whether a job started."""
    p = layer_paths(key, folder)
    if p["info"].exists() or not Path(image).is_file():
        return False
    try:  # a lock younger than 10 minutes: a job is running
        if time.time() - p["lock"].stat().st_mtime < 600:
            return False
    except OSError:
        pass
    python, models = find_python(), find_models()
    if not python or not models:
        return False
    folder.mkdir(parents=True, exist_ok=True)
    p["lock"].write_text(str(time.time()))
    with open(folder / f"{key}-layers.log", "w") as log:
        popen(
            [
                "nice",
                "-n",
                "19",
                python,
                __file__,
                "make",
                str(image),
                key,
                str(folder),
            ],
            stdout=subprocess.DEVNULL,
            stderr=log,
            start_new_session=True,
        )
    return True


# ----------------------------------------------------------------------------
# the work itself, in the Python find_python() picked


def pick_subject(seg, depth):  # arrays 0..1, same shape
    """The subject mask and what it is: the character when the cut-out
    found one of a sensible size, else the nearest part of the scene."""
    import numpy as np

    share = float((seg > 0.5).mean())
    if SUBJECT_MIN <= share <= SUBJECT_MAX:
        return seg, "character"
    near = float(np.quantile(depth, 1 - NEAR_SHARE))
    far = float(np.quantile(depth, 0.5))
    if near - far < 0.15:  # a flat scene: nothing stands out
        return np.zeros_like(depth), "none"
    soft = np.clip((depth - near) / 0.08 + 0.5, 0, 1)
    return soft, "near"


def _run_seg(session, img):
    import numpy as np

    w, h = img.size
    side = max(w, h)
    sq = np.zeros((side, side, 3), np.float32)
    sq[:h, :w] = np.asarray(img, np.float32) / 255
    from PIL import Image

    x = np.asarray(
        Image.fromarray((sq * 255).astype(np.uint8)).resize((1024, 1024)), np.float32
    )
    x = (x / 255).transpose(2, 0, 1)[None]
    m = session.run(None, {session.get_inputs()[0].name: x})[0][0][0]
    m = Image.fromarray((np.clip(m, 0, 1) * 255).astype(np.uint8)).resize((side, side))
    return np.asarray(m, np.float32)[:h, :w] / 255


def _run_depth(session, img):
    import numpy as np
    from PIL import Image

    w, h = img.size
    x = np.asarray(img.resize((518, 518), Image.Resampling.BICUBIC), np.float32) / 255
    x = (x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
    x = x.transpose(2, 0, 1)[None].astype(np.float32)
    d = session.run(None, {session.get_inputs()[0].name: x})[0][0]
    d = (d - d.min()) / max(1e-6, float(d.max() - d.min()))
    d = Image.fromarray((d * 255).astype(np.uint8)).resize(
        (w, h), Image.Resampling.BICUBIC
    )
    return np.asarray(d, np.float32) / 255


def _fill(img, mask, radius: int):
    """The picture with the masked part filled from around it: a blur that
    only counts the unmasked pixels (normalised convolution), widened until
    every hole is covered."""
    import numpy as np
    from PIL import Image, ImageFilter

    a = np.asarray(img, np.float32)
    keep = 1 - mask
    out = a.copy()
    todo = mask > 0.02
    r = radius
    while todo.any() and r < max(img.size):
        k = Image.fromarray((keep * 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(r)
        )
        kb = np.asarray(k, np.float32)[..., None] / 255
        ab = np.asarray(
            Image.fromarray((a * keep[..., None]).astype(np.uint8)).filter(
                ImageFilter.GaussianBlur(r)
            ),
            np.float32,
        )
        fill = ab / np.maximum(kb, 1e-3)
        got = todo & (kb[..., 0] > 0.05)
        out[got] = fill[got]
        todo &= ~got
        r *= 2
    m = mask[..., None]
    return Image.fromarray(np.clip(a * (1 - m) + out * m, 0, 255).astype(np.uint8))


def make(image: Path, key: str, folder: Path) -> dict:
    import numpy as np
    import onnxruntime as ort
    from PIL import Image, ImageFilter

    models = find_models()
    if not models:
        raise RuntimeError("the cut-out and depth models are not in " + str(MODELS))
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = THREADS
    opts.inter_op_num_threads = 1
    cpu = ["CPUExecutionProvider"]
    img = Image.open(image).convert("RGB")
    seg = _run_seg(ort.InferenceSession(str(models["seg"]), opts, providers=cpu), img)
    depth = _run_depth(
        ort.InferenceSession(str(models["depth"]), opts, providers=cpu), img
    )
    mask, subject = pick_subject(seg, depth)
    p = layer_paths(key, folder)
    Image.fromarray((depth * 255).astype(np.uint8)).save(p["depth"])
    info = {"subject": subject, "share": round(float((mask > 0.5).mean()), 4)}
    if subject != "none":
        # a soft edge, and the hole in the background a little wider than the
        # subject so no rim of it shows when the layers move apart
        w = img.size[0]
        soft = Image.fromarray((mask * 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(max(1, w // 1600))
        )
        fg = img.copy()
        fg.putalpha(soft)
        fg.save(p["fg"], "WEBP", quality=90, method=4)
        hole = Image.fromarray(((mask > 0.3) * 255).astype(np.uint8)).filter(
            ImageFilter.MaxFilter(max(3, (w // 180) | 1))
        )
        hole = hole.filter(ImageFilter.GaussianBlur(max(2, w // 400)))
        _fill(img, np.asarray(hole, np.float32) / 255, max(4, w // 160)).save(
            p["bg"], quality=88
        )
    p["info"].write_text(json.dumps(info), encoding="utf-8")
    return info


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[0] == "make":
        image, key, folder = Path(argv[1]), argv[2], Path(argv[3])
        p = layer_paths(key, folder)
        try:
            info = make(image, key, folder)
        except Exception as e:  # recorded, so it is not tried again and again
            p["info"].write_text(json.dumps({"subject": "none", "error": str(e)}))
            print(f"forge_layers: {e}", file=sys.stderr)
            return 1
        finally:
            p["lock"].unlink(missing_ok=True)
        # the hub picks the layers up from a fresh hub/theme.js
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import forge_theme

        forge_theme.apply(FORGE / "hub")
        print(json.dumps(info))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
