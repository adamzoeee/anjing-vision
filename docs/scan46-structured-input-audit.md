# Scan 46 structured-input audit

## Scope

The regression package is located at:

`E:\anlingzhijing\anjing-vision\scan46_完整数据包`

It is outside the formal repository and is never copied into Git. Although the package also contains video, images, point clouds, model output, and intermediate reconstruction files, the risk-assessment regression is restricted to the final structured files under `data/work/46/postprocess`.

## Allowed regression inputs

| Artifact | Purpose |
| --- | --- |
| `structure.json` | Room, openings, semantic instances, positions, and accepted geometry |
| `structure_calibrated.json` | Meter-calibrated structure and reference validation |
| `measurements.json` | Verified room/opening/object measurements and confidence gates |
| `passage_analysis.json` | Structure-only path, bottleneck, walkable area, and furniture clearances |
| `spatial_foundation.json` | Normalized room, doors, furniture, passage, and risk-input summary |

Files such as PLY, MP4, extracted frames, masks, camera poses, SLAM3R output, SpatialLM output, and Gaussian artifacts are explicitly out of scope for formal metric/risk evaluation.

## Observed structured facts

- `structure.json`: 4 walls, 1 door, 1 window, 5 legacy objects, 6 semantic instances, and 8 rejected objects.
- `measurements.json`: meter scale is available; 2 openings and 6 objects are present.
- `passage_analysis.json`: status `ok`, analysis basis `existing_2d_structure_only`.
- Primary route: `door_to_bed`, from `door_01` to `bed_001`.
- Primary route length: `1.36 m`.
- Primary route minimum clear width: `0.48 m`.
- Door-connected walkable area: `2.163 m²`.
- Furniture clearances contain 15 relationships; examples include `bookshelf_002` to `desk_002` at `0.04 m`, and `bed_001` to `table_002` at `0.30 m`.

The imported legacy report has score `50.0` and marks most path-related items not evaluable. That report predates the richer structure-only passage artifact and is treated as historical output, not the expected result of the new official evaluator.

## Regression expectations

1. Existing source files remain byte-identical after evaluation; provenance hashes are checked where provided.
2. The evaluator derives metrics only from the five allowed JSON artifacts.
3. Missing activity anchors do not become a fabricated room-centre route; `entrance_to_activity` must be `not_evaluable` unless an explicit anchor exists.
4. Missing threshold, step, slope, or obstacle evidence remains `not_evaluable` and does not receive a safe score.
5. Every risk trace refers back to a formal metric and, where applicable, an object ID or path ID already present in structured data.
