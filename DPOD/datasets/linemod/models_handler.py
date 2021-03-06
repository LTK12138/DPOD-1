import numpy as np
import cv2
from glob import glob
import os
from functools import lru_cache
from tqdm import tqdm
from argparse import ArgumentParser
import matplotlib.pyplot as plt
from scipy.interpolate import griddata


def read_obj_file(path):
    """
    reads <something>_small.obj file

    vertices: (n_vertices, 3) float array with 3d coordinates of vertices
    faces:    (n_faces, 3)    int   array with indices of model faces
    """
    with open(path) as file:
        vertices = []
        faces = []
        for line in file:
            if line[0] == 'v':
                x, y, z = map(float, line.split(' ')[1:4])
                vertices.append((x, y, z))
            if line[0] == 'f':
                v1, v2, v3 = map(int, line.split(' ')[1:])
                faces.append((v1-1, v2-1, v3-1))

        vertices = np.array(vertices)
        faces = np.array(faces)

        return vertices, faces


def read_position_file(path):
    """
    reads position (poses/<model_name>/info_<image_id>.txt) file from provided dataset

    image_size: (h, w) int tuple
    model_id: str - this is sometimes number and sometimes name
    rotation_matrix: (3, 3) float array
    center: (3,) float array - position of model center in meters
    extend: (3,) float array - I don't know what it is
    """
    try:
        with open(path) as file:
            lines = file.readlines()
            if len(lines) == 2:
                # no object
                return None
            image_size = tuple(map(int, lines[1].split(' ')))
            model_id = lines[2]

            rotation_matrix = np.array([[float(x) for x in line.split(' ')] for line in lines[4:7]])
            center = np.array(list(map(float, lines[8].split(' '))))
            extend = np.array(list(map(float, lines[10].split(' '))))

            return image_size, model_id, rotation_matrix, center, extend

    except Exception as e:
        print(e)
        print('crashed on', path)
        print(open(path).readlines())
        raise e


def draw_poly(image, vertices, color):
    """
    image: (h, w, c) int array
    vertices: (n_vertices, 2) vertices coordinates
    color: (c,) int array
    """
    cv2.fillConvexPoly(image, vertices.astype(int), color.tolist())
    return image


def transform_points(points, rotation_matrix, translation_vector):
    points = points@rotation_matrix.T + translation_vector
    points = points@np.diag([1, -1, -1])
    return points


def project_points(points, camera_matrix):
    return cv2.projectPoints(points, np.zeros(3), np.zeros(3), camera_matrix, None)[0][:, 0, :]


