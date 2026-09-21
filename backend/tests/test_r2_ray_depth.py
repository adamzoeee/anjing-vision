"""Catch wrong ray direction, invalid views, and unobservable depth updates."""
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import local_gap_geometry as geometry


def test_ray_depth_uses_multiview_disparity_not_initial_depth():
    k=np.array([[100.,0,50],[0,100,50],[0,0,1]])
    cameras=[(k,np.eye(3),np.array([x,0.,0.])) for x in [0.,.2,.4]]
    # True point [0,0,2]: manually projected pixels with 10/20 px disparities.
    tracks=np.array([[[50.,50.]],[[40.,50.]],[[30.,50.]]])
    result=geometry.refine_ray_depth(np.array([[50.,50.]]),np.array([2.15]),
        tracks,np.ones((3,1),bool),cameras,0,max_change=.3)
    assert result['accepted'][0]
    np.testing.assert_allclose(result['points'][0],[0,0,2],atol=1e-6)


def test_pure_rotation_or_zero_camera_baseline_cannot_certify_depth():
    k=np.eye(3);cameras=[(k,np.eye(3),np.zeros(3))]*3
    result=geometry.refine_ray_depth(np.array([[0.,0.]]),np.array([2.]),
        np.zeros((3,1,2)),np.ones((3,1),bool),cameras,0,max_change=.3)
    assert not result['accepted'][0]


def test_invalid_view_does_not_pull_depth_or_count_as_support():
    k=np.array([[100.,0,50],[0,100,50],[0,0,1]])
    cameras=[(k,np.eye(3),np.array([x,0.,0.])) for x in [0.,.2,.4,.6]]
    tracks=np.array([[[50.,50.]],[[40.,50.]],[[30.,50.]],[[500.,500.]]])
    valid=np.array([[True],[True],[True],[False]])
    result=geometry.refine_ray_depth(np.array([[50.,50.]]),np.array([2.1]),tracks,valid,cameras,0,max_change=.3)
    assert result['accepted'][0]
    np.testing.assert_allclose(result['points'][0],[0,0,2],atol=1e-6)


def test_depth_change_bound_rejects_unrelated_correspondence():
    k=np.array([[100.,0,50],[0,100,50],[0,0,1]])
    cameras=[(k,np.eye(3),np.array([x,0.,0.])) for x in [0.,.2,.4]]
    tracks=np.array([[[50.,50.]],[[45.,50.]],[[40.,50.]]])
    result=geometry.refine_ray_depth(np.array([[50.,50.]]),np.array([2.]),tracks,
        np.ones((3,1),bool),cameras,0,max_change=.3)
    assert not result['accepted'][0]


def test_reference_ray_uses_world_camera_center_and_rotation():
    k=np.array([[100.,0,50],[0,100,50],[0,0,1]])
    # Camera looks toward world +X; true point is [3,2,3].
    r=np.array([[0.,0,-1],[0,1,0],[1,0,0]])
    cameras=[(k,r,np.array([1.,2.,z])) for z in [3.,2.8,2.6]]
    tracks=np.array([[[50.,50.]],[[40.,50.]],[[30.,50.]]])
    result=geometry.refine_ray_depth(np.array([[50.,50.]]),np.array([2.1]),tracks,
        np.ones((3,1),bool),cameras,0,max_change=.3)
    assert result['accepted'][0]
    np.testing.assert_allclose(result['points'][0],[3,2,3],atol=1e-6)


def test_intrinsics_follow_crop_origin_instead_of_recentering_principal_point():
    k=np.array([[100.,0,50],[0,100,50],[0,0,1]])
    result=geometry.transfer_crop_intrinsics(k,[0,100,200,200],[0,0,200,200],200)
    np.testing.assert_allclose(result,[[100,0,50],[0,100,150],[0,0,1]])


def test_crop_resize_scales_focal_length_and_principal_point_together():
    k=np.array([[100.,0,50],[0,100,50],[0,0,1]])
    result=geometry.transfer_crop_intrinsics(k,[0,0,200,200],[0,0,400,400],200)
    np.testing.assert_allclose(result,[[50,0,25],[0,50,25],[0,0,1]])


def test_reference_zero_error_cannot_hide_bad_neighbor_correspondence():
    k=np.array([[100.,0,50],[0,100,50],[0,0,1]])
    cameras=[(k,np.eye(3),np.array([x,0.,0.])) for x in [0.,.02,.04]]
    # One neighbor is displaced vertically. The reference always has zero
    # reprojection error and must not let a median certify this inconsistent track.
    tracks=np.array([[[50.,50.]],[[49.,50.]],[[48.,52.]]])
    result=geometry.refine_ray_depth(np.array([[50.,50.]]),np.array([2.]),tracks,
        np.ones((3,1),bool),cameras,0,max_change=2.)
    assert not result['accepted'][0]
