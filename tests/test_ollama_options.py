"""GPU-only Ollama option builders."""

from smartinbox.calendar_extract import build_calendar_ollama_options
from smartinbox.email_summary import build_summary_ollama_options
from smartinbox.ollama_options import (
    OLLAMA_GPU_LAYERS,
    build_ollama_gpu_options,
    is_dedicated_ollama_instance,
    resolve_main_gpu_for_request,
)


def test_gpu_layers_never_cpu():
    assert OLLAMA_GPU_LAYERS == -1
    opts = build_ollama_gpu_options()
    assert opts["num_gpu"] == -1
    assert "main_gpu" not in opts


def test_main_gpu_pinned():
    opts = build_ollama_gpu_options(main_gpu=1)
    assert opts["num_gpu"] == -1
    assert opts["main_gpu"] == 1


def test_summary_options_gpu_only():
    opts = build_summary_ollama_options(main_gpu=1)
    assert opts["num_gpu"] == -1
    assert opts["main_gpu"] == 1
    assert opts["num_predict"] == 2048


def test_calendar_options_gpu_only():
    opts = build_calendar_ollama_options(main_gpu=1)
    assert opts["num_gpu"] == -1
    assert opts["main_gpu"] == 1


def test_dedicated_ollama_detects_compose_ports():
    assert is_dedicated_ollama_instance("http://127.0.0.1:11435")
    assert is_dedicated_ollama_instance("http://127.0.0.1:11434")
    assert is_dedicated_ollama_instance("http://ollama-gpu0:11434")


def test_dedicated_ollama_omits_host_gpu_index():
    assert (
        resolve_main_gpu_for_request(1, base_url="http://127.0.0.1:11435")
        is None
    )
    assert (
        resolve_main_gpu_for_request(1, base_url="http://192.168.1.10:11499")
        == 1
    )