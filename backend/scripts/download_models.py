"""下载 SAM checkpoint（segment-anything 官方）到 backend/models/。"""
import os
import urllib.request
from pathlib import Path

URLS = {
    "sam_vit_h_4b8939.pth": "https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth",
}
ROOT = Path(__file__).resolve().parent.parent  # backend/


def main():
    out = ROOT / "models"
    out.mkdir(exist_ok=True)
    for name, url in URLS.items():
        dest = out / name
        if dest.exists() and dest.stat().st_size > 1e8:
            print(f"跳过（已存在）: {name}")
            continue
        # 下载到 .part 临时文件，完成后再原子改名，避免中断留下损坏 checkpoint 被误判
        tmp = dest.with_suffix(dest.suffix + ".part")
        print(f"下载 {name} ({url}) ...")
        urllib.request.urlretrieve(url, tmp)
        os.replace(tmp, dest)
        print("完成")


if __name__ == "__main__":
    main()
