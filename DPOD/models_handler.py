import os
from glob import glob
import json
import numpy as np
import cv2
from DPOD.apolloscape_specs import car_id2name, car_name2id
from math import sin, cos
import functools
from copy import copy
from scipy.interpolate import griddata
from scipy.stats import mode
from torch.nn.functional import softmax
import torch

"""
    This file contains functions for 3D-2D geometry as well as 
    class for operations involving 3D models such as:
    - generating masks on given images
    - performing PNP+RANSAC
"""


def transform_points(points, rotation_matrix, translation_vector):
    return points@rotation_matrix + translation_vector


def project_points(points, camera_matrix):
    return cv2.projectPoints(points, np.zeros(3), np.zeros(3), camera_matrix, None)[0][:, 0, :]


def draw_triangles(image, vertices, triangles, color=(0, 0, 255), colors=None):
    if colors is not None:
        for n, t in enumerate(triangles):
            coord = np.array([vertices[t[0]][:2], vertices[t[1]][:2], vertices[t[2]][:2]], dtype=np.int32)
            cv2.fillConvexPoly(image, coord, colors[n].tolist())
    else:
        for t in triangles:
            coord = np.array([vertices[t[0]][:2], vertices[t[1]][:2], vertices[t[2]][:2]], dtype=np.int32)
            cv2.fillConvexPoly(image, coord, color)


def euler_to_Rot(yaw, pitch, roll):
    Y = np.array([
        [cos(yaw), 0, sin(yaw)],
        [0, 1, 0],
        [-sin(yaw), 0, cos(yaw)]
    ])
    P = np.array([
        [1, 0, 0],
        [0, cos(pitch), -sin(pitch)],
        [0, sin(pitch), cos(pitch)]
    ])
    R = np.array([
        [cos(roll), -sin(roll), 0],
        [sin(roll), cos(roll), 0],
        [0, 0, 1]
    ])
    return np.dot(Y, np.dot(P, R))


