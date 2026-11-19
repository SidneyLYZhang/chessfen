# Recognize boards with the YOLO26-m ONNX detector, not per-square classifiers

Status: accepted

The brief asked to split the board into 64 squares and classify each, but 2d-chess-ocr publishes only whole-image detectors — its per-square classifiers exist only in training code, with no released weights. The detector's postprocess already splits the board bounding box into an 8×8 grid and assigns pieces by center, yielding the same Placement by a different mechanism. We therefore run **yolo26m-finetuned.onnx** (81,764,872 bytes; SHA-256 `a8e78afa8e00cd7ee39a941f888327bd85a12c3cf5c2140a4fa882ea3f7abff7`; 95.93% full-board accuracy on the author's 245-image OOD synthetic benchmark) through **onnxruntime with our own pre/postprocessing**, bake the weights into the Docker image from the upstream revision `03d9df9fc14fade1a3579683fd0de215b3864ee1` (checksum-gated), and stay on Python 3.13. The ONNX metadata reports the exporter version (Ultralytics 8.4.56).

## Considered Options

- **Per-square classifiers (the literal brief)**: would require generating synthetic training data and training on GPU, for no accuracy gain over the published detector. Rejected.
- **The repo's inference code as-is**: pulls in ultralytics (AGPL-3.0, incompatible with our MIT license and self-deploy guidance), torch ≥2.10, transformers ≥5.2, and Python ≥3.14.2 — a heavy image and a license conflict. Rejected.
- **RF-DETR-L** (Apache-2.0, 135 MB, 95.19%): similar accuracy but needs torch and a larger image. Rejected in favor of the smaller ONNX YOLO.
- **RF-DETR-2XL**: weights are Roboflow PML-1.0 (non-commercial) despite the MIT model card. Excluded.

## Consequences

- Recognition accepts only cropped, axis-aligned 2D board images; the detector does no perspective correction, so phone photos are out of scope for phase 1.
- We own the pre/postprocessing (resize/normalize, grid split, center assignment, orientation marker) and must pin and track upstream weight versions ourselves.
- Per-square results are exposed in the API and UI to preserve the brief's per-square intent.
- License note: the upstream repository and HuggingFace model card declare MIT, while the exported ONNX file embeds an exporter-default `license` metadata string claiming AGPL-3.0. We treat the author's repository/model-card MIT grant as the license of record (and use no ultralytics code), but the discrepancy is recorded here rather than silently ignored.
- Relicensing note: the project moved to GPL-3.0-or-later in ADR-0003; the rejection of ultralytics/AGPL still holds because AGPL imposes stronger copyleft obligations than GPL.