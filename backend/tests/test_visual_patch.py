import sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from visual_window_patch import safe_faces
from visual_window_patch import r2_labels, grid_faces
from visual_window_patch import supported_mask, overlay
from PIL import Image

def test_unverified_observations_cannot_enter_independent_mesh():
    mask=np.ones((2,2),bool)
    result=supported_mask(mask,np.arange(4),np.array([0,2,3,5]))
    assert result.tolist()==[[False,False],[True,True]]
    assert mask.all()  # caller's semantic mask remains unchanged

def test_real_depth_occlusion_hides_background_surface():
    p=np.array([[-.1,-.1,2],[.1,-.1,2],[0,.1,2]])
    uv=np.array([[0,0],[223,0],[100,223]])
    faces=np.array([[0,1,2]])
    texture=np.full((224,224,3),[255,0,0],dtype=np.uint8)
    before=Image.new('RGB',(896,896),(15,19,26))
    view=(np.array([[112,0,111.5],[0,112,111.5],[0,0,1]]),np.eye(3),np.zeros(3))
    hidden=overlay(before,np.ones((896,896)),p,uv,faces,texture,view)
    visible=overlay(before,np.full((896,896),3.),p,uv,faces,texture,view)
    assert np.array_equal(np.array(before),np.array(hidden))
    assert not np.array_equal(np.array(before),np.array(visible))

def test_r2_excludes_curtain_shelf_and_bed():
    labels=r2_labels()
    assert labels[100,170]==0  # curtain
    assert labels[180,215]==0  # shelf
    assert labels[190,100]==0  # bed
    assert labels[160,155]==1  # cushion
    assert labels[200,185]==2  # bag

def test_grid_never_joins_different_objects_or_missing_pixels():
    labels=np.array([[1,1,2],[1,0,2],[1,2,2]])
    index=np.arange(9).reshape(3,3)
    faces=grid_faces(index,labels)
    assert all(len(set(labels.ravel()[f]))==1 and labels.ravel()[f[0]]>0 for f in faces)

def test_faces_do_not_bridge_large_or_masked_gaps():
    p=np.array([[0,0,0],[.01,0,0],[0,.01,0],[1,0,0]])
    uv=np.array([[2,2],[3,2],[2,3],[8,2]],float)
    mask=np.ones((10,10),bool)
    f=safe_faces(p,uv,np.array([[0,1,2],[0,1,3]]),mask)
    assert f.tolist()==[[0,1,2]]
    mask[2,2]=False
    assert len(safe_faces(p,uv,np.array([[0,1,2]]),mask))==0
