"""Run the isolated scan-46 supplemental reconstruction.

This job reconstructs only anchored frames from ``buchong2``.  It does not
modify the accepted scan preview or any report/structure artifacts.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.slam3r_runner import run_reconstruction


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", nargs="?", type=Path, default=ROOT / "data/work/46/supplement_buchong2_v1")
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    result = run_reconstruction(
        candidate / "frames",
        candidate / "slam3r",
        test_name="scene",
    )
    print(json.dumps({
        "status": "done",
        "ply": str(result["ply"]),
        "seconds": result["seconds"],
    }, ensure_ascii=False))
