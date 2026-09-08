import unittest
import numpy as np
from window_observed_surface import sample_cells


class ObservedSurfaceTests(unittest.TestCase):
    def grid(self):
        y,x=np.mgrid[:3,:3]
        return np.stack([x*.01,y*.01,x*0],axis=-1).astype(float)

    def test_subdivision_stays_inside_observed_cells(self):
        p=self.grid();c=np.ones_like(p)*.3
        q,r=sample_cells(p,c,np.ones((3,3),bool),subdivisions=3)
        self.assertEqual(len(q),36)
        self.assertTrue(np.all(q>=p.min((0,1))))
        self.assertTrue(np.all(q<=p.max((0,1))))
        np.testing.assert_allclose(r,.3)

    def test_no_bridging_glass_mask_or_missing_observations(self):
        p=self.grid();mask=np.ones((3,3),bool);mask[1,1]=False
        q,r=sample_cells(p,p,mask)
        self.assertEqual(len(q),0)

    def test_no_surface_across_depth_discontinuity(self):
        p=self.grid();p[:,1:,2]=1
        q,r=sample_cells(p,p,np.ones((3,3),bool))
        self.assertTrue(np.all(q[:,2]==1))

    def test_no_nonfinite_vertices(self):
        p=self.grid();p[1,1]=np.nan
        q,r=sample_cells(p,p,np.ones((3,3),bool))
        self.assertEqual(len(q),0)

if __name__=='__main__':unittest.main()
