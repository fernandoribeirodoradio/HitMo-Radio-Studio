from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Lock

import numpy as np

SAMPLE_RATE = 48_000
CHANNELS = 2
DEFAULT_MASTER_GAIN = 10 ** (-6 / 20)
LIMITER_CEILING = 0.98


class PCMQueue:
    """Bounded, thread-safe FIFO for decoded 48 kHz stereo float frames."""

    def __init__(self, max_frames: int = SAMPLE_RATE * 2):
        self._blocks: deque[np.ndarray] = deque()
        self._offset = 0
        self._frames = 0
        self._max_frames = max_frames
        self._lock = Lock()
        self.dropped_frames = 0

    def push(self, samples: np.ndarray) -> None:
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim != 2 or samples.shape[1] != CHANNELS or not len(samples):
            return
        samples = np.ascontiguousarray(samples)
        with self._lock:
            if len(samples) > self._max_frames:
                self.dropped_frames += len(samples) - self._max_frames
                samples = samples[-self._max_frames:]
            overflow = self._frames + len(samples) - self._max_frames
            while overflow > 0 and self._blocks:
                available = len(self._blocks[0]) - self._offset
                discard = min(overflow, available)
                self._offset += discard
                self._frames -= discard
                self.dropped_frames += discard
                overflow -= discard
                if self._offset == len(self._blocks[0]):
                    self._blocks.popleft()
                    self._offset = 0
            self._blocks.append(samples)
            self._frames += len(samples)

    def pop(self, frames: int) -> np.ndarray:
        result = np.zeros((frames, CHANNELS), dtype=np.float32)
        written = 0
        with self._lock:
            while written < frames and self._blocks:
                block = self._blocks[0]
                available = len(block) - self._offset
                count = min(frames - written, available)
                result[written:written + count] = block[self._offset:self._offset + count]
                self._offset += count
                self._frames -= count
                written += count
                if self._offset == len(block):
                    self._blocks.popleft()
                    self._offset = 0
        return result

    def clear(self) -> None:
        with self._lock:
            self._blocks.clear()
            self._offset = 0
            self._frames = 0

    @property
    def queued_frames(self) -> int:
        with self._lock:
            return self._frames


@dataclass(frozen=True)
class MixMeter:
    peak: float
    rms: float
    limiter_gain: float


def equal_power_crossfade(frame_count: int, start_frame: int, total_frames: int) -> tuple[np.ndarray, np.ndarray]:
    """Return cosine/sine equal-power gains for a deterministic sample range."""
    if total_frames <= 0:
        raise ValueError("A duração do crossfade deve ser positiva.")
    progress = np.clip((start_frame + np.arange(frame_count, dtype=np.float32)) / total_frames, 0.0, 1.0)
    angle = progress * (np.pi / 2.0)
    return np.cos(angle).astype(np.float32), np.sin(angle).astype(np.float32)


def mix_pcm(
    sources: dict[str, np.ndarray],
    gains: dict[str, float | np.ndarray],
    muted: set[str] | frozenset[str] = frozenset(),
    *,
    bus_gain: float = 1.0,
    master_gain: float = DEFAULT_MASTER_GAIN,
    ceiling: float = LIMITER_CEILING,
) -> tuple[np.ndarray, MixMeter]:
    """Sum source PCM, apply channel/bus/master gain and a linked peak limiter."""
    if not sources:
        return np.zeros((0, CHANNELS), dtype=np.float32), MixMeter(0.0, 0.0, 1.0)
    frame_count = max(len(np.asarray(source)) for source in sources.values())
    mixed = np.zeros((frame_count, CHANNELS), dtype=np.float32)
    for source_id, source in sources.items():
        if source_id in muted:
            continue
        data = np.asarray(source, dtype=np.float32)
        if data.ndim != 2 or data.shape[1] != CHANNELS:
            raise ValueError(f"A fonte {source_id} precisa ter amostras estéreo float.")
        gain = gains.get(source_id, 1.0)
        count = min(frame_count, len(data))
        if np.isscalar(gain):
            mixed[:count] += data[:count] * float(gain)
        else:
            envelope = np.asarray(gain, dtype=np.float32).reshape(-1)
            if len(envelope) < count:
                envelope = np.pad(envelope, (0, count - len(envelope)), constant_values=envelope[-1] if len(envelope) else 1.0)
            mixed[:count] += data[:count] * envelope[:count, None]
    mixed *= float(bus_gain) * float(master_gain)
    pre_limit_peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    limiter_gain = min(1.0, float(ceiling) / pre_limit_peak) if pre_limit_peak > 0 else 1.0
    if limiter_gain < 1.0:
        mixed *= limiter_gain
        safe_ceiling = float(np.nextafter(np.float32(ceiling), np.float32(0.0)))
        np.clip(mixed, -safe_ceiling, safe_ceiling, out=mixed)
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(mixed, dtype=np.float64)))) if mixed.size else 0.0
    return mixed, MixMeter(peak, rms, limiter_gain)
