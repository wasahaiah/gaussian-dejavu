# 
# Toyota Motor Europe NV/SA and its affiliated companies retain all intellectual 
# property and proprietary rights in and to this software, related documentation 
# and any modifications thereto. Any use, reproduction, disclosure or distribution 
# of this software and related documentation without an express license agreement 
# from Toyota Motor Europe NV/SA is strictly prohibited.
#

from typing import Tuple, Literal
import math
import numpy as np
from scipy.spatial.transform import Rotation as R
import json
from pathlib import Path
import os


def getOrthProjectionMatrix_numpy(znear, zfar, scale=1.0):
    # Author: Peizhi Yan
    # Source: https://www.songho.ca/opengl/gl_projectionmatrix.html
    # Desctiption: to compute the orthogonal projection matrix  [numpy version]

    width = 1.0 / scale
    height = 1.0 / scale
    top = height / 2
    bottom = -top
    right = width / 2
    left = -right

    P = np.zeros((4, 4), dtype=np.float32)

    z_sign = 1.0 # unused for now. 

    # Set the diagonal elements for scaling
    P[0, 0] = 2.0 / (right - left)
    P[1, 1] = 2.0 / (top - bottom)
    P[2, 2] = -2.0 / (zfar - znear)  # Note the inversion of the depth range
    
    # Set the translation elements
    P[0, 3] = -(right + left) / (right - left)
    P[1, 3] = -(top + bottom) / (top - bottom)
    P[2, 3] = -(zfar + znear) / (zfar - znear)

    # This element remains unchanged
    P[3, 3] = 1.0
    
    return P


def projection_from_intrinsics(K: np.ndarray, image_size: Tuple[int], 
                               near: float=0.01, far:float=10, flip_y: bool=False, z_sign=-1):
    """
    Transform points from camera space (x: right, y: up, z: out) to clip space (x: right, y: up, z: in)
    Args:
        K: Intrinsic matrix, (N, 3, 3)
            K = [[
                        [fx, 0, cx],
                        [0, fy, cy],
                        [0,  0,  1],
                ]
            ]
        image_size: (height, width)
    Output:
        proj = [[
                [2*fx/w, 0.0,     (w - 2*cx)/w,             0.0                     ],
                [0.0,    2*fy/h, (h - 2*cy)/h,             0.0                     ],
                [0.0,    0.0,     z_sign*(far+near) / (far-near), -2*far*near / (far-near)],
                [0.0,    0.0,     z_sign,                     0.0                     ]
            ]
        ]
    """

    B = K.shape[0]
    h, w = image_size

    if K.shape[-2:] == (3, 3):
        fx = K[..., 0, 0]
        fy = K[..., 1, 1]
        cx = K[..., 0, 2]
        cy = K[..., 1, 2]
    elif K.shape[-1] == 4:
        # fx, fy, cx, cy = K[..., [0, 1, 2, 3]].split(1, dim=-1)
        fx = K[..., [0]]
        fy = K[..., [1]]
        cx = K[..., [2]]
        cy = K[..., [3]]
    else:
        raise ValueError(f"Expected K to be (N, 3, 3) or (N, 4) but got: {K.shape}")

    proj = np.zeros([B, 4, 4])
    proj[:, 0, 0]  = fx * 2 / w 
    proj[:, 1, 1]  = fy * 2 / h
    proj[:, 0, 2]  = (w - 2 * cx) / w
    proj[:, 1, 2]  = (h - 2 * cy) / h
    proj[:, 2, 2]  = z_sign * (far+near) / (far-near)
    proj[:, 2, 3]  = -2*far*near / (far-near)
    proj[:, 3, 2]  = z_sign

    if flip_y:
        proj[:, 1, 1] *= -1
    return proj



