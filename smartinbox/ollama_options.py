"""Ollama runtime options — GPU inference only (never CPU layers)."""

from __future__ import annotations

from typing import Any

# Linux: offload every layer to GPU. num_gpu=0 would run layers on CPU.
OLLAMA_GPU_LAYERS = -1


def is_dedicated_ollama_instance(base_url: str) -> bool:
    """True when base_url targets a single-GPU Docker Ollama (one device: index 0)."""
    url = (base_url or "").strip().lower().rstrip("/")
    if ":11434" in url or ":11435" in url:
        return True
    if "ollama-gpu0" in url:
        return True
    if "://ollama:" in url or url.endswith("/ollama"):
        return True
    return False


def resolve_main_gpu_for_request(
    main_gpu: int | None, *, base_url: str
) -> int | None:
    """Map config GPU to Ollama API options.

    Per-GPU Ollama containers expose a single GPU as device 0. Host indices
    (e.g. main_gpu: 1 on the :11434 instance) break model loads.
    """
    if is_dedicated_ollama_instance(base_url):
        return None
    return main_gpu


def build_ollama_gpu_options(
    *,
    main_gpu: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Base Ollama options for GPU-only inference."""
    opts: dict[str, Any] = {"num_gpu": OLLAMA_GPU_LAYERS}
    opts.update(extra)
    if main_gpu is not None and main_gpu >= 0:
        opts["main_gpu"] = int(main_gpu)
    return opts