"""Estimate camera motion from sparse optical flow near the frame edges."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from shotforge.checks import FAIL, PASS, WARN, Finding
from shotforge.models import Shot

SAMPLE_RATE = 6
MOTION_LIMIT = 0.04
CHECK = "camera motion"


def _frames(video: Path) -> Iterator[tuple[float, NDArray]]:
    capture = cv2.VideoCapture(str(video))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not np.isfinite(fps) or fps <= 0:
            return
        stride = max(1, round(fps / SAMPLE_RATE))
        index = 0
        while capture.grab():
            if index % stride == 0:
                ok, frame = capture.retrieve()
                if not ok:
                    return
                height, width = frame.shape[:2]
                scale = min(1, 640 / max(height, width))
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                if scale < 1:
                    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                yield index / fps, gray
            index += 1
    finally:
        capture.release()


def _background_transform(before: NDArray, after: NDArray, mask: NDArray) -> NDArray | None:
    points = cv2.goodFeaturesToTrack(
        before, maxCorners=400, qualityLevel=0.01, minDistance=6, mask=mask
    )
    if points is None or len(points) < 12:
        return None

    tracked, visible, _ = cv2.calcOpticalFlowPyrLK(before, after, points, None)
    if tracked is None or visible is None:
        return None
    backwards, returned, _ = cv2.calcOpticalFlowPyrLK(after, before, tracked, None)
    if backwards is None or returned is None:
        return None
    consistent = np.linalg.norm(points[:, 0] - backwards[:, 0], axis=1) < 1.5
    good = visible.ravel().astype(bool) & returned.ravel().astype(bool) & consistent
    source, target = points[good, 0], tracked[good, 0]
    if len(source) < 12:
        return None

    matrix, inliers = cv2.estimateAffinePartial2D(source, target, ransacReprojThreshold=2)
    if matrix is None or inliers is None:
        return None
    supported = inliers.ravel().astype(bool)
    if supported.sum() < max(12, len(source) / 2):
        return None

    # A moving person at one edge should not stand in for the whole background.
    height, width = before.shape
    x, y = source[supported].T
    quadrants = (x >= width / 2).astype(int) + 2 * (y >= height / 2)
    if np.ptp(x) < width * 0.45 or np.ptp(y) < height * 0.35 or len(set(quadrants)) < 3:
        return None
    return np.vstack((matrix, [0, 0, 1]))


def _extent(transform: NDArray, width: int, height: int) -> float:
    center = np.array([width / 2, height / 2])
    linear = transform[:2, :2]
    shift = np.linalg.norm(linear @ center + transform[:2, 2] - center) / width
    scale = np.hypot(*linear[:, 0])
    angle = np.arctan2(linear[1, 0], linear[0, 0])
    rotation = np.hypot(width, height) * abs(np.sin(angle / 2)) / width
    # Measure a push-in by the fractional change in image width.
    return max(shift, abs(scale - 1), rotation)


def check_motion(video: Path, shot: Shot) -> Finding:
    try:
        with closing(_frames(video)) as samples:
            peak, accelerating, tracked, total = _measure(samples)
    except (cv2.error, OSError, ValueError):
        return Finding(CHECK, WARN, "could not analyze camera motion in this clip")
    if not tracked:
        return Finding(CHECK, WARN, "could not track enough background features")

    plan = shot.camera.move.strip().lower()
    movement = f"background motion reached {peak:.1%} of frame width."
    if tracked >= total * 2 / 3:
        if accelerating is None:
            movement += " Too few samples to estimate acceleration."
        else:
            movement += " Accelerating." if accelerating else " No sustained acceleration."
    if peak >= MOTION_LIMIT and plan in ("", "static"):
        return Finding(CHECK, FAIL, movement, fix="regenerate with a locked-off camera")
    if tracked < total * 2 / 3:
        return Finding(
            CHECK, WARN, f"{movement} Background tracked in {tracked}/{total} intervals."
        )
    return Finding(CHECK, PASS, movement)


def _measure(samples: Iterator[tuple[float, NDArray]]) -> tuple[float, bool | None, int, int]:
    first = next(samples, None)
    if first is None:
        return 0.0, None, 0, 0
    start, before = first
    height, width = before.shape
    mask = np.full_like(before, 255)
    mask[height // 5 : 4 * height // 5, width // 5 : 4 * width // 5] = 0
    transform = np.eye(3)
    peak = 0.0
    speeds: list[list[float]] = [[]]
    tracked = total = 0
    for end, after in samples:
        total += 1
        step = _background_transform(before, after, mask)
        if step is None:
            transform = np.eye(3)
            if speeds[-1]:
                speeds.append([])
        else:
            tracked += 1
            speeds[-1].append(_extent(step, width, height) / (end - start))
            transform = step @ transform
            peak = max(peak, _extent(transform, width, height))
        start, before = end, after
    longest = max(speeds, key=len)
    third = len(longest) // 3
    accelerating = None
    if third >= 2:
        early = np.median(longest[:third])
        late = np.median(longest[-third:])
        accelerating = bool(peak >= MOTION_LIMIT and late > early * 1.5 + 0.01)
    return peak, accelerating, tracked, total
