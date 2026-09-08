"""Estimate cached observations to immutable baseline; output diagnostics only."""
import json
import numpy as np
import open3d as o
from audit_scan46_observation_planes import load_inputs, POST, OUT

def prepare(p):
    p=p.voxel_down_sample(.05)
    p.estimate_normals(o.geometry.KDTreeSearchParamHybrid(radius=.12,max_nn=40))
    return p,o.pipelines.registration.compute_fpfh_feature(p,o.geometry.KDTreeSearchParamHybrid(radius=.25,max_nn=80))

def main():
    maps,images,R,t=load_inputs()
    pts=np.concatenate([np.asarray(maps[i]).reshape(-1,3)[::12] for i in range(0,900,15)])
    pts=pts@R.T+t
    src=o.geometry.PointCloud(o.utility.Vector3dVector(pts))
    target=o.io.read_point_cloud(str(POST/'scene_preview_local_surface_repair_candidate.ply'))
    target=o.geometry.PointCloud(o.utility.Vector3dVector(np.asarray(target.points)[:507637]))
    s,sf=prepare(src); d,df=prepare(target)
    reg=o.pipelines.registration
    before=reg.evaluate_registration(s,d,.07,np.eye(4))
    o.utility.random.seed(46)
    found=reg.registration_ransac_based_on_feature_matching(s,d,sf,df,True,.10,
        reg.TransformationEstimationPointToPoint(False),3,
        [reg.CorrespondenceCheckerBasedOnEdgeLength(.9),reg.CorrespondenceCheckerBasedOnDistance(.10)],
        reg.RANSACConvergenceCriteria(50000,.999))
    refined=reg.registration_icp(s,d,.06,found.transformation,reg.TransformationEstimationPointToPlane(),reg.ICPConvergenceCriteria(max_iteration=60))
    report={'before_fitness':before.fitness,'before_rmse':before.inlier_rmse,'ransac_fitness':found.fitness,
        'after_fitness':refined.fitness,'after_rmse':refined.inlier_rmse,'transform':refined.transformation.tolist()}
    OUT.mkdir(exist_ok=True)
    (OUT/'registration_contract.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
