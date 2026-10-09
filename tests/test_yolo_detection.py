import numpy as np

from display_project.detection.yolo import UltralyticsYoloDetector


class FakeBoxes:
    xyxy = np.asarray(
        [
            [10.0, 20.0, 18.0, 28.0],
            [100.0, 120.0, 180.0, 220.0],
            [0.0, 0.0, 50.0, 50.0],
        ],
        dtype=np.float32,
    )
    conf = np.asarray([0.81, 0.71, 0.20], dtype=np.float32)
    cls = np.asarray([0, 1, 2], dtype=np.float32)


class FakeResult:
    boxes = FakeBoxes()
    names = {0: "screw", 1: "hand", 2: "background"}


class FakeModel:
    names = {0: "screw", 1: "hand", 2: "background"}

    def __init__(self) -> None:
        self.calls = []

    def predict(self, frame, **kwargs):
        self.calls.append(kwargs)
        return [FakeResult()]


def test_yolo_detector_filters_to_requested_classes_and_threshold():
    model = FakeModel()
    detector = UltralyticsYoloDetector(
        "unused.pt",
        device="cpu",
        image_size=1280,
        model=model,
    )

    detections = detector.detect(
        np.zeros((240, 320, 3), dtype=np.uint8),
        ["screw", "hand"],
        box_threshold=0.30,
    )

    assert detections.labels == ["screw", "hand"]
    assert detections.xyxy.shape == (2, 4)
    assert detections.confidence.tolist() == [0.8100000023841858, 0.7099999785423279]
    assert model.calls[0]["imgsz"] == 1280
    assert model.calls[0]["device"] == "cpu"


def test_yolo_detector_returns_empty_when_no_requested_class_matches():
    detector = UltralyticsYoloDetector("unused.pt", model=FakeModel())

    detections = detector.detect(
        np.zeros((240, 320, 3), dtype=np.uint8),
        ["bolt"],
        box_threshold=0.30,
    )

    assert detections.labels == []
    assert detections.xyxy.shape == (0, 4)
