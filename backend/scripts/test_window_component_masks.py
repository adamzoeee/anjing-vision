import unittest
from integrate_window_local_sequence import component_mask

class WindowMaskTests(unittest.TestCase):
    def test_slanted_right_frame_is_retained_without_central_glass(self):
        mask=component_mask('right_frame_and_curtain',[(36,224,148,224)])
        self.assertTrue(mask[160,144], 'Observed slanted inner rim must remain')
        self.assertTrue(mask[200,141])
        self.assertFalse(mask[100,140], 'Exterior seen through glass is not a rim')
        self.assertFalse(mask[160,100])

if __name__=='__main__':unittest.main()
