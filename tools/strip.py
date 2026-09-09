"""Нарезка кадров вокруг ТС в контактный лист — для ручной разметки эталона."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[0].parent / "src"))

from paxcount.visits import pick_primary, primary_boxes, vehicle_tracks  # noqa: E402
from paxcount.core.trackdata import cache_path, load  # noqa: E402
from paxcount.core.video import frames  # noqa: E402


def build(video: Path, t0: float, t1: float, step: float, cols: int, out: Path,
          tile_w: int = 320, pad: float = 0.15) -> Path:
    data = load(cache_path(video))
    boxes = primary_boxes(pick_primary(vehicle_tracks(data)), data, data.effective_fps)
    wanted = [t0 + i * step for i in range(int((t1 - t0) / step) + 1)]
    picked: list[np.ndarray] = []
    wi = 0

    for frame_idx, ts, frame in frames(video):
        if wi >= len(wanted):
            break
        if ts < wanted[wi]:
            continue
        wi += 1
        box = boxes.get(frame_idx)
        if box is None:
            crop = frame
        else:
            h, w = frame.shape[:2]
            bw, bh = box[2] - box[0], box[3] - box[1]
            x0 = int(max(0, box[0] - pad * bw)); y0 = int(max(0, box[1] - pad * bh))
            x1 = int(min(w, box[2] + pad * bw)); y1 = int(min(h, box[3] + pad * bh))
            crop = frame[y0:y1, x0:x1]
        if crop.size == 0:
            crop = frame
        scale = tile_w / crop.shape[1]
        crop = cv2.resize(crop, (tile_w, max(1, int(crop.shape[0] * scale))))
        cv2.putText(crop, f"{ts:.1f}s", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (0, 0, 255), 2)
        picked.append(crop)

    if not picked:
        raise SystemExit("нет кадров")
    th = max(c.shape[0] for c in picked)
    tiles = [
        cv2.copyMakeBorder(c, 0, th - c.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        for c in picked
    ]
    rows = []
    for i in range(0, len(tiles), cols):
        row = tiles[i : i + cols]
        while len(row) < cols:
            row.append(np.zeros_like(tiles[0]))
        rows.append(np.hstack(row))
    sheet = np.vstack(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return out


if __name__ == "__main__":
    video = Path(sys.argv[1])
    t0, t1, step = float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
    cols = int(sys.argv[5]); out = Path(sys.argv[6])
    print(build(video, t0, t1, step, cols, out))
