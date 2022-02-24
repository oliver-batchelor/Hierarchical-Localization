import kornia
from kornia.feature.laf import (
        extract_patches_from_pyramid, laf_from_center_scale_ori)
import numpy as np
import torch
import pycolmap

from ..utils.base_model import BaseModel


EPS = 1e-6


def sift_to_rootsift(x):
    x = x / (np.linalg.norm(x, ord=1, axis=-1, keepdims=True) + EPS)
    x = np.sqrt(x.clip(min=EPS))
    x = x / (np.linalg.norm(x, axis=-1, keepdims=True) + EPS)
    return x


class DoG(BaseModel):
    default_conf = {
        'vlfeat': {
            'num_octaves': 4,
            'octave_resolution': 3,
            'first_octave': 0,
            'edge_thresh': 10,
            'peak_thresh': 0.01,
            'upright': False,
        },
        'descriptor': 'rootsift',
        'max_keypoints': -1,
        'patch_size': 32,
        'mr_size': 12,
    }
    required_inputs = ['image']
    detection_noise = 1.0

    def _init(self, conf):
        if conf['descriptor'] == 'sosnet':
            self.describe = kornia.feature.SOSNet(pretrained=True)
        elif conf['descriptor'] not in ['sift', 'rootsift']:
            raise ValueError(f'Unknown descriptor: {conf["descriptor"]}')

    def _forward(self, data):
        image = data['image']
        image_np = image.cpu().numpy()[0, 0]
        assert image.shape[1] == 1
        assert image_np.min() >= -EPS and image_np.max() <= 1 + EPS

        keypoints, scores, descriptors = pycolmap.extract_sift(
            image_np, **self.conf['vlfeat']
        )

        if self.conf['descriptor'] in ['sift', 'rootsift']:
            if self.conf['descriptor'] == 'rootsift':
                descriptors = sift_to_rootsift(descriptors)
            descriptors = torch.from_numpy(descriptors)
        elif self.conf['descriptor'] == 'sosnet':
            # VLFeat -> xyA conversion.
            # Based on https://github.com/colmap/colmap/blob/dev/src/feature/types.cc#L43-L53.
            center = keypoints[:, :2] + 0.5
            scale = keypoints[:, 2] * self.conf['mr_size'] / 2
            ori = -np.rad2deg(keypoints[:, 3])
            lafs = laf_from_center_scale_ori(
                torch.from_numpy(center)[None],
                torch.from_numpy(scale)[None, :, None, None],
                torch.from_numpy(ori)[None, :, None]).to(image.device)
            patches = extract_patches_from_pyramid(
                    image, lafs, PS=self.conf['patch_size'])[0]
            descriptors = self.describe(patches).reshape(len(patches), 128)
        else:
            raise ValueError(f'Unknown descriptor: {self.conf["descriptor"]}')

        keypoints = torch.from_numpy(keypoints[:, :2])  # keep only x, y
        scores = torch.from_numpy(scores)

        if self.conf['max_keypoints'] != -1:
            # TODO: check that the scores from PyCOLMAP are 100% correct,
            # follow https://github.com/mihaidusmanu/pycolmap/issues/8
            indices = torch.topk(scores, self.conf['max_keypoints'])
            keypoints = keypoints[indices]
            scores = scores[indices]
            descriptors = descriptors[indices]

        return {
            'keypoints': keypoints[None],
            'scores': scores[None],
            'descriptors': descriptors.T[None],
        }
