"""Local GigaAM ONNX paths (data/models, no CDN at runtime)."""

from __future__ import annotations

from infrastructure.paths import lira_data

ONNX_MODEL_ID = "gigaam-v3-e2e-rnnt"
MODEL_DIR = lira_data("models", "gigaam-v3-e2e-rnnt")
HF_MODEL_REPO = "istupakov/gigaam-v3-onnx"

REQUIRED_MODEL_FILES = (
    "config.json",
    "v3_e2e_rnnt_encoder.onnx",
    "v3_e2e_rnnt_decoder.onnx",
    "v3_e2e_rnnt_joint.onnx",
    "v3_e2e_rnnt_vocab.txt",
)

MODEL_DOWNLOAD_FILES = (
    *REQUIRED_MODEL_FILES,
    "v3_e2e_rnnt.yaml",
)