class ModelsHandler:
    def __init__(self, kaggle_dataset_dir_path):
        self.raw_models = {
            os.path.split(model_path)[-1][:-5]: json.load(open(model_path))
            for model_path in glob(f'{kaggle_dataset_dir_path}/car_models_json/*.json')
        }

        # TODO: handle the fact that APOLLOSCAPE dataset was taken using two cameras with different parameters
        self.camera_matrix = np.array([
            [2304.5479, 0, 1686.2379],
            [0, 2305.8757, 1354.9849],
            [0, 0, 1]
        ], dtype=np.float32)

    def model_id_to_vertices_and_triangles(self, model_id):
        """
            Should also work given model name as input
        """
        if model_id in self.raw_models:
            data = self.raw_models[model_id]
        elif model_id in car_id2name:
            data = self.raw_models[car_id2name[model_id]]
        else:
            raise KeyError

        vertices = np.array(data['vertices'])
        vertices[:, 1] = -vertices[:, 1]
        triangles = np.array(data['faces']) - 1
        return vertices, triangles

    @staticmethod
    def normalize_to_256(x):
        return 256 * (x - x.min()) / (x.max() - x.min()) % 256

    @functools.lru_cache()
    def get_model_faces_midpoints(self, model_id):
        vertices, triangles = self.model_id_to_vertices_and_triangles(model_id)
        faces_mid_points = \
            (vertices[triangles[:, 0]]+vertices[triangles[:, 1]]+vertices[triangles[:, 2]])/3

        return faces_mid_points

    @functools.lru_cache()
    def get_model_face_to_color_array(self, model_id):
        faces_mid_points = self.get_model_faces_midpoints(model_id)
        return self.color_points(faces_mid_points, model_id)

    def color_points(self, points, model_id):
        h_colors = points[:, 1]
        r_colors = np.arctan2(*points[:, [0, 2]].T)
        h_colors = self.normalize_to_256(h_colors)
        r_colors = self.normalize_to_256(r_colors)
        colors = np.array([(model_id, hc, rc) for hc, rc in zip(h_colors, r_colors)]).astype(int)
        return colors

    def get_color_to_3dpoints_arrays(self, model_id):
        vertices, _ = self.model_id_to_vertices_and_triangles(model_id)
        colors = self.color_points(vertices, model_id)
        points_for_griddata = colors[:, 1:]
        values_for_griddata = vertices
        grid1, grid2 = np.mgrid[0:256, 0:256]

        def interpolate(method):
            return griddata(
                points=points_for_griddata,
                values=values_for_griddata,
                xi=(grid1, grid2),
                method=method
            )

        interpolated = interpolate('linear')
        missing_mask = np.isnan(interpolated)
        interpolated[missing_mask] = interpolate('nearest')[missing_mask]
        return interpolated

    def draw_model(self, img, model_id, translation_vector, rotation_matrix):
        """
            draw model identified by model_id onto img using coloring
            (class_mask, height_mask, angle_mask)
        """

        points3d_on_model, triangles = self.model_id_to_vertices_and_triangles(model_id)
        points3d_in_reality = transform_points(points3d_on_model, rotation_matrix, translation_vector)
        points2d_on_image = project_points(points3d_in_reality, self.camera_matrix)

        faces_mid_points3d_in_reality = \
            (points3d_in_reality[triangles[:, 0]]+points3d_in_reality[triangles[:, 1]]+points3d_in_reality[triangles[:, 2]])/3

        # face_ordering = np.argsort(-faces_mid_points3d_in_reality[:, 1]) # draw faces on each model from bottom
        face_ordering = np.argsort(-faces_mid_points3d_in_reality[:, 2])   # draw faces on each model from front

        triangles = triangles[face_ordering]                                  # this changes order
        colors = self.get_model_face_to_color_array(model_id)[face_ordering]  # this changes order

        draw_triangles(img, points2d_on_image, triangles, colors=colors)

        return img

    def draw_kaggle_model(self, img, model_id, kaggle_yaw, kaggle_pitch, kaggle_roll, x, y, z):
        yaw, pitch, roll = -kaggle_pitch, -kaggle_yaw, -kaggle_roll
        rotation_matrix = euler_to_Rot(yaw, pitch, roll)
        self.draw_model(img, model_id, np.array((x, y, z)), rotation_matrix)

    def draw_kaggle_models(self, img, model_types, kaggle_yaws, kaggle_pitches, kaggle_rolls, xs, ys, zs):
        for foo in sorted(zip(model_types, kaggle_yaws, kaggle_pitches, kaggle_rolls, xs, ys, zs), key=lambda foo: float(foo[-1]), reverse=True):
            self.draw_kaggle_model(img, *foo)

    def draw_kaggle_models_from_kaggle_string(self, img, kaggle_string):
        items = kaggle_string.split(' ')
        items = [float(x) for x in items]
        model_types, yaws, pitches, rolls, xs, ys, zs = [items[i::7] for i in range(7)]
        self.draw_kaggle_models(img, model_types, yaws, pitches, rolls, xs, ys, zs)

    def make_mask_from_kaggle_string(self, kaggle_string, img=None):
        # img is only for copying resolution
        mask = np.zeros(img.shape[:2]+[3], dtype=np.int16) if img else np.zeros([2710, 3384, 3], dtype=np.int16)
        mask[:, :, 0] = -1
        self.draw_kaggle_models_from_kaggle_string(mask, kaggle_string)
        return mask

    def make_visualizations(self, img, mask):
        no_car_mask     = mask[:, :, 0] == 255
        car_mask        = np.logical_not(no_car_mask)
        model_type_mask = mask[:, :, 0].astype(np.uint8)
        height_mask     = mask[:, :, 1].astype(np.uint8)
        angle_mask      = mask[:, :, 2].astype(np.uint8)

        overlay = 0.3
        overlay_img = copy(img)
        overlay_img[car_mask] = (overlay*np.array([255, 0, 0], dtype=np.uint8) + (1-overlay)*overlay_img)[car_mask]

        model_type_img = cv2.applyColorMap(model_type_mask*3, cv2.COLORMAP_RAINBOW)
        model_type_img[no_car_mask] = np.zeros(3, dtype=np.uint8)

        height_img = cv2.applyColorMap(height_mask, cv2.COLORMAP_SPRING)
        height_img[no_car_mask] = np.zeros(3, dtype=np.uint8)

        angle_img = cv2.applyColorMap(angle_mask, cv2.COLORMAP_HSV)
        angle_img[no_car_mask] = np.zeros(3, dtype=np.uint8)

        return overlay_img, model_type_img, height_img, angle_img

    def pnp_ransac_single_instance(self, data, model_id):
        """
        Arguments:
            data: (n_points, 4) shaped np.array which columns correspond to
                    - x coordinate of pixel on an image
                    - y coordinate of pixel on an image
                    - observed "height colour" (2nd channel in our masks)
                    - observed "angle  colour" (3rd channel in our masks)
            model_id: str or int identifier of 3D Model that will be fitted

        Returns:
            - whether solution has been found or not
            - (number of inliers, 2) array with positions of inliers
            - translation vector
            - rotation matrix
        """
        color_to_3dpoints = self.get_color_to_3dpoints_arrays(model_id)
        object_points = color_to_3dpoints[data[:, 2].astype(int), data[:, 3].astype(int)]
        image_points  = data[:, :2]
        # to chyba zwraca odwróconą rotację
        converged, rodrigues_rotation_vector, translation_vector, inliers = \
            cv2.solvePnPRansac(
                object_points,
                image_points,
                self.camera_matrix,
                None
            )

        return converged, image_points[inliers.flatten()], translation_vector.T, cv2.Rodrigues(rodrigues_rotation_vector.T).T

    def pnp_ransac_single_instance2(self, color_u, color_v, mask, model_id):
        points2d = np.argwhere(mask)
        colors = np.hstack([
            color_u.flatten()[mask.flatten()],
            color_v.flatten()[mask.flatten()],
        ])
        data = np.hstack([points2d, colors])
        return self.pnp_ransac_single_instance(data, model_id)

    def pnp_ransac_multiple_instances(self, clasification, correspondence_u, correspondence_v):
        """
        Args:
            clasification:

        Algorithm is as follows:
            1.  Filter points containing objects by thresholding on probabilty of belonging to background
           (1a. Filter biggest connected component, maybe with some tolerance)
            2.  Select most probable class for selected points
            3.  Select other classes at least as probable as alpha*most_probable_class_probability,
                but no more than some fixed value
            4.  Apply PnP+RANSAC for selected points and classes 3D models
            5.  Chose class that maximizes number of inliers
            6.  From now on treat theses inliers as background
            7.  Iterate with some stop condition
        """

        output = []
        background_threshold = 0.5
        probabilities = softmax(clasification, dim=0)
        color_u = np.argmax(correspondence_u, axis=0)
        color_v = np.argmax(correspondence_v, axis=0)
        background_id = clasification.shape[0]-1
        while True:
            # for each pixels containing car assign most probable class
            most_probable_class_pixelwise = np.argmax(probabilities, axis=0)

            # select most frequent class apart from
            most_frequent_class = mode(most_probable_class_pixelwise[most_probable_class_pixelwise != background_id]).mode.item()

            model_id = most_frequent_class
            converged, inliers, translation_vector, rotation_matrix = \
                self.pnp_ransac_single_instance2(color_u, color_v, clasification == most_frequent_class, model_id)

            if not converged:
                break

            # from now on treat inliers as background in order not to use them again
            probabilities[ :, inliers[:, 0], inliers[:, 1]] = 0
            probabilities[-1, inliers[:, 0], inliers[:, 1]] = 1

            output.append((model_id, translation_vector, rotation_matrix))

        return output



