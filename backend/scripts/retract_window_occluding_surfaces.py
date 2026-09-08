"""Retract only provenance-confirmed artificial points inside the window ROI."""
import json
import hashlib
import sys
import numpy as np
import open3d as o
from audit_scan46_observation_planes import ROOT,POST

def main():
    dest=ROOT/'data/work/46/window_local_sequence_20260907'
    source=dest/(sys.argv[1] if len(sys.argv)>1 else 'window_local_components_extent.ply')
    cloud=o.io.read_point_cloud(str(source));p=np.asarray(cloud.points);c=np.asarray(cloud.colors)
    old=o.io.read_point_cloud(str(POST/'scene_preview_local_surface_repair_candidate.ply'))
    manifest=json.loads((POST/'scene_preview_local_surface_repair_candidate.json').read_text())
    assert np.array_equal(p[:621290],np.asarray(old.points)[:621290])
    assert np.array_equal(c[:621290],np.asarray(old.colors)[:621290])
    new=p[633278:]
    low=new.min(0)-.035;high=new.max(0)+.035
    # Only the window's depth band; no removal on the physical floor (Y~1.27).
    low[2]=-.65;high[2]=.20
    roi=np.all((p>=low)&(p<=high),axis=1)
    remove=np.zeros(len(p),bool);counts={};start=manifest['baseline_points_preserved']
    for name,count in manifest['additions'].items():
        end=start+count
        if name=='bed_top':break  # Already retracted historically, not in this prefix.
        ids=np.arange(start,end);ids=ids[roi[ids]]
        remove[ids]=True;counts[name]=len(ids);start=end
    assert not remove[:507637].any()  # Every original measured point stays.
    assert not remove[621290:].any()  # Floor observations and this window stay.
    assert not (remove&~roi).any()
    keep=~remove
    target=dest/(source.stem+'_unoccluded.ply');assert not target.exists()
    result=o.geometry.PointCloud(o.utility.Vector3dVector(p[keep]));result.colors=o.utility.Vector3dVector(c[keep])
    assert o.io.write_point_cloud(str(target),result)
    check=o.io.read_point_cloud(str(target))
    assert np.array_equal(np.asarray(check.points),p[keep])
    assert np.array_equal(np.asarray(check.colors),c[keep])
    np.save(target.with_suffix('.removed_indices.npy'),np.flatnonzero(remove))
    report={'removed_by_provenance':counts,'removed_total':int(remove.sum()),'remaining':int(keep.sum()),
        'roi_bounds':[low.tolist(),high.tolist()],'original_measured_points_preserved':507637,
        'outside_roi_xyz_rgb_exact':True,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'candidate_sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
    target.with_suffix('.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))

if __name__=='__main__':main()
