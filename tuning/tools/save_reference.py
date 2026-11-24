"""把校對後的字幕文字和偵測到的時間合併，存成翻譯基準參考稿。

  .venv\\Scripts\\python.exe tuning\\tools\\save_reference.py 偵測資料夾 校對文字檔 輸出.json

校對文字檔：一行一句，格式「編號<Tab或空白>正確文字」；文字留空＝那句是誤判，丟掉。
相鄰兩句文字相同（字幕閃爍被切成兩段）會合併。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def main() -> None:
    segdir, proof, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    segs = {s["i"]: s for s in json.loads((segdir / "segments.json").read_text(encoding="utf-8"))}
    fixed: dict[int, str] = {}
    for line in proof.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*(\d+)\s*(.*)$", line)
        if m:
            fixed[int(m.group(1))] = m.group(2).strip()
    refs: list[dict] = []
    for i in sorted(segs):
        text = fixed.get(i, "")
        if not text:
            continue
        s = segs[i]
        if refs and refs[-1]["text"] == text and s["start"] - refs[-1]["end"] < 1.0:
            refs[-1]["end"] = s["end"]
            continue
        refs.append({"start": s["start"], "end": s["end"], "text": text})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(refs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(refs)} 句 → {out}")


if __name__ == "__main__":
    main()
