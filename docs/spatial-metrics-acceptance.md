# Second-stage spatial metric acceptance

## Acceptance scope

The formal spatial metric builder was executed against scan 46 using only:

- `measurements.json`
- `passage_analysis.json`
- `spatial_foundation.json`

The builder rejects non-JSON input paths. Input SHA256 hashes are recorded in the generated payload and the source files remain unchanged. No video, image, mask, camera pose, point cloud, SLAM3R, SpatialLM, semantic-model, or Gaussian artifact was accessed.

## Scan 46 result

| Metric | Status | Value |
| --- | --- | ---: |
| Main passage width | derived | 0.480 m |
| Minimum passage width | derived | 0.480 m |
| Door width | measured | 0.846 m |
| Entrance space | derived | 2.163 m² |
| Path length | derived | 1.360 m |
| Path continuity | derived | true |
| Path obstruction | derived | false |
| Furniture spacing | derived | 0.040 m |
| Wall-furniture clearance | derived | 0.000 m |
| Bed-wall distance | derived | 0.000 m |
| Bedside clearance | derived | 0.300 m |
| Activity area | not_evaluable | explicit activity anchor missing |
| Crowding | derived | 0.5315 |
| Bed surrounding space | derived | 0.000 m |
| Main activity area safety | not_evaluable | explicit activity anchor missing |

Coverage is `13/15` (`86.7%`). The two unavailable metrics are retained in the output with the reason `explicit_activity_anchor_missing`; neither is counted as safe.

## Path normalization

The existing `door_to_bed` route is normalized as complete:

- start: `door_01`
- target: `bed_001`
- length: `1.36 m`
- continuous: `true`
- obstructed: `false`
- bottleneck width: `0.48 m`
- bottleneck position: `[1.8156, 0.639]`

`entrance_to_activity` is emitted as `not_evaluable` because scan 46 has no explicit activity-area anchor. The room centre is not used as a substitute.

## Acceptance conclusion

The second-stage metric layer provides all 15 required metric records, normalized paths, source traceability, confidence slots, explicit missing-data reasons, and coverage accounting. It is ready to serve as the sole input to formal risk evaluation.
