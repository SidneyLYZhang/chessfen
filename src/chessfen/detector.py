from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, UnidentifiedImageError

from chessfen.recognition import Detection, RecognitionError

DEFAULT_CONFIDENCE = 0.25
INPUT_SIZE = 640
PAD_VALUE = 114


class OnnxDetector:
    """YOLO26 end-to-end ONNX detector (NMS-free) for chessboard OCR.

    The exported model expects a 640x640 RGB letterboxed image and returns
    (x1, y1, x2, y2, confidence, class_id) rows in letterbox coordinates.
    Weights are loaded lazily so the app can boot without them.
    """

    def __init__(
        self,
        model_path: str | Path,
        confidence: float = DEFAULT_CONFIDENCE,
    ) -> None:
        self._model_path = Path(model_path)
        self._confidence = confidence
        self._session: Any = None

    def detect(self, image_bytes: bytes) -> list[Detection]:
        session = self._session or self._load_session()
        image = _decode_image(image_bytes)
        tensor, scale, pad_x, pad_y = _preprocess(image)
        input_name = session.get_inputs()[0].name
        outputs = session.run(None, {input_name: tensor})
        rows = outputs[0][0]
        width, height = image.size
        detections: list[Detection] = []
        for row in rows:
            confidence = float(row[4])
            if confidence < self._confidence:
                continue
            x1 = _clamp((float(row[0]) - pad_x) / scale, 0.0, float(width))
            y1 = _clamp((float(row[1]) - pad_y) / scale, 0.0, float(height))
            x2 = _clamp((float(row[2]) - pad_x) / scale, 0.0, float(width))
            y2 = _clamp((float(row[3]) - pad_y) / scale, 0.0, float(height))
            detections.append(
                Detection(
                    class_id=int(row[5]),
                    confidence=confidence,
                    xyxy=(x1, y1, x2, y2),
                )
            )
        return detections

    def _load_session(self) -> Any:
        if not self._model_path.is_file():
            raise RecognitionError("recognition_unavailable")
        try:
            import onnxruntime as ort

            self._session = ort.InferenceSession(
                str(self._model_path), providers=["CPUExecutionProvider"]
            )
        except Exception as exc:
            raise RecognitionError("recognition_unavailable") from exc
        return self._session


def _decode_image(image_bytes: bytes) -> Image.Image:
    try:
        return Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise RecognitionError("invalid_image") from exc


def _preprocess(image: Image.Image) -> tuple[np.ndarray, float, int, int]:
    width, height = image.size
    if width <= 0 or height <= 0:
        raise RecognitionError("invalid_image")
    scale = min(INPUT_SIZE / width, INPUT_SIZE / height)
    new_width = max(1, round(width * scale))
    new_height = max(1, round(height * scale))
    resized = image.resize((new_width, new_height), Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (INPUT_SIZE, INPUT_SIZE), (PAD_VALUE, PAD_VALUE, PAD_VALUE))
    pad_x = (INPUT_SIZE - new_width) // 2
    pad_y = (INPUT_SIZE - new_height) // 2
    canvas.paste(resized, (pad_x, pad_y))
    array = np.asarray(canvas, dtype=np.float32) / 255.0
    tensor = np.ascontiguousarray(array.transpose(2, 0, 1)[None, ...])
    return tensor, scale, pad_x, pad_y


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
