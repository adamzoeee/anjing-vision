import sys
from pathlib import Path
import numpy as np
import cv2
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from local_gap_geometry import intersect_plane,estimate_pose
import local_gap_geometry as gaps

def test_local_replacement_preserves_all_points_outside_authorized_seam():
    import pytest
    p=np.arange(12).reshape(4,3);c=p/12
    remove=np.array([False,True,False,False]);allowed=remove.copy()
    q,r=gaps.replace_local(p,c,remove,allowed,np.array([[8,8,8]]),np.ones((1,3)))
    np.testing.assert_array_equal(q[:3],p[~remove]);np.testing.assert_array_equal(r[:3],c[~remove])
    with pytest.raises(ValueError):
        gaps.replace_local(p,c,remove,np.zeros(4,bool),np.empty((0,3)),np.empty((0,3)))

def test_surface_hole_ignores_sampling_gaps_but_keeps_large_missing_patch():
    occupied=np.zeros((100,100),bool)
    occupied[::4,::4]=True
    occupied[30:70,30:70]=False
    roi=np.zeros_like(occupied);roi[10:90,10:90]=True
    area=gaps.largest_surface_hole(occupied,roi,5)
    assert 1400<area<2000
    occupied[::4,::4]=True
    assert gaps.largest_surface_hole(occupied,roi,5)==0

def test_patch_support_counts_distinct_views_not_duplicate_points():
    p=np.array([[.01,0,0],[.02,0,0],[.03,0,0],[1.01,0,0],[1.02,0,0],[1.03,0,0]])
    c=np.ones_like(p)*.5
    fused,colors=gaps.fuse_multiview(p,c,np.array([0,1,2,0,0,0]),.1,3)
    np.testing.assert_allclose(fused,[[.02,0,0]])
    np.testing.assert_allclose(colors,[[.5,.5,.5]])

def test_fusion_keeps_baseline_occupied_voxel_out_of_additions():
    base=np.array([[.01,0,0],[-.01,0,0]])
    p=np.array([[.08,0,0],[-.08,0,0],[.12,0,0]])
    assert gaps.missing_voxels(p,base,.1).tolist()==[False,False,True]

def test_hole_mask_excludes_existing_depth_and_outside_polygon():
    depth=np.full((7,7),np.inf);depth[3,3]=2.
    mask=gaps.hole_mask(depth,[(1,1),(5,1),(5,5),(1,5)])
    assert mask[2,2] and not mask[3,3] and not mask[0,0]
    assert mask.sum()==24

def test_screen_selection_rejects_old_surface_outside_and_behind_camera():
    mask=np.zeros((7,7),bool);mask[3,3]=True
    k=np.array([[1.,0,3],[0,1.,3],[0,0,1]])
    p=np.array([[0,0,2],[2,0,2],[0,0,-2],[20,0,2]])
    assert gaps.screen_hits(p,k,np.eye(3),np.zeros(3),mask).tolist()==[True,False,False,False]

def test_pixel_rays_land_on_measured_plane_not_learned_depth():
    p,valid=intersect_plane(np.array([[0,0],[1,0]],float),np.eye(3),np.eye(3),np.zeros(3),np.array([0,0,1,-2.]))
    assert valid.all()
    np.testing.assert_allclose(p,[[0,0,2],[2,0,2]])

def test_parallel_and_behind_camera_intersections_are_rejected():
    _,valid=intersect_plane(np.array([[0,0],[1,0]],float),np.eye(3),np.eye(3),np.zeros(3),np.array([1,0,0,1.]))
    assert not valid.any()

def test_camera_pose_uses_static_anchors_despite_bad_correspondences():
    rng=np.random.default_rng(46);p=rng.uniform(-1,1,(80,3));p[:,2]+=4
    k=np.array([[300.,0,112],[0,300.,112],[0,0,1]])
    uv=cv2.projectPoints(p,np.array([.03,-.08,.01]),np.array([.2,.1,.3]),k,None)[0].reshape(-1,2)
    uv[:15]=rng.uniform(0,224,(15,2))
    result=estimate_pose(p,uv,k)
    assert result is not None
    predicted=cv2.projectPoints(p[15:],cv2.Rodrigues(result['rotation'])[0],-result['rotation']@result['center'],k,None)[0].reshape(-1,2)
    assert np.max(np.linalg.norm(predicted-uv[15:],axis=1))<.001