class ModelsHandler:
    def __init__(self, models_dir_path='models_small', color_resolution=255):
        self.camera_matrix = np.array([
            [572.41140, 0, 325.26110],
            [0, 573.57043, 242.04899],
            [0, 0, 1]
        ])
        self.color_resolution = color_resolution
        self._model_name_to_model_file_path = dict()
        for model_dir in glob(f'{models_dir_path}/*'):
            model_name = os.path.split(model_dir)[1]
            if 'Holepuncher' in model_dir:
                continue
            model_filepath = glob(f'{model_dir}/*.obj')[0]

            self._model_name_to_model_file_path[model_name] = model_filepath
        self.model_name_to_model_id = {name: n + 1 for n, name in enumerate(sorted(self._model_name_to_model_file_path.keys()))}
        self.model_id_to_model_name = {v: k for k, v in self.model_name_to_model_id.items()}

    @property
    def model_names(self):
        return list(self.model_name_to_model_id.keys())

    @lru_cache()
    def get_vertices(self, model_name):
        return read_obj_file(self._model_name_to_model_file_path[model_name])[0]

    @lru_cache()
    def get_faces(self, model_name):
        return read_obj_file(self._model_name_to_model_file_path[model_name])[1]

    @lru_cache()
    def get_faces_midpoints(self, model_name):
        vertices = self.get_vertices(model_name)
        faces = self.get_faces(model_name)
        faces_mid_points = (vertices[faces[:, 0]] + vertices[faces[:, 1]] + vertices[faces[:, 2]]) / 3
        return faces_mid_points

    def color_uv(self, points):
        # to [0, 1] range
        max_height = points[:, 1].max()
        min_height = points[:, 1].min()
        height_colors = (points[:, 1] - min_height) / (max_height - min_height)
        angle_colors  = np.arctan2(*points[:, [0, 2]].T)
        angle_colors  = (angle_colors + np.pi) / (2*np.pi)
        return np.stack([height_colors, angle_colors], axis=-1)

    
    @lru_cache()
    def get_faces_uv_colors(self, model_name):
        """
        return (n_faces, 2) float array with
            first  column being height scaled to [0, 1] and
            second column being angle  scaled to [0, 1]
        """
        vertices = self.get_vertices(model_name)
        faces = self.get_faces(model_name)
        faces_mid_points = self.get_faces_midpoints(model_name)  # self.color_uv can be used here
        max_height = vertices[:, 1].max()
        min_height = vertices[:, 1].min()
        height_colors = (faces_mid_points[:, 1] - min_height) / (max_height - min_height)
        angle_colors  = np.arctan2(*faces_mid_points[:, [0, 2]].T)
        angle_colors  = (angle_colors + np.pi) / (2*np.pi)
        return np.stack([height_colors, angle_colors], axis=-1)

    @lru_cache()
    def get_color_to_3dpoints_arrays(self, model_name):
        vertices = self.get_vertices(model_name)
        colors = (self.color_uv(vertices)*self.color_resolution).astype(int)
        points_for_griddata = colors
        values_for_griddata = vertices
        grid1, grid2 = np.mgrid[0:self.color_resolution, 0:self.color_resolution]

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
        #interpolated = interpolate('nearest')
        return interpolated

    def draw_color_mask(self, image, model_name, rotation_matrix, center):
        """
        image: (h, w, 2) int valued array to draw onto
        rotation_matrix: (3,3) float array
        center: (3, ) float array - translation vector
        """
        translation_vector = center
        points3d_on_model = self.get_vertices(model_name)
        points3d_in_reality = transform_points(points3d_on_model, rotation_matrix, translation_vector)
        points2d_on_image = project_points(points3d_in_reality, self.camera_matrix)

        faces = self.get_faces(model_name)

        faces_mid_points3d_in_reality = (
            points3d_in_reality[faces[:, 0]] +
            points3d_in_reality[faces[:, 1]] +
            points3d_in_reality[faces[:, 2]]
        ) / 3

        # face_ordering = np.argsort(-faces_mid_points3d_in_reality[:, 1]) # draw faces on each model from bottom
        face_ordering = np.argsort(-faces_mid_points3d_in_reality[:, 2])  # draw faces on each model from front

        faces = faces[face_ordering]                                  # this changes order
        colors = self.get_faces_uv_colors(model_name)[face_ordering]  # this changes order

        for vertices, color in zip(faces, colors):
            draw_poly(image, np.stack([points2d_on_image[v] for v in vertices]), (self.color_resolution*color).astype(int))

        return image

    def draw_class_mask(self, image, model_name, rotation_matrix, center):
        """
        Fills area occupied by model accordingly to model_id->model_name mapping in self.model_name_to_model_id

        image: (h, w) int valued array to draw onto
        rotation_matrix: (3,3) float array
        center: (3, ) float array - translation vector
        """

        translation_vector = center
        points3d_on_model = self.get_vertices(model_name)
        points3d_in_reality = transform_points(points3d_on_model, rotation_matrix, translation_vector)
        points2d_on_image = project_points(points3d_in_reality, self.camera_matrix)

        faces = self.get_faces(model_name)
        color = np.array([self.model_name_to_model_id[model_name]]).astype(int)

        for vertices in faces:
            draw_poly(image, np.stack([points2d_on_image[v] for v in vertices]), color)

        return image
