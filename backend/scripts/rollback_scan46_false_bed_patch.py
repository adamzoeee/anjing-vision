"""Retract the historical synthetic bed-top patch by provenance, not a crop."""
import hashlib
import json
import numpy as np
import open3d as o
from audit_scan46_observation_planes import POST,OUT

source=POST/'scene_preview_verified_observations_20260905.ply'
baseline=POST/'scene_preview_local_surface_repair_candidate.ply'
manifest=json.loads(baseline.with_suffix('.json').read_text())
cloud=o.io.read_point_cloud(str(source));xyz=np.asarray(cloud.points);rgb=np.asarray(cloud.colors)
base=o.io.read_point_cloud(str(baseline))
assert np.array_equal(xyz[:627241],np.asarray(base.points))
assert np.array_equal(rgb[:627241],np.asarray(base.colors))
start=manifest['baseline_points_preserved']
for name,count in manifest['additions'].items():
    if name=='bed_top':break
    start+=count
end=start+manifest['additions']['bed_top']
# The original generator writes a synthetic horizontal z plane after walls.
assert end==627241
assert np.ptp(xyz[start:end,2])<1e-9
keep=np.ones(len(xyz),bool);keep[start:end]=False
target=OUT/'scene_preview_retract_false_bed_patch.ply'
if target.exists():raise RuntimeError('Refuse overwrite')
out=o.geometry.PointCloud(o.utility.Vector3dVector(xyz[keep]));out.colors=o.utility.Vector3dVector(rgb[keep])
o.io.write_point_cloud(str(target),out)
check=o.io.read_point_cloud(str(target))
assert np.array_equal(np.asarray(check.points),xyz[keep])
assert np.array_equal(np.asarray(check.colors),rgb[keep])
report={'removed_synthetic_bed_top_indices':[start,end], 'removed':end-start,
 'remaining':int(keep.sum()),'retained_xyz_rgb_exact':True,
 'removed_bounds':np.array([xyz[start:end].min(0),xyz[start:end].max(0)]).tolist(),
 'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
 'candidate_sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
target.with_suffix('.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
