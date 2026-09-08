"""把已经准备好的四段补拍序列拆成四个独立 SLAM3R 候选。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/work/46/supplement_buchong2_v1"


if __name__ == "__main__":
    payload = json.loads((SOURCE / "supplement_mapping.json").read_text(encoding="utf-8"))
    for clip_id in range(4):
        target = ROOT / f"data/work/46/supplement_buchong2_clip{clip_id}"
        frames = target / "frames"
        frames.mkdir(parents=True, exist_ok=True)
        selected = [item for item in payload["mapping"] if int(item["clip_id"]) == clip_id]
        mapping = []
        for output_index, item in enumerate(selected):
            source = SOURCE / "frames" / item["output"]
            output = f"frame_{output_index + 1:05d}.jpg"
            shutil.copy2(source, frames / output)
            updated = dict(item)
            updated["output_index"] = output_index
            updated["output"] = output
            mapping.append(updated)
        result = {
            "status": "prepared", "method": "isolated_single_clip_anchored_sequence",
            "source": str(SOURCE), "clip_id": clip_id, "output_frames": len(mapping),
            "anchor_frames": sum(x["kind"] == "anchor" for x in mapping),
            "supplement_frames": sum(x["kind"] == "supplement" for x in mapping),
            "mapping": mapping,
        }
        (target / "supplement_mapping.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({k: v for k, v in result.items() if k != "mapping"}, ensure_ascii=False))
