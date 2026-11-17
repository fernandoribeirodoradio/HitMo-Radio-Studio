from __future__ import annotations

import time
from pathlib import Path
from threading import RLock

import numpy as np
from shiboken6 import isValid as _qt_object_is_valid
from PySide6.QtCore import QIODevice, QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import (
    QAudio, QAudioBufferOutput, QAudioFormat, QAudioSink, QMediaDevices, QMediaPlayer,
)

from .audio_mixer import (
    CHANNELS, DEFAULT_MASTER_GAIN, LIMITER_CEILING, SAMPLE_RATE, PCMQueue,
    equal_power_crossfade, mix_pcm,
)


class _ChannelControl(QObject):
    """Compatibility volume/mute facade whose controls feed the software mixer."""
    volumeChanged = Signal(float)
    mutedChanged = Signal(bool)

    def __init__(self, engine: "AudioEngine", channel_id: str | None = None, bus: str = "PGM"):
        super().__init__(engine)
        self.engine = engine
        self.channel_id = channel_id
        self.bus = bus

    def volume(self) -> float:
        if self.channel_id:
            return self.engine.channel_gain(self.channel_id)
        return self.engine.bus_gain(self.bus)

    def setVolume(self, value: float) -> None:
        value = min(1.0, max(0.0, float(value)))
        if self.channel_id:
            self.engine.set_channel_gain(self.channel_id, value)
        else:
            self.engine.set_bus_gain(self.bus, value)
        self.volumeChanged.emit(value)

    def isMuted(self) -> bool:
        if self.channel_id:
            return self.engine.channel_muted(self.channel_id)
        return self.engine.bus_muted(self.bus)

    def setMuted(self, muted: bool) -> None:
        if self.channel_id:
            self.engine.set_channel_muted(self.channel_id, muted)
        else:
            self.engine.set_bus_muted(self.bus, muted)
        self.mutedChanged.emit(bool(muted))


