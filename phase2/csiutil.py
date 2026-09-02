"""CSI binary loader shared by align.py and extract_shots.py."""
from __future__ import annotations

from pathlib import Path

import numpy as np

import analysis as A

SAMPLE_DTYPE = np.dtype([
    ("dt", "<u4"), ("w", "<u2"), ("a", "<u2"), ("s", "<u2"), ("d", "<u2"),
    ("mask", "<u2"), ("flags", "<u2"),
])
BIT_M1 = 0x80


def load_csi(path: str | Path):
    raw = Path(path).read_bytes()
    body = np.frombuffer(raw[96:], dtype=SAMPLE_DTYPE)
    t_ms = np.cumsum(body["dt"].astype(np.float64)) / 1000.0
    return t_ms, body


def mouse1_edge_ms(t_ms, body) -> np.ndarray:
    m1 = (body["mask"] & BIT_M1) != 0
    return t_ms[A.rising_edges(m1)]


def session_id_from_csi(path: str | Path) -> str:
    return Path(path).stem
