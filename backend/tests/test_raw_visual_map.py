import sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from raw_visual_map import consistent_identity, sample_identity

def test_identity_needs_distinct_frames_and_no_wide_spread():
    rows=[dict(old_frame=i,xyz=[i*.005,0,0],distance=i) for i in range(3)]
    assert consistent_identity(rows) is not None
    assert consistent_identity([rows[0],rows[0]]) is None
    rows[1]['xyz']=[.2,0,0];rows[2]['xyz']=[.4,0,0]
    assert consistent_identity(rows) is None

def test_depth_edge_and_low_confidence_are_rejected():
    y,x=np.mgrid[:8,:8];p=np.stack([x*.001,y*.001,np.ones_like(x)],-1)
    c=np.full((8,8),20.)
    assert sample_identity(p,c,(3.2,3.2)) is not None
    p[4,4,2]=2
    assert sample_identity(p,c,(3.2,3.2)) is None
    assert sample_identity(p,c*0,(1.2,1.2)) is None

def test_pixel_query_interpolates_only_observed_neighbours():
    y,x=np.mgrid[:8,:8];p=np.stack([x*.001,y*.001,np.ones_like(x)],-1)
    np.testing.assert_allclose(sample_identity(p,np.full((8,8),20.),(3.2,3.4)),[.0032,.0034,1])
    assert sample_identity(p,np.full((8,8),20.),(-1,2)) is None
