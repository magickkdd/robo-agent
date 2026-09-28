"""Colour-blob segmentation of a rendered frame — a sensor algorithm, not a shortcut.

Why this exists in a VLM phase: the ablation arm `wo_vlm` still needs to be able to
*see* something, and every offline test needs a deterministic reader that costs no
money and makes no network call. A colour-blob detector over the rendered pixels is
the honest answer to both: it reads the same PNG a model reads, it is reproducible to
the bit, and its failure modes are the instructive ones (it cannot tell a cube from a
cuboid of the same colour, and it says nothing at all about anything not colour-coded).

It uses no simulator state: not the segmentation index map `getCameraImage` also
returns, not body poses. `mask_summary`'s fragmentation statistics are computed from
the same pixels, which is how "this blob is really two pieces, something is standing
in front of it" becomes evidence instead of an assumption.
"""
from __future__ import annotations

import numpy as np


def _hue_sat_value(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorised HSV (hue in degrees, s/v in [0,1]) for a float or uint8 RGB image."""
    c = np.asarray(rgb, dtype=float) / 255.0 if np.asarray(rgb).dtype == np.uint8 \
        else np.clip(np.asarray(rgb, dtype=float), 0.0, 1.0)
    mx = c.max(axis=-1)
    mn = c.min(axis=-1)
    diff = mx - mn
    # a grey pixel has diff == 0 and no hue at all; dividing by 1.0 there keeps the
    # arithmetic finite (each band below masks those pixels out anyway) so a frame
    # full of table does not raise a "invalid value in divide" warning per call
    safe = np.where(diff > 0, diff, 1.0)
    sat = np.where(mx > 0, diff / np.maximum(mx, 1e-9), 0.0)
    r, g, b = c[..., 0], c[..., 1], c[..., 2]
    hue = np.zeros_like(mx)
    band = (mx == r) & (diff > 0)
    hue[band] = (60.0 * ((g - b) / safe) % 360.0)[band]
    band = (mx == g) & (diff > 0)
    hue[band] = (60.0 * ((b - r) / safe) + 120.0)[band]
    band = (mx == b) & (diff > 0)
    hue[band] = (60.0 * ((r - g) / safe) + 240.0)[band]
    return hue, sat, mx


def hue_of(colour_rgb) -> float:
    h, _, _ = _hue_sat_value(np.array([[colour_rgb]], dtype=float))
    return float(h[0, 0])


def colour_mask(rgb: np.ndarray, colour_rgb, *, hue_tol_deg: float = 22.0,
                min_saturation: float = 0.32, min_value: float = 0.12) -> np.ndarray:
    """Pixels that read as this declared colour, allowing for flat shading.

    Hue and saturation, not RGB distance: the tiny renderer multiplies a face by its
    lighting term, so one object's top and side are two different RGB values with the
    same hue. Judging in RGB would cut each body into pieces; judging in hue keeps the
    body whole and keeps grey metal and beige table out.
    """
    hue, sat, val = _hue_sat_value(rgb)
    target = hue_of(colour_rgb)
    d = np.abs((hue - target + 180.0) % 360.0 - 180.0)      # circular hue distance
    return (d <= float(hue_tol_deg)) & (sat >= min_saturation) & (val >= min_value)


def mask_summary(mask: np.ndarray) -> dict:
    """Area, tight box, and how broken up the blob is.

    `rows_split` counts rows where the mask appears in two or more runs. A convex
    body seen whole has one run per row, so `rows_split > 0` means the outline is
    broken: something is in front of it, or it left the frame. That is a *pixel*
    observation — no id map, no pose — and it is what lets the percept say
    "partly hidden" instead of reporting a confident box around two disjoint pieces.
    """
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return {"pixels": 0, "bbox": None, "fill_ratio": 0.0, "rows_split": 0,
                "cols_split": 0, "max_runs_per_row": 0, "max_runs_per_col": 0,
                "touches_frame": []}
    padded_w = np.zeros((mask.shape[0], mask.shape[1] + 2), dtype=bool)
    padded_w[:, 1:-1] = mask
    padded_h = np.zeros((mask.shape[0] + 2, mask.shape[1]), dtype=bool)
    padded_h[1:-1, :] = mask
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    area = (x1 - x0 + 1) * (y1 - y0 + 1)
    rising_row = (np.diff(padded_w.astype(np.int8), axis=1) == 1).sum(axis=1)
    rising_col = (np.diff(padded_h.astype(np.int8), axis=0) == 1).sum(axis=0)
    touches = [side for side, hit in (
        ("left", x0 == 0), ("right", x1 == mask.shape[1] - 1),
        ("top", y0 == 0), ("bottom", y1 == mask.shape[0] - 1)) if hit]
    return {"pixels": int(xs.size), "bbox": [x0, y0, x1, y1],
            "fill_ratio": round(float(xs.size) / float(area), 4),
            "rows_split": int((rising_row > 1).sum()),
            "cols_split": int((rising_col > 1).sum()),
            "max_runs_per_row": int(rising_row.max()), "max_runs_per_col": int(rising_col.max()),
            "touches_frame": touches}


def colour_bboxes(rgb: np.ndarray, colours: dict[str, tuple[float, float, float]],
                  *, min_pixels: int = 40, **mask_kwargs) -> list[dict]:
    """One row per declared colour that appears in the frame with real area.

    `min_pixels` is the floor below which a few anti-aliased edge pixels do not count
    as an object. The list is ordered by pixel count, largest first: the biggest blob
    of a colour is the object, and everything smaller than it of the same colour is
    already accounted for by `max_runs_per_row`."""
    rows = []
    for name, colour in sorted(colours.items()):
        st = mask_summary(colour_mask(rgb, colour, **mask_kwargs))
        if st["pixels"] >= min_pixels:
            rows.append({"color": name, **st})
    return sorted(rows, key=lambda r: -r["pixels"])
