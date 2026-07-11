#!/usr/bin/env python3
"""
Model Manager — RAM-efficient model swapping for low-resource VMs.

On a 1.8GB RAM VM, we can't keep all models loaded simultaneously.
This module provides a singleton ModelManager that:
  - Loads a model on demand
  - Unloads the previous model (gc.collect + del) before loading a new one
  - Uses 8GB swap as overflow for model weights
  - Tracks memory usage and forces cleanup when needed

Models managed:
  - Whisper (faster-whisper): ~500MB-2GB depending on size
  - WhisperX (with alignment): ~900MB-1.5GB
  - Kokoro TTS: ~1.1GB
  - XTTS-v2 (local): ~1.8GB
  - OpenVoice V2: ~400MB
  - Demucs: ~800MB
  - Diarizer: ~300MB
"""

import gc
import threading
import time
import os
import psutil


class ModelManager:
    """Singleton model manager that swaps models in/out of RAM."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._loaded_model = None  # (name, model_object)
        self._model_lock = threading.Lock()
        self._available_mem = psutil.virtual_memory().total

    def get_loaded_name(self):
        """Return the name of the currently loaded model, or None."""
        with self._model_lock:
            return self._loaded_model[0] if self._loaded_model else None

    def get_memory_usage(self) -> dict:
        """Return current memory stats."""
        vm = psutil.virtual_memory()
        sm = psutil.swap_memory()
        proc = psutil.Process()
        return {
            "ram_total_mb": vm.total / 1024 / 1024,
            "ram_used_mb": vm.used / 1024 / 1024,
            "ram_available_mb": vm.available / 1024 / 1024,
            "ram_percent": vm.percent,
            "swap_total_mb": sm.total / 1024 / 1024,
            "swap_used_mb": sm.used / 1024 / 1024,
            "process_rss_mb": proc.memory_info().rss / 1024 / 1024,
            "loaded_model": self.get_loaded_name(),
        }

    def unload_current(self):
        """Unload the currently loaded model and free memory."""
        with self._model_lock:
            if self._loaded_model is None:
                return
            name, model = self._loaded_model
            self._loaded_model = None
            try:
                del model
            except Exception:
                pass
            gc.collect()
            # Force Python to release memory back to OS
            try:
                import ctypes
                ctypes.CDLL("libc.so.6").malloc_trim(0)
            except Exception:
                pass
            print(f"        [ModelManager] Unloaded '{name}', freed memory")
            self._print_mem()

    def load_model(self, name: str, loader_fn):
        """Load a model by name, unloading any previously loaded model first.
        
        Args:
            name: unique model identifier (e.g., 'whisper-large-v3', 'kokoro')
            loader_fn: callable that returns the model object
            
        Returns:
            The loaded model object
        """
        with self._model_lock:
            # Already loaded?
            if self._loaded_model and self._loaded_model[0] == name:
                return self._loaded_model[1]

            # Unload previous model
            if self._loaded_model is not None:
                old_name, old_model = self._loaded_model
                self._loaded_model = None
                try:
                    del old_model
                except Exception:
                    pass
                gc.collect()
                try:
                    import ctypes
                    ctypes.CDLL("libc.so.6").malloc_trim(0)
                except Exception:
                    pass
                print(f"        [ModelManager] Swapped '{old_name}' → '{name}'")

            # Load new model
            self._print_mem()
            model = loader_fn()
            self._loaded_model = (name, model)
            self._print_mem()
            return model

    def _print_mem(self):
        """Print current memory usage (debug)."""
        vm = psutil.virtual_memory()
        proc = psutil.Process()
        print(f"        [ModelManager] RAM: {vm.available / 1024 / 1024:.0f}MB avail / "
              f"{vm.used / 1024 / 1024:.0f}MB used, "
              f"Proc: {proc.memory_info().rss / 1024 / 1024:.0f}MB RSS")


# Global singleton
_model_manager = ModelManager()
