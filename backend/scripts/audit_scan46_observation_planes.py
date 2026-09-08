"""Recover physical surface planes from image-identified observations, never axis labels."""
import json
from pathlib import Path
import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
POST = ROOT / 'data/work/46/postprocess'
OUT = ROOT / 'data/work/46/coordinate_repair_20260905'

def load_inputs():
    pred = ROOT / 'data/work/45/slam3r/scene/preds'
    maps = np.load(pred / 'registered_pcds.npy', mmap_mode='r')
    images = np.load(pred / 'input_imgs.npy', mmap_mode='r')
    alignment = json.loads((ROOT/'data/work/45/postprocess/alignment.json').read_text())
    rotation = np.array(alignment['alignment']['rotation'])
    scale = alignment['scale']['applied']
    shift = np.array([0., 0., -alignment['alignment']['floor_z_raw_aligned']])*scale
    return maps, images, rotation*scale, shift

def fit_plane(points):
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
    o3d.utility.random.seed(46)
    plane, ids = cloud.segment_plane(.018, 3, 2000)
    p = points[ids]
    center = p.mean(0)
    _, _, vt = np.linalg.svd(p-center, full_matrices=False)
    normal = vt[-1]
    if normal[np.argmax(abs(normal))] < 0:
        normal = -normal
    return np.r_[normal, -normal@center], p

def main():
    maps, images, rotation, shift = load_inputs()
    base = o3d.io.read_point_cloud(str(POST/'scene_preview_local_surface_repair_candidate.ply'))
    xyz = np.asarray(base.points)
    tree = cKDTree(xyz)
    report = {}
    for name, frame, rects in [
        ('floor',199,[(40,210,15,200)]),
        ('window',687,[(26,34,68,210),(40,205,55,63)]),
    ]:
        metric = np.asarray(maps[frame],float)@rotation.T+shift
        pixels = np.concatenate([metric[a:b,c:d].reshape(-1,3) for a,b,c,d in rects])
        plane, support = fit_plane(pixels)
        distances, ids = tree.query(support)
        anchors = xyz[np.unique(ids[distances<.025])]
        print(name,'observed',plane,'anchors',len(anchors),'distance',np.quantile(distances,[.5,.9]))
        if len(anchors)<50:
            report[name]={'rejected':'insufficient baseline anchors','observed_plane':plane.tolist(),'nearest_distance_m':np.quantile(distances,[.5,.9]).tolist()}
            continue
        base_plane, base_support = fit_plane(anchors)
        report[name] = dict(frame=frame, observed_plane=plane.tolist(),
            baseline_plane=base_plane.tolist(), baseline_support=len(base_support),
            observed_inliers=len(support), observed_bounds=np.quantile(support,[.02,.98],axis=0).tolist(),
            baseline_bounds=np.quantile(base_support,[.02,.98],axis=0).tolist(),
            normal_difference_deg=float(np.degrees(np.arccos(np.clip(abs(plane[:3]@base_plane[:3]),0,1)))),
            baseline_distances=np.quantile(distances,[.5,.9,.95]).tolist())
    OUT.mkdir(exist_ok=True)
    (OUT/'observation_planes.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__ == '__main__':
    main()
