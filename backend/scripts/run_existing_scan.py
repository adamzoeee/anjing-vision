"""Run the pipeline for an existing scan without creating or uploading it again."""

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scan_id", type=int)
    args = parser.parse_args()

    os.chdir(ROOT)
    from app.tasks.pipeline_runner import run_pipeline

    run_pipeline(args.scan_id)


if __name__ == "__main__":
    main()
