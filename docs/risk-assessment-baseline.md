# Risk assessment development baseline

## Repository baseline

- Development starting commit: `726e3320f38b6272773a14344ccdc8b252375cdf`
- Branch: `main`
- Remote: `https://github.com/adamzoeee/anjing-vision.git`
- Git identity verified before the first commit: `syh99a <3611715586@qq.com>`
- The pre-existing `backend/pipeline/semantic.py` working-tree change was preserved, tested, and committed separately as `5c52123`.

## Test baseline

The backend suite was run from `backend/` with an explicit repository-local pytest temporary directory. The observed result is:

```text
241 passed, 4 failed
```

The four failures are known pre-existing expectation mismatches:

1. `test_measurement_builder.py::test_references_compute_one_global_scale`
2. `test_measurement_builder.py::test_validation_reference_is_not_used_to_compute_scale`
3. `test_semantic_evidence.py::test_keyframe_selection_is_uniform_bounded_and_never_duplicates_low_fps_input`
4. `test_structure_builder.py::test_unlabelled_floor_obstacle_is_kept_for_risk_analysis`

The task brief listed six known failures. The current checkout therefore has two fewer failures than that historical count. New work must introduce zero additional failures; the four items above remain the comparison set and are not to be altered merely to make the suite green.

## Current pipeline status

| Area | Existing implementation | Current status | Development decision |
| --- | --- | --- | --- |
| Structure-only passage analysis | `pipeline/space_foundation.py` | Produces door-to-bed route, narrowest width, connected area, and furniture clearances without reading point clouds | Reuse as the structured geometry source |
| Legacy point-cloud passage analysis | `pipeline/passage_metrics.py`, `pipeline/passage_builder.py` | Upstream/legacy measurement code reads PLY | Do not call from the new risk-assessment layer |
| Measurement gating | `pipeline/measurement_builder.py` | Produces verified measurements and a legacy risk-input adapter | Preserve compatibility; add formal metrics separately |
| Risk rules | `pipeline/rules.py` | Eight legacy rules; red/yellow/green vocabulary; legacy 40/40/20 categories | Replace official path with centralized versioned rules and low/medium/high output while retaining explicit legacy compatibility |
| Official scoring | `pipeline/rules.py` | Legacy worst-item scoring | Implement a single official 40/30/30 score with insufficient-data semantics |
| Pipeline integration | `app/tasks/pipeline_runner.py` | Risk identification and scoring are explicitly deferred | Wire the unified backend result after structured artifacts exist |
| Flutter report | `app/lib/pages/report_page.dart` | Displays the legacy report shape | Make it a renderer of the backend unified payload only |
| PDF | `pipeline/pdf_report.py` | Contains duplicated risk labels/advice | Consume the unified payload; do not make independent decisions |

## Hard boundaries

The formal metric and risk implementation may consume only existing structured outputs such as `structure.json`, `structure_calibrated.json`, `measurements.json`, `passage_analysis.json`, and `spatial_foundation.json`. It must not read or re-analyse video, images, masks, camera poses, point clouds, SLAM3R output, SpatialLM output, or Gaussian artifacts.

Missing evidence is represented as `not_evaluable`, never as safe. If core evidence is insufficient, the official overall score is `null` and the status is `insufficient_data`.
