"""從影片畫面擷取「燒在畫面上」的字幕：偵測每句字幕出現／消失的時間，OCR 出草稿，並產生校對用的拼圖。

  .venv\\Scripts\\python.exe tuning\\tools\\extract_burned_subs.py 影片.mp4 輸出資料夾

輸出：
  輸出資料夾/segments.json   每句 {i, start, end, ocr}，start/end 為秒
  輸出資料夾/crops/NNN.png   每句字幕的截圖
  輸出資料夾/sheets/NN.png   每張 16 句的校對拼圖（左上角是句子編號）

字幕假設：畫面下方、淺色字＋深色外框（鈐 Mae 頻道的樣式）。OCR 只是草稿，需要人工校對。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

STEP = 0.2            # 每 0.2 秒取一張
REGION = (0.80, 0.99)  # 字幕在畫面高度的 80%～99%
MIN_PIXELS = 400       # 字幕像素少於這個數量 = 沒有字幕
CHANGE_IOU = 0.55      # 前後兩張的字形重疊率低於這個值 = 換了一句
MIN_DUR = 0.4          # 少於 0.4 秒的不算一句（轉場）


def text_mask(crop: np.ndarray) -> np.ndarray:
    """淺青色字且附近有深色外框的像素。"""
    rgb = crop.astype(np.int16)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    # 鈐 Mae 的字幕是淺青色（約 RGB 170,250,232）：G、B 高、R 明顯較低；白色面板（R≈G≈B）不算
    bright = (g > 215) & (b > 190) & (g - r > 45)
    dark = rgb.mean(axis=2) < 70
    near_dark = dark.copy()
    for dy in range(-3, 4):          # 簡易膨脹：外框附近 3 像素
        for dx in range(-3, 4):
            near_dark |= np.roll(np.roll(dark, dy, 0), dx, 1)
    return bright & near_dark


def iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return inter / union if union else 1.0


def scan(video: str):
    c = av.open(video)
    s = c.streams.video[0]
    next_t = 0.0
    for f in c.decode(s):
        t = float(f.pts * s.time_base)
        if t < next_t:
            continue
        next_t = t + STEP
        img = f.to_ndarray(format="rgb24")
        h = img.shape[0]
        crop = img[int(h * REGION[0]):int(h * REGION[1])]
        yield t, crop


def segments(video: str):
    segs, cur = [], None
    for t, crop in scan(video):
        m = text_mask(crop)
        has = m.sum() >= MIN_PIXELS
        if cur and (not has or iou(m, cur["mask"]) < CHANGE_IOU):
            cur["end"] = t
            if cur["end"] - cur["start"] >= MIN_DUR:
                segs.append(cur)
            cur = None
        if has and cur is None:
            cur = {"start": t, "mask": m, "frames": []}
        if cur is not None:
            cur["frames"].append(crop)
    if cur:
        cur["end"] = cur["start"] + STEP * len(cur["frames"])
        segs.append(cur)
    return segs


def ocr_reader():
    import easyocr

    return easyocr.Reader(["ch_tra", "en"], gpu=True, verbose=False)


def ocr(reader, crop: np.ndarray) -> str:
    g = ImageOps.autocontrast(Image.fromarray(crop).convert("L"))
    g = g.resize((g.width * 2, g.height * 2), Image.LANCZOS).convert("RGB")
    return "".join(x[1] for x in reader.readtext(np.asarray(g))).replace(" ", "")


def make_sheets(crops: list[Path], out: Path, per: int = 16, width: int = 900) -> None:
    out.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()
    for k in range(0, len(crops), per):
        ims = []
        for p in crops[k:k + per]:
            im = Image.open(p)
            im = im.resize((width, int(im.height * width / im.width)))
            canvas = Image.new("RGB", (width, im.height + 2), "white")
            canvas.paste(im, (0, 0))
            d = ImageDraw.Draw(canvas)
            d.rectangle((0, 0, 44, 16), fill="black")
            d.text((3, 2), p.stem, fill="yellow", font=font)
            ims.append(canvas)
        sheet = Image.new("RGB", (width, sum(i.height for i in ims)), "white")
        y = 0
        for i in ims:
            sheet.paste(i, (0, y))
            y += i.height
        sheet.save(out / f"{k // per:02d}.png")


def main() -> None:
    video, outdir = sys.argv[1], Path(sys.argv[2])
    (outdir / "crops").mkdir(parents=True, exist_ok=True)
    segs = segments(video)
    reader = ocr_reader()
    result, crops = [], []
    for i, sg in enumerate(segs):
        mid = sg["frames"][len(sg["frames"]) // 2]
        p = outdir / "crops" / f"{i:03d}.png"
        Image.fromarray(mid).save(p)
        crops.append(p)
        result.append({"i": i, "start": round(sg["start"], 2), "end": round(sg["end"], 2), "ocr": ocr(reader, mid)})
    (outdir / "segments.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    make_sheets(crops, outdir / "sheets")
    print(f"{len(result)} 句字幕 → {outdir}")


if __name__ == "__main__":
    main()
