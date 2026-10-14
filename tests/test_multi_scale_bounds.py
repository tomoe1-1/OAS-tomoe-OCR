"""Actual local/service template searches must finish on invalid scale grids."""
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from module.atom.image import RuleImage
from module.image.runtime import ImageRuntime
from module.image.scales import (
    MAX_MULTI_SCALE_SAMPLES, multi_scale_values, normalize_multi_scale_range,
)


class MultiScaleBoundsTests(unittest.TestCase):
    def setUp(self):
        random = np.random.default_rng(123)
        self.template = random.integers(0, 256, size=(10, 10, 3), dtype=np.uint8)
        self.image = random.integers(0, 256, size=(70, 70, 3), dtype=np.uint8)
        self.image[27:37, 30:40] = self.template
        self.roi = [5, 7, 50, 50]
        self.rule = RuleImage(roi_front=(0, 0, 10, 10), roi_back=self.roi,
                              method=RuleImage.METHOD_MULTI_SCALE_TEMPLATE_MATCH,
                              threshold=.99, file='offline-template.png')
        self.rule._image = self.template
        self.runtime = ImageRuntime.__new__(ImageRuntime)

    def local(self, scale_range=None, scale_step=.1):
        self.rule.scale_range = scale_range
        self.rule.scale_step = scale_step
        return self.rule.multi_scale_template_match(self.image)

    def service(self, scale_range=None, scale_step=.1):
        return self.runtime._multi_scale_template_match(self.image, self.template, self.roi,
            .99, scale_range, scale_step, 'offline-template')

    def test_default_local_and_service_match_same_center_with_seven_scales(self):
        for endpoint in (self.local, self.service):
            with self.subTest(endpoint=endpoint.__name__), patch('cv2.resize', wraps=cv2.resize) as resize:
                result = endpoint()
                self.assertEqual(resize.call_count, 7)
                if endpoint == self.local:
                    self.assertTrue(result)
                    self.assertEqual(self.rule.roi_front, [30, 27, 10, 10])
                else:
                    matched, score, roi = result
                    self.assertTrue(matched)
                    self.assertGreater(score, .99)
                    self.assertEqual(roi, [30, 27, 10, 10])

    def test_positive_step_that_cannot_advance_float_is_rejected_before_matching(self):
        self.assertEqual(1. + 1e-20, 1.)  # The previous while-loop never advanced.
        for endpoint in (self.local, self.service):
            with self.subTest(endpoint=endpoint.__name__), patch('cv2.resize') as resize:
                with self.assertRaisesRegex(ValueError, '2048 samples'):
                    endpoint((1., 1.1), 1e-20)
                resize.assert_not_called()

    def test_oversized_grid_and_overflowing_grid_fail_before_opencv_work(self):
        for scale_range, step in (((.5, 1.5), 1e-4), ((1e-300, 1e300), 1e-300)):
            for endpoint in (self.local, self.service):
                with self.subTest(endpoint=endpoint.__name__, bounds=scale_range), patch('cv2.resize') as resize:
                    with self.assertRaisesRegex(ValueError, '2048 samples'):
                        endpoint(scale_range, step)
                    resize.assert_not_called()

    def test_nonfinite_bounds_and_steps_fail_without_matching(self):
        for scale_range, step in (((1., float('inf')), .1), ((float('nan'), 1.), .1),
                                  ((1., 2.), float('nan')), ((1., 2.), float('inf')),
                                  ((1., 2.), -float('inf'))):
            for endpoint in (self.local, self.service):
                with self.subTest(endpoint=endpoint.__name__, bounds=scale_range, step=step), \
                        patch('cv2.resize') as resize:
                    with self.assertRaisesRegex(ValueError, 'finite'):
                        endpoint(scale_range, step)
                    resize.assert_not_called()

    def test_nonpositive_step_keeps_existing_default_fallback(self):
        for step in (0, -.25):
            for scale_range in ((1.2, .6), (1.2, .6, step)):
                with self.subTest(bounds=scale_range, step=step):
                    self.rule.scale_range = scale_range
                    self.rule.scale_step = step
                    self.assertEqual(self.rule._get_multi_scale_range(), (.6, 1.2, .1))
                    self.assertEqual(self.runtime._get_multi_scale_range(scale_range, step), (.6, 1.2, .1))
                    self.assertTrue(self.local(scale_range, step))
                    self.assertTrue(self.service(scale_range, step)[0])

    def test_integer_enumeration_accepts_limit_and_rejects_one_more_sample(self):
        bounds = normalize_multi_scale_range((1., 2.), 1. / (MAX_MULTI_SCALE_SAMPLES - 1))
        values = multi_scale_values(*bounds)
        self.assertEqual(len(values), MAX_MULTI_SCALE_SAMPLES)
        self.assertEqual(values[0], 1.)
        self.assertEqual(values[-1], 2.)
        with self.assertRaisesRegex(ValueError, '2048 samples'):
            normalize_multi_scale_range((1., 2.), 1. / MAX_MULTI_SCALE_SAMPLES)

    def test_single_scale_remains_finite_with_a_tiny_unused_step(self):
        self.assertEqual(multi_scale_values(*normalize_multi_scale_range((1., 1.), 1e-300)), (1.,))
        self.assertTrue(self.local((1., 1.), 1e-300))
        self.assertTrue(self.service((1., 1.), 1e-300)[0])

    def test_oversized_templates_are_skipped_without_changing_scale_budget(self):
        # A valid but oversized template used to hit a separate increment/continue path.
        self.template = np.zeros((100, 100, 3), dtype=np.uint8)
        self.rule._image = self.template
        with patch('cv2.resize') as resize:
            self.assertFalse(self.local((1., 2.), .1))
            self.assertFalse(self.service((1., 2.), .1)[0])
            resize.assert_not_called()


if __name__ == '__main__':
    unittest.main()