class OrbitCamera:
    def __init__(self, W, H, r=2, fovy=60, znear=0.01, zfar=10, scale=1.0,
                 convention: Literal["opengl", "opencv"]="opengl",
                 projection: Literal["perspective", "orthogonal"]="perspective", 
                 save_path='camera.json'):
        self.image_width = W
        self.image_height = H
        self.radius_default = r  # the orbit camera radius to the look at center (0,0,0)
        self.fovy_default = fovy # only for perspective projection
        self.znear = znear  # defines the nearest bound of a view volume
        self.zfar = zfar    # defines the farest bound of a view volume
        self.scale = scale  # only for orthogonal projection, by default is 1.0
        self.convention = convention
        self.projection = projection
        self.save_path = save_path

        self.up = np.array([0, 1, 0], dtype=np.float32)
        self.reset()
        self.load()
    
    def reset(self):
        """ The internal state of the camera is based on the OpenGL convention, but 
            properties are converted to the target convention when queried.
        """
        self.rot = R.from_matrix([[1, 0, 0], [0, 1, 0], [0, 0, 1]])  # OpenGL convention
        self.look_at = np.array([0, 0, 0], dtype=np.float32)  # look at this point
        self.radius = self.radius_default  # camera distance from center
        self.fovy = self.fovy_default
        if self.convention == "opencv":
            self.z_sign = 1
            self.y_sign = 1
        elif self.convention == "opengl":
            self.z_sign = -1
            self.y_sign = -1
        else:
            raise ValueError(f"Unknown convention: {self.convention}")
    
    def save(self):
        save_dict = {
            'rotation': self.rot.as_matrix().tolist(),
            'look_at': self.look_at.tolist(),
            'radius': self.radius,
            'fovy': self.fovy,
        }
        with open(self.save_path, "w") as f:
            json.dump(save_dict, f, indent=4)
    
    def clear(self):
        os.remove(self.save_path)
    
    def load(self):
        if not Path(self.save_path).exists():
            return
        with open(self.save_path, "r") as f:
            load_dict = json.load(f)
        self.rot = R.from_matrix(np.array(load_dict['rotation']))
        self.look_at = np.array(load_dict['look_at'])
        self.radius = load_dict['radius']
        self.fovy = load_dict['fovy']

    @property
    def fovx(self):
        return self.fovy / self.image_height * self.image_width
    
    @property
    def intrinsics(self):
        focal = self.image_height / (2 * np.tan(np.radians(self.fovy) / 2))
        return np.array([focal, focal, self.image_width // 2, self.image_height // 2])
    
    @property
    def projection_matrix(self):
        # this is the default perspective projection
        if self.projection == "perspective":
            return projection_from_intrinsics(self.intrinsics[None], (self.image_height, self.image_width), self.znear, self.zfar, z_sign=self.z_sign)[0]

        # this is the orthogonal projection
        if self.projection == "orthogonal":
            return getOrthProjectionMatrix_numpy(znear=self.znear, zfar=self.zfar, scale=self.scale)

    @property
    def world_view_transform(self):
        return np.linalg.inv(self.pose)  # world2cam

    @property
    def full_proj_transform(self):
        return self.projection_matrix @ self.world_view_transform

    @property
    def pose(self):
        # first move camera to radius
        pose = np.eye(4, dtype=np.float32)
        pose[2, 3] += self.radius

        # rotate
        rot = np.eye(4, dtype=np.float32)
        rot[:3, :3] = self.rot.as_matrix()
        pose = rot @ pose

        # translate
        pose[:3, 3] -= self.look_at

        if self.convention == "opencv":
            pose[:, [1, 2]] *= -1
        elif self.convention == "opengl":
            pass
        else:
            raise ValueError(f"Unknown convention: {self.convention}")
        return pose
    
    def orbit(self, dx, dy):
        # rotate along camera up/side axis!
        side = self.rot.as_matrix()[:3, 0]
        rotvec_x = self.up * np.radians(-0.3 * dx)
        rotvec_y = side * np.radians(-0.3 * dy)
        self.rot = R.from_rotvec(rotvec_x) * R.from_rotvec(rotvec_y) * self.rot

    def scale(self, delta):
        self.radius *= 1.1 ** (-delta)

    def pan(self, dx, dy, dz=0):
        # pan in camera coordinate system (careful on the sensitivity!)
        d = np.array([dx, -dy, dz])  # the y axis is flipped
        self.look_at += 2 * self.rot.as_matrix()[:3, :3] @ d * self.radius / self.image_height * math.tan(np.radians(self.fovy) / 2)