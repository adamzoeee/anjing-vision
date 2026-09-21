import sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import local_gap_geometry as geometry
import localize_r2_map as localization


def test_render_identity_keeps_visible_baseline_id_and_color_together():
    points=np.array([[0.,0,3],[0,0,2],[0,0,-1]])
    colors=np.array([[1.,0,0],[0,1,0],[0,0,1]])
    k=np.array([[2.,0,2],[0,2,2],[0,0,1]])
    rgb,depth,ids=geometry.render_identity(points,colors,k,np.eye(3),np.zeros(3),(5,5),0)
    assert ids[2,2]==1
    assert depth[2,2]==2
    np.testing.assert_array_equal(rgb[2,2],[0,255,0])
    assert ids[0,0]==-1 and np.isinf(depth[0,0])


def test_render_identity_splat_occlusion_never_returns_hidden_point():
    points=np.array([[0.,0,3],[0,0,2]])
    rgb,depth,ids=geometry.render_identity(points,np.ones((2,3)),np.eye(3),np.eye(3),np.zeros(3),(3,4),1)
    np.testing.assert_array_equal(ids[:2,:2],np.ones((2,2),int))
    assert ids[2,3]==-1


def test_render_identity_preserves_original_index_when_culling_invalid_points():
    points=np.array([[np.nan,0,1],[0,0,-2],[10,0,1],[0.,0,2]])
    rgb,depth,ids=geometry.render_identity(points,np.ones((4,3)),np.eye(3),np.eye(3),np.zeros(3),(2,2),0)
    assert ids[0,0]==3


def test_many_renderings_of_one_query_feature_are_only_one_correspondence():
    p=np.array([[0.,0,2],[.01,0,2],[1.,0,2]])
    rows=[dict(query_xy=[20.,30.],point_id=i,reference=str(j),region=3,distance=10.)
          for j,i in enumerate([0,1,0,2])]
    found=localization.select_map_matches(rows,p)
    assert len(found)==1 and found[0]['point_id'] in [0,1]


def test_inconsistent_feature_identity_is_not_resolved_by_nearest_point():
    p=np.array([[0.,0,2],[1.,0,2],[2.,0,2]])
    rows=[dict(query_xy=[20.,30.],point_id=i,reference=str(i),region=3,distance=10.) for i in range(3)]
    assert localization.select_map_matches(rows,p)==[]


def test_map_pixel_uses_observed_surface_not_splat_center():
    p=np.array([[(x-2)*.02,(y-2)*.02,2.] for y in range(5) for x in range(5)])
    k=np.array([[100.,0,2],[0,100.,2],[0,0,1]])
    result=geometry.map_surface_point([2.25,2.5],np.arange(25).reshape(5,5),p,k,np.eye(3),np.zeros(3))
    assert result is not None
    np.testing.assert_allclose(result['xyz'],[.005,.01,2.],atol=1e-8)
    assert result['plane_p90_m']<1e-8


def test_map_surface_does_not_invent_xyz_in_unobserved_pixel():
    result=geometry.map_surface_point([2,2],np.full((5,5),-1),np.zeros((1,3)),np.eye(3),np.eye(3),np.zeros(3))
    assert result is None


def test_collinear_map_support_cannot_define_a_surface():
    p=np.array([[x*.001,0.,2.] for x in range(25)])
    result=geometry.map_surface_point([2,2],np.arange(25).reshape(5,5),p,np.eye(3),np.eye(3),np.zeros(3))
    assert result is None
