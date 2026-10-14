import unittest
from unittest.mock import Mock
from types import SimpleNamespace
import cv2,numpy as np
from tasks.GameUi.recovery_exit import ASSETS,exit_point,click_exit

class RecoveryExitTests(unittest.TestCase):
    def canvas(self,kind,x=23,y=15,scale=1.):
        template=cv2.imdecode(np.fromfile(ASSETS/('recovery_'+kind+'.png'),dtype=np.uint8),cv2.IMREAD_COLOR)
        template=cv2.cvtColor(template,cv2.COLOR_BGR2RGB)
        template=cv2.resize(template,None,fx=scale,fy=scale)
        image=np.full((720,1280,3),65,np.uint8);h,w=template.shape[:2];image[y:y+h,x:x+w]=template
        return image,(x+w//2,y+h//2,kind)
    def test_actual_matching_center_for_both_exits_and_shifted_position(self):
        for kind in ('back_arrow','close_cross'):
            for scale in (.75,1.,1.25):
                image,point=self.canvas(kind,841,17,scale)
                self.assertEqual(exit_point(image),point)
    def test_no_fixed_coordinate_when_no_exit(self):
        self.assertIsNone(exit_point(np.full((720,1280,3),65,np.uint8)))
    def test_single_input_two_fresh_frames_unchanged_exit_is_not_success(self):
        image,point=self.canvas('back_arrow')
        device=SimpleNamespace(image=image,click=Mock())
        task=SimpleNamespace(device=device,screenshot=Mock())
        self.assertFalse(click_exit(task));device.click.assert_called_once_with(point[0],point[1],control_name='RECOVERY_EXIT_BACK_ARROW')
        self.assertEqual(task.screenshot.call_count,2)
    def test_changed_frames_are_observed_after_input(self):
        image,point=self.canvas('close_cross')
        device=SimpleNamespace(image=image,click=Mock())
        task=SimpleNamespace(device=device,screenshot=Mock(side_effect=lambda:setattr(device,'image',np.full(image.shape,200,np.uint8))))
        self.assertTrue(click_exit(task));device.click.assert_called_once();self.assertEqual(task.screenshot.call_count,2)
if __name__=='__main__':unittest.main()
