import numpy as np


def compute_homography(
    image_points: list[tuple[float, float]],
    field_points: list[tuple[float, float]],
) -> list[list[float]]:
    if len(image_points) < 4:
        raise ValueError("At least 4 point correspondences are required")
    if len(image_points) != len(field_points):
        raise ValueError("image_points and field_points must have equal length")

    rows: list[list[float]] = []
    for (x, y), (u, v) in zip(image_points, field_points, strict=True):
        rows.append([-x, -y, -1.0, 0.0, 0.0, 0.0, x * u, y * u, u])
        rows.append([0.0, 0.0, 0.0, -x, -y, -1.0, x * v, y * v, v])

    a = np.asarray(rows, dtype=np.float64)
    _, _, vh = np.linalg.svd(a)
    h = vh[-1, :]

    matrix = h.reshape((3, 3))
    if np.isclose(matrix[2, 2], 0.0):
        raise ValueError("Degenerate homography")

    matrix = matrix / matrix[2, 2]
    return matrix.tolist()


def project_image_to_field(homography: list[list[float]], x: float, y: float) -> tuple[float, float] | None:
    h = np.asarray(homography, dtype=np.float64)
    point = np.asarray([x, y, 1.0], dtype=np.float64)
    projected = h @ point
    if np.isclose(projected[2], 0.0):
        return None
    field_x = projected[0] / projected[2]
    field_y = projected[1] / projected[2]
    return float(field_x), float(field_y)