class _SinkSource(QIODevice):
    """Sequential PCM source consumed by QAudioSink on its output callback."""
    def __init__(self, bus: str, engine: "AudioEngine", fmt: QAudioFormat):
        super().__init__(engine)
        self.bus, self.engine, self.fmt = bus, engine, fmt
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    def isSequential(self) -> bool:
        return True

    def bytesAvailable(self) -> int:
        # QAudioSink needs a nonzero availability hint to start pulling data.
        return max(self.fmt.bytesPerFrame() * 1024, super().bytesAvailable())

    def readData(self, max_size: int) -> bytes:
        frame_size = max(1, self.fmt.bytesPerFrame())
        frames = max(0, max_size // frame_size)
        if not frames:
            return b""
        pcm = self.engine._render_bus(self.bus, frames)
        return self.engine._encode_output(pcm, self.fmt)

    def writeData(self, data: bytes) -> int:
        return -1


class _AudioBus(QObject):
    error = Signal(str, str)

    def __init__(self, name: str, engine: "AudioEngine"):
        super().__init__(engine)
        self.engine = engine
        self.name = name
        self.sink: QAudioSink | None = None
        self.source: _SinkSource | None = None
        self.device_id = ""
        self.actual_format = QAudioFormat()
        self.error.connect(engine._on_bus_error)

    def close(self) -> None:
        if self.sink:
            sink, self.sink = self.sink, None
            sink.stateChanged.disconnect()
            sink.stop()
            sink.deleteLater()
            self.sink = None
        if self.source:
            self.source.close()
            self.source.deleteLater()
            self.source = None
        self.device_id = ""

    def configure(self, device, device_id: str) -> None:
        self.close()
        if device.isNull():
            return
        mix_format = QAudioFormat()
        mix_format.setSampleRate(SAMPLE_RATE)
        mix_format.setChannelCount(CHANNELS)
        mix_format.setSampleFormat(QAudioFormat.SampleFormat.Float)
        if device.isFormatSupported(mix_format):
            out_format = mix_format
        else:
            out_format = device.preferredFormat()
        sink = QAudioSink(device, out_format, self)
        source = _SinkSource(self.name, self.engine, out_format)
        sink.setBufferSize(max(out_format.bytesPerFrame() * 960, out_format.bytesPerFrame() * 2))
        sink.stateChanged.connect(lambda state, s=sink, b=self.name: self._state_changed(b, s, state))
        self.sink, self.source = sink, source
        self.device_id = device_id
        self.actual_format = out_format
        sink.start(source)

    def _state_changed(self, bus: str, sink: QAudioSink, state) -> None:
        if sink is not self.sink or not _qt_object_is_valid(sink):
            return
        if sink.error() != QAudio.Error.NoError:
            self.error.emit(bus, f"Saída {bus}: {sink.error().name}")


class AudioEngine(QObject):
    """Qt decoders feed a shared PCM mixer and independent PGM/CUE device sinks."""

    program_state = Signal(object)
    preview_state = Signal(object)
    loop_state = Signal(object)
    program_position = Signal(int, int)
    preview_position = Signal(int, int)
    loop_position = Signal(int, int)
    program_error = Signal(str)
    preview_error = Signal(str)
    loop_error = Signal(str)
    program_finished = Signal()
    program_faded_out = Signal(int)
    preview_finished = Signal()
    cart_state = Signal(int, object)
    cart_finished = Signal(int)
    cart_error = Signal(int, str)
    bus_meter = Signal(str, float, float)
    bus_error = Signal(str, str)
    routing_changed = Signal(str, str)

    def __init__(self, output_id: str = "", cue_output_id: str = ""):
        super().__init__()
        self._lock = RLock()
        self._channels: dict[str, dict] = {}
        self._routes: dict[str, str] = {}
        self._bus_gains = {"PGM": 1.0, "CUE": 1.0}
        self._bus_mutes = {"PGM": False, "CUE": False}
        self._master_gain = DEFAULT_MASTER_GAIN
        self._crossfades: dict[str, dict] = {}
        self._player_channels: dict[int, str] = {}
        self._end_generations: dict[str, int] = {}
        self._buses = {name: _AudioBus(name, self) for name in ("PGM", "CUE")}
        self.program_output = _ChannelControl(self, "program", "PGM")
        self.preview_output = _ChannelControl(self, "preview", "CUE")
        self.program = QMediaPlayer(self)
        self.preview = QMediaPlayer(self)
        self._loop_players: dict[str, tuple[QMediaPlayer, _ChannelControl]] = {}
        self._cart_players: dict[int, tuple[QMediaPlayer, _ChannelControl]] = {}
        self._cart_active: set[int] = set()
        self._program_fade_started = 0.0
        self._program_fade_duration_ms = 0
        self._program_fade_start_volume = 1.0
        self._program_fade_timer = QTimer(self)
        self._program_fade_timer.setInterval(15)
        self._program_fade_timer.timeout.connect(self._advance_program_fade)
        self.cue_configuration_error = ""
        self._create_player_channel("program", self.program, "PGM")
        self._create_player_channel("preview", self.preview, "CUE")
        self.program.errorOccurred.connect(lambda _error, text: self._error(self.program, text))
        self.preview.errorOccurred.connect(lambda _error, text: self._error(self.preview, text))
        for slot in range(1, 13):
            player = QMediaPlayer(self)
            channel = f"cart:{slot}"
            control = self._create_player_channel(channel, player, "PGM")
            player.playbackStateChanged.connect(lambda state, number=slot: self.cart_state.emit(number, state))
            player.errorOccurred.connect(lambda _error, message, number=slot: self._cart_error(number, message))
            player.mediaStatusChanged.connect(lambda status, number=slot: self._cart_media_status(number, status))
            self._cart_players[slot] = (player, control)
        self.program.playbackStateChanged.connect(lambda state: self.program_state.emit(state))
        self.preview.playbackStateChanged.connect(lambda state: self.preview_state.emit(state))
        self.program.positionChanged.connect(lambda p: self.program_position.emit(p, self.program.duration()))
        self.preview.positionChanged.connect(lambda p: self.preview_position.emit(p, self.preview.duration()))
        self.program.mediaStatusChanged.connect(self._program_media_status)
        self.preview.mediaStatusChanged.connect(self._preview_media_status)
        self.set_output(output_id)
        if cue_output_id:
            try:
                if not self.set_cue_output(cue_output_id):
                    self.cue_configuration_error = "O dispositivo CUE salvo não está disponível."
            except ValueError as exc:
                self.cue_configuration_error = str(exc)

    def _create_player_channel(self, channel_id: str, player: QMediaPlayer, bus: str) -> _ChannelControl:
        fmt = QAudioFormat()
        fmt.setSampleRate(SAMPLE_RATE)
        fmt.setChannelCount(CHANNELS)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        buffer_output = QAudioBufferOutput(fmt, player)
        player.setAudioOutput(None)
        player.setAudioBufferOutput(buffer_output)
        queue = PCMQueue()
        with self._lock:
            self._channels[channel_id] = {"queue": queue, "gain": 1.0, "muted": False, "control": None, "player": player}
            self._routes[channel_id] = bus
            self._player_channels[id(player)] = channel_id
            self._end_generations.setdefault(channel_id, 0)
        buffer_output.audioBufferReceived.connect(lambda buffer, key=channel_id: self._buffer_received(key, buffer))
        player._hitmo_buffer_output = buffer_output
        control = _ChannelControl(self, channel_id, bus)
        self._channels[channel_id]["control"] = control
        return control

    def _buffer_received(self, channel_id: str, buffer) -> None:
        if not buffer.isValid() or buffer.frameCount() <= 0 or not buffer.data():
            return
        try:
            samples = np.frombuffer(buffer.data(), dtype=np.float32).reshape(-1, CHANNELS).copy()
        except (ValueError, TypeError):
            return
        with self._lock:
            channel = self._channels.get(channel_id)
            if channel:
                channel["queue"].push(samples)

    def _render_bus(self, bus: str, frames: int) -> np.ndarray:
        with self._lock:
            channel_ids = [key for key, route in self._routes.items() if route == bus]
            sources = {key: self._channels[key]["queue"].pop(frames) for key in channel_ids}
            gains = {key: self._channel_gain_for_render(key, frames) for key in channel_ids}
            muted = {key for key in channel_ids if self._channels[key]["muted"]}
            out, meter = mix_pcm(
                sources, gains, muted, bus_gain=self._bus_gains[bus],
                master_gain=self._master_gain, ceiling=LIMITER_CEILING,
            )
            if self._bus_mutes[bus]:
                out.fill(0)
                meter_peak = meter_rms = 0.0
            else:
                meter_peak, meter_rms = meter.peak, meter.rms
        self.bus_meter.emit(bus, meter_peak, meter_rms)
        return out

    def _channel_gain_for_render(self, channel_id: str, frames: int):
        fade = self._crossfades.get(channel_id)
        base = self._channels[channel_id]["gain"]
        if not fade:
            return base
        if fade.get("finished"):
            final_gain = 0.0 if fade["direction"] == "out" else 1.0
            return np.full(frames, base * final_gain, dtype=np.float32)
        start, total, direction = fade["position"], fade["total"], fade["direction"]
        a, b = equal_power_crossfade(frames, start, total)
        envelope = b if direction == "in" else a
        fade["position"] = min(total, start + frames)
        if fade["position"] >= total:
            fade["finished"] = True
        return envelope * base

    @staticmethod
    def _encode_output(pcm: np.ndarray, fmt: QAudioFormat) -> bytes:
        rate = fmt.sampleRate()
        if rate != SAMPLE_RATE and len(pcm):
            out_count = round(len(pcm) * rate / SAMPLE_RATE)
            old = np.arange(len(pcm), dtype=np.float64)
            new = np.arange(out_count, dtype=np.float64) * SAMPLE_RATE / rate
            pcm = np.column_stack([np.interp(new, old, pcm[:, ch]) for ch in range(CHANNELS)]).astype(np.float32)
        channel_count = fmt.channelCount()
        if channel_count == 1:
            pcm = pcm.mean(axis=1, keepdims=True)
        elif channel_count > 2:
            pcm = np.pad(pcm, ((0, 0), (0, channel_count - 2)))
        sample_format = fmt.sampleFormat()
        if sample_format == QAudioFormat.SampleFormat.Float:
            return np.asarray(pcm, dtype=np.float32).tobytes()
        if sample_format == QAudioFormat.SampleFormat.Int16:
            return (np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes()
        if sample_format == QAudioFormat.SampleFormat.Int32:
            return (np.clip(pcm, -1, 1) * 2147483647).astype("<i4").tobytes()
        if sample_format == QAudioFormat.SampleFormat.UInt8:
            return (np.clip(pcm, -1, 1) * 127.5 + 127.5).astype("u1").tobytes()
        return b"\x00" * (len(pcm) * max(1, fmt.bytesPerFrame()))

    def _on_bus_error(self, bus: str, message: str) -> None:
        self.bus_error.emit(bus, message)

    def _finish_after_output_drain(self, channel_id: str, player: QMediaPlayer, callback) -> None:
        generation = self._end_generations.get(channel_id, 0) + 1
        self._end_generations[channel_id] = generation

        def check_queue() -> None:
            if self._end_generations.get(channel_id) != generation:
                return
            channel = self._channels.get(channel_id)
            if channel is None:
                return
            bus = self._routes[channel_id]
            sink = self._buses[bus].sink
            if sink is None or sink.state() != QAudio.State.ActiveState:
                channel["queue"].clear()
            if channel["queue"].queued_frames:
                QTimer.singleShot(10, check_queue)
                return
            QTimer.singleShot(25, finish)

        def finish() -> None:
            if self._end_generations.get(channel_id) != generation:
                return
            channel = self._channels.get(channel_id)
            if channel is None:
                return
            if channel["queue"].queued_frames:
                check_queue()
                return
            self._end_generations[channel_id] += 1
            player.setSource(QUrl())
            callback()

        QTimer.singleShot(10, check_queue)

    def _resolve_device(self, device_id: str):
        devices = QMediaDevices.audioOutputs()
        if device_id:
            return next((d for d in devices if bytes(d.id()).decode(errors="ignore") == device_id), None)
        return QMediaDevices.defaultAudioOutput()

    @classmethod
    def _device_id_for_selection(cls, device_id: str) -> str:
        device = cls._resolve_device(device_id)
        return bytes(device.id()).decode(errors="ignore") if device and not device.isNull() else ""

    def set_output(self, device_id: str) -> None:
        device = self._resolve_device(device_id)
        if device is None or device.isNull():
            outputs = QMediaDevices.audioOutputs()
            device = outputs[0] if outputs else None
        if device is None:
            return
        ident = bytes(device.id()).decode(errors="ignore")
        cue = self._buses["CUE"]
        if cue.device_id and cue.device_id == ident:
            raise ValueError("PGM e CUE precisam usar dispositivos de saída distintos.")
        self._buses["PGM"].configure(device, ident)

    def configure_outputs(self, pgm_device_id: str, cue_device_id: str) -> None:
        if any(
            channel["player"].playbackState() != QMediaPlayer.PlaybackState.StoppedState
            or channel["queue"].queued_frames
            for channel in self._channels.values()
        ):
            raise RuntimeError("Pare todas as fontes antes de reconfigurar saídas de áudio.")
        pgm = self._resolve_device(pgm_device_id)
        cue = self._resolve_device(cue_device_id) if cue_device_id else None
        if pgm is None or pgm.isNull():
            raise RuntimeError("Não há uma saída PGM disponível.")
        if cue_device_id and (cue is None or cue.isNull()):
            raise RuntimeError("O dispositivo CUE selecionado não está disponível.")
        pgm_id = bytes(pgm.id()).decode(errors="ignore")
        cue_id = bytes(cue.id()).decode(errors="ignore") if cue else ""
        if cue_id and cue_id == pgm_id:
            raise ValueError("PGM e CUE precisam usar dispositivos de saída distintos.")
        # Validation precedes either mutation, avoiding partial routing changes.
        self._buses["CUE"].close()
        self._buses["PGM"].configure(pgm, pgm_id)
        if cue:
            self._buses["CUE"].configure(cue, cue_id)

    def set_cue_output(self, device_id: str) -> bool:
        if not device_id:
            self._buses["CUE"].close()
            self.cue_configuration_error = ""
            return True
        device = self._resolve_device(device_id)
        if device is None or device.isNull():
            self._buses["CUE"].close()
            self.cue_configuration_error = "O dispositivo CUE salvo não está disponível."
            return False
        ident = bytes(device.id()).decode(errors="ignore")
        if ident == self._buses["PGM"].device_id:
            raise ValueError("PGM e CUE precisam usar dispositivos de saída distintos.")
        self._buses["CUE"].configure(device, ident)
        self.cue_configuration_error = ""
        return True

    @property
    def cue_available(self) -> bool:
        return self._buses["CUE"].sink is not None

    @property
    def pgm_device_id(self) -> str:
        return self._buses["PGM"].device_id

    @property
    def cue_device_id(self) -> str:
        return self._buses["CUE"].device_id

    def set_channel_route(self, channel_id: str, bus: str) -> None:
        bus = bus.upper()
        if bus not in self._buses:
            raise ValueError("O destino deve ser PGM ou CUE.")
        fixed_routes = {"program": "PGM", "preview": "CUE"}
        if channel_id in fixed_routes and bus != fixed_routes[channel_id]:
            raise ValueError(f"A fonte {channel_id} permanece no barramento {fixed_routes[channel_id]}.")
        if bus == "CUE" and not self.cue_available:
            raise RuntimeError("CUE indisponível: configure uma saída física diferente da saída PGM.")
        with self._lock:
            if channel_id not in self._channels:
                raise KeyError(channel_id)
            player = self._channels[channel_id]["player"]
            if player.playbackState() != QMediaPlayer.PlaybackState.StoppedState or player.source().isValid():
                raise RuntimeError("A fonte precisa estar descarregada antes de mudar seu destino PGM/CUE.")
            self._channels[channel_id]["queue"].clear()
            self._routes[channel_id] = bus
        self.routing_changed.emit(channel_id, bus)

    def channel_route(self, channel_id: str) -> str:
        return self._routes[channel_id]

    def set_cartouche_route(self, slot: int, bus: str) -> None:
        self.set_channel_route(f"cart:{int(slot)}", bus)

    def set_loop_route(self, path: str, bus: str) -> None:
        key = self._loop_key(path)
        if key not in self._loop_players:
            raise KeyError(path)
        self.set_channel_route(f"loop:{key}", bus)

    def set_channel_gain(self, channel_id: str, gain: float) -> None:
        with self._lock:
            self._channels[channel_id]["gain"] = min(1.0, max(0.0, float(gain)))
            self._crossfades.pop(channel_id, None)
        self._channels[channel_id]["control"].volumeChanged.emit(float(gain))

    def channel_gain(self, channel_id: str) -> float:
        return float(self._channels[channel_id]["gain"])

    def set_channel_muted(self, channel_id: str, muted: bool) -> None:
        with self._lock:
            self._channels[channel_id]["muted"] = bool(muted)

    def channel_muted(self, channel_id: str) -> bool:
        return bool(self._channels[channel_id]["muted"])

    def set_bus_gain(self, bus: str, gain: float) -> None:
        with self._lock:
            self._bus_gains[bus] = min(1.0, max(0.0, float(gain)))

    def bus_gain(self, bus: str) -> float:
        return float(self._bus_gains[bus])

    def set_bus_muted(self, bus: str, muted: bool) -> None:
        with self._lock:
            self._bus_mutes[bus] = bool(muted)

    def bus_muted(self, bus: str) -> bool:
        return bool(self._bus_mutes[bus])

    def set_master_gain(self, gain: float) -> None:
        with self._lock:
            self._master_gain = min(1.0, max(0.0, float(gain)))

    @property
    def master_gain(self) -> float:
        return float(self._master_gain)

    def active_sources(self, bus: str) -> list[str]:
        bus = bus.upper()
        with self._lock:
            return [
                source_id for source_id, channel in self._channels.items()
                if self._routes.get(source_id) == bus
                and channel["player"].playbackState() == QMediaPlayer.PlaybackState.PlayingState
            ]

    def crossfade(self, source_a: str, source_b: str, duration_ms: int) -> None:
        if self._routes.get(source_a) != self._routes.get(source_b):
            raise ValueError("As fontes do crossfade devem estar no mesmo barramento.")
        frames = max(1, int(duration_ms * SAMPLE_RATE / 1000))
        with self._lock:
            self._crossfades[source_a] = {"position": 0, "total": frames, "direction": "out"}
            self._crossfades[source_b] = {"position": 0, "total": frames, "direction": "in"}

    @staticmethod
    def available_outputs():
        return QMediaDevices.audioOutputs()

    def _cart_error(self, slot: int, message: str) -> None:
        self._cart_active.discard(slot)
        self._end_generations[f"cart:{slot}"] += 1
        self._crossfades.pop(f"cart:{slot}", None)
        player, _output = self._cart_players[slot]
        details = message or player.errorString() or "Falha ao abrir o áudio."
        player.stop()
        player.setSource(QUrl())
        self._channels[f"cart:{slot}"]["queue"].clear()
        self.cart_error.emit(slot, details)

    def _cart_media_status(self, slot: int, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            player, _output = self._cart_players[slot]
            def complete() -> None:
                self._cart_active.discard(slot)
                self.cart_finished.emit(slot)
            self._finish_after_output_drain(f"cart:{slot}", player, complete)

    def play_cartouche(self, slot: int, path: str) -> bool:
        slot = int(slot)
        if slot not in self._cart_players:
            raise ValueError("O número do cartucho deve estar entre 1 e 12.")
        if slot in self._cart_active:
            return False
        player, _output = self._cart_players[slot]
        if player.playbackState() != QMediaPlayer.PlaybackState.StoppedState or player.source().isValid():
            return False
        if not Path(path).is_file():
            self.cart_error.emit(slot, "Arquivo de áudio não encontrado.")
            return False
        self._cart_active.add(slot)
        self._end_generations[f"cart:{slot}"] += 1
        self._crossfades.pop(f"cart:{slot}", None)
        self._channels[f"cart:{slot}"]["queue"].clear()
        self._load(player, path)
        return True

    def pause_cartouche(self, slot: int) -> None:
        pair = self._cart_players.get(int(slot))
        if pair and int(slot) in self._cart_active:
            pair[0].pause()

    def resume_cartouche(self, slot: int) -> None:
        pair = self._cart_players.get(int(slot))
        if pair and int(slot) in self._cart_active and pair[0].playbackState() == QMediaPlayer.PlaybackState.PausedState:
            pair[0].play()

    def stop_cartouche(self, slot: int) -> None:
        slot = int(slot)
        pair = self._cart_players.get(slot)
        if not pair:
            return
        player, _output = pair
        was_active = slot in self._cart_active
        self._cart_active.discard(slot)
        if was_active or player.source().isValid():
            self._end_generations[f"cart:{slot}"] += 1
            self._crossfades.pop(f"cart:{slot}", None)
            player.stop()
            player.setSource(QUrl())
            self._channels[f"cart:{slot}"]["queue"].clear()

    def stop_all_cartouches(self) -> None:
        for slot in tuple(self._cart_players):
            self.stop_cartouche(slot)

    def shutdown(self) -> None:
        self.stop_program()
        self.program.setSource(QUrl())
        self.stop_preview()
        self.stop_loop()
        self.stop_all_cartouches()
        for player, _output in self._cart_players.values():
            player.setSource(QUrl())
        for bus in self._buses.values():
            bus.close()

    def cartouche_active(self, slot: int) -> bool:
        return int(slot) in self._cart_active

    def cartouche_paused(self, slot: int) -> bool:
        pair = self._cart_players.get(int(slot))
        return bool(pair and pair[0].playbackState() == QMediaPlayer.PlaybackState.PausedState)

    def _error(self, player: QMediaPlayer, message: str) -> None:
        if player is self.program:
            self.cancel_program_fade()
            signal = self.program_error
        elif player is self.preview:
            signal = self.preview_error
        else:
            signal = self.loop_error
        signal.emit(message or player.errorString() or "Falha ao abrir o áudio.")

    def _program_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.cancel_program_fade()
            self._finish_after_output_drain("program", self.program, self.program_finished.emit)

    def _preview_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._finish_after_output_drain("preview", self.preview, self.preview_finished.emit)

    @staticmethod
    def _load(player: QMediaPlayer, path: str, autoplay: bool = True) -> None:
        player.stop()
        player.setSource(QUrl.fromLocalFile(path))
        if autoplay:
            player.play()

    def play_program(self, path: str) -> None:
        self.cancel_program_fade()
        self._end_generations["program"] += 1
        self._crossfades.pop("program", None)
        self._channels["program"]["queue"].clear()
        self._load(self.program, path)

    def toggle_program(self) -> None:
        if self.program.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.program.pause()
        elif self.program.source().isValid():
            self.program.play()

    def pause_program(self) -> None:
        self.program.pause()

    def resume_program(self) -> None:
        if self.program.source().isValid():
            self.program.play()

    def stop_program(self) -> None:
        self.cancel_program_fade()
        self._end_generations["program"] += 1
        self._crossfades.pop("program", None)
        self.program.stop()
        self._channels["program"]["queue"].clear()

    def eject_program(self) -> None:
        self.cancel_program_fade()
        self._end_generations["program"] += 1
        self._crossfades.pop("program", None)
        self.program.stop()
        self.program.setSource(QUrl())
        self._channels["program"]["queue"].clear()

    @property
    def program_fade_active(self) -> bool:
        return self._program_fade_timer.isActive()

    def fade_out_program(self, duration_ms: int) -> None:
        self.cancel_program_fade()
        duration_ms = max(0, int(duration_ms))
        if duration_ms == 0:
            position = self.program.position()
            self.program.stop()
            self._end_generations["program"] += 1
            self._crossfades.pop("program", None)
            self._channels["program"]["queue"].clear()
            self.program_faded_out.emit(position)
            return
        self._program_fade_duration_ms = duration_ms
        self._program_fade_started = time.monotonic()
        self._program_fade_start_volume = self.program_output.volume()
        self._program_fade_timer.start()

    def _advance_program_fade(self) -> None:
        elapsed_ms = (time.monotonic() - self._program_fade_started) * 1000
        ratio = min(1.0, elapsed_ms / self._program_fade_duration_ms)
        self.program_output.setVolume(self._program_fade_start_volume * (1.0 - ratio))
        if ratio < 1.0:
            return
        position = self.program.position()
        self._program_fade_timer.stop()
        self.program.stop()
        self._end_generations["program"] += 1
        self._crossfades.pop("program", None)
        self._channels["program"]["queue"].clear()
        self.program_output.setVolume(self._program_fade_start_volume)
        self.program_faded_out.emit(position)

    def cancel_program_fade(self) -> None:
        if self._program_fade_timer.isActive():
            self._program_fade_timer.stop()
            self.program_output.setVolume(self._program_fade_start_volume)

    def set_muted(self, muted: bool) -> None:
        self.program_output.setMuted(muted)

    def preview_path(self, path: str) -> None:
        if not self.cue_available:
            self.preview_error.emit("Pré-escuta indisponível: configure uma saída CUE diferente da saída PGM.")
            return
        self._channels["preview"]["queue"].clear()
        self._end_generations["preview"] += 1
        self._crossfades.pop("preview", None)
        self._load(self.preview, path)

    def stop_preview(self) -> None:
        self._end_generations["preview"] += 1
        self._crossfades.pop("preview", None)
        self.preview.stop()
        self.preview.setSource(QUrl())
        self._channels["preview"]["queue"].clear()

    @staticmethod
    def _loop_key(path: str) -> str:
        return str(QUrl.fromLocalFile(path).toLocalFile()).casefold()

    def start_loop(self, path: str, bus: str = "PGM") -> None:
        key = self._loop_key(path)
        if key in self._loop_players:
            return
        if bus.upper() == "CUE" and not self.cue_available:
            raise RuntimeError("CUE indisponível: configure uma saída física diferente da saída PGM.")
        player = QMediaPlayer(self)
        channel_id = f"loop:{key}"
        control = self._create_player_channel(channel_id, player, bus.upper())
        player.errorOccurred.connect(lambda _error, text, p=player: self._error(p, text))
        player.playbackStateChanged.connect(lambda state: self.loop_state.emit(state))
        player.positionChanged.connect(lambda p, media=player: self.loop_position.emit(p, media.duration()))
        player.setLoops(QMediaPlayer.Loops.Infinite)
        player.setSource(QUrl.fromLocalFile(path))
        self._loop_players[key] = (player, control)
        player.play()

    def stop_loop(self, path: str | None = None) -> None:
        keys = list(self._loop_players) if path is None else [self._loop_key(path)]
        for key in keys:
            pair = self._loop_players.pop(key, None)
            if pair:
                player, _control = pair
                channel_id = f"loop:{key}"
                player.stop()
                player.setSource(QUrl())
                with self._lock:
                    self._channels.pop(channel_id, None)
                    self._routes.pop(channel_id, None)
                    self._crossfades.pop(channel_id, None)
                player.deleteLater()

    def loop_active(self, path: str) -> bool:
        return self._loop_key(path) in self._loop_players

    @staticmethod
    def format_ms(value: int) -> str:
        total = max(0, value) // 1000
        return f"{total // 60:02}:{total % 60:02}"
