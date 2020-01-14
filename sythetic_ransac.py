import numpy as np
from DPOD.models_handler import *
import matplotlib.pyplot as plt
import numpy as np
import cv2
import matplotlib.pyplot as plt

np.random.seed(666)

models_handler = ModelsHandler('data/kaggle')
model_id = 5

points, _ = models_handler.model_id_to_vertices_and_triangles(model_id)
translation_vector = np.array([0, 0.2, 5])
rotation_matrix = np.eye(3)
rotation_rodrigues_vector = cv2.Rodrigues(rotation_matrix)[0]

img = np.zeros((2710, 3384, 3), dtype=np.uint8)
img = models_handler.draw_model(img, model_id, translation_vector, rotation_matrix)
pixels_to_consider = np.where(img[..., 0] == model_id)
pixels_to_consider = pixels_to_consider[0][::10], pixels_to_consider[1][::10]
observed_colors = img[pixels_to_consider][:, 1:]
points_implied = models_handler.get_color_to_3dpoints_arrays(model_id)[observed_colors[:, 0], observed_colors[:, 1]]
points_implied[:, 1] = -points_implied[:, 1]
points_projected = np.stack(pixels_to_consider).T.astype(float)
points = points_implied



#points_moved = transform_points(points, rotation_matrix, translation_vector)
#points_projected = project_points(points_moved, models_handler.camera_matrix)

fix_output = np.array([
    [0, 1, 0],
    [1, 0, 0],
    [0, 0, 1]
]).astype(float)

result = cv2.solvePnPRansac(points, points_projected, models_handler.camera_matrix, None)
success, ransac_rotataton_rodrigues_vector, ransac_translation_vector, inliers = result
ransac_rotataton_rodrigues_vector = ransac_rotataton_rodrigues_vector.flatten()
ransac_rotation_matrix = cv2.Rodrigues(ransac_rotataton_rodrigues_vector)[0]
ransac_translation_vector = ransac_translation_vector.flatten()
inliers = inliers.flatten()

print(translation_vector, ransac_translation_vector, sep='\n')
print(rotation_matrix, ransac_rotation_matrix, sep='\n')

img = models_handler.draw_model(img, model_id, ransac_translation_vector, ransac_rotation_matrix)
plt.imshow(img); plt.show()

'''img = np.zeros((2710, 3384, 3), dtype=np.uint8)
models_handler.draw_model(img, 1, np.array([0, 0, 10]), np.eye(3))
plt.imshow(img); plt.show()

image_points = np.where(img[..., 0] == 1)
object_points = models_handler.get_color_to_3dpoints_arrays(1)[img[image_points]]
print(image_points)
print(object_points)'''