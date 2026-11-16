from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer


class AudioEngine(QObject):
    """UI-independent playback service with program, preview and loop decks."""

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

    def __init__(self, output_id: str = ""):
        super().__init__()
        self.program_output = QAudioOutput(self)
        self.preview_output = QAudioOutput(self)
        self.program = QMediaPlayer(self)
        self.preview = QMediaPlayer(self)
        self._loop_players: dict[str, tuple[QMediaPlayer, QAudioOutput]] = {}
        self._program_fade_started = 0.0
        self._program_fade_duration_ms = 0
        self._program_fade_start_volume = 1.0
        self._program_fade_timer = QTimer(self)
        self._program_fade_timer.setInterval(15)
        self._program_fade_timer.timeout.connect(self._advance_program_fade)
        for player, output in ((self.program, self.program_output), (self.preview, self.preview_output)):
            player.setAudioOutput(output)
            player.errorOccurred.connect(lambda _error, text, p=player: self._error(p, text))
        self.program.playbackStateChanged.connect(lambda state: self.program_state.emit(state))
        self.preview.playbackStateChanged.connect(lambda state: self.preview_state.emit(state))
        self.program.positionChanged.connect(lambda p: self.program_position.emit(p, self.program.duration()))
        self.preview.positionChanged.connect(lambda p: self.preview_position.emit(p, self.preview.duration()))
        self.program.mediaStatusChanged.connect(self._program_media_status)
        self.preview.mediaStatusChanged.connect(self._preview_media_status)
        self.set_output(output_id)

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
            self.program_finished.emit()
            self.program.setSource(QUrl())

    def _preview_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.preview_finished.emit()
            self.preview.setSource(QUrl())

    @staticmethod
    def _load(player: QMediaPlayer, path: str, autoplay: bool = True) -> None:
        player.stop()
        player.setSource(QUrl.fromLocalFile(path))
        if autoplay:
            player.play()

    def play_program(self, path: str) -> None:
        self.cancel_program_fade()
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
        self.program.stop()

    def eject_program(self) -> None:
        self.cancel_program_fade()
        self.program.stop()
        self.program.setSource(QUrl())

    @property
    def program_fade_active(self) -> bool:
        return self._program_fade_timer.isActive()

    def fade_out_program(self, duration_ms: int) -> None:
        """Reduce PGM output, then stop it; zero milliseconds is an immediate cut."""
        self.cancel_program_fade()
        duration_ms = max(0, int(duration_ms))
        if duration_ms == 0:
            position = self.program.position()
            self.program.stop()
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
        self.program_output.setVolume(self._program_fade_start_volume)
        self.program_faded_out.emit(position)

    def cancel_program_fade(self) -> None:
        if self._program_fade_timer.isActive():
            self._program_fade_timer.stop()
            self.program_output.setVolume(self._program_fade_start_volume)

    def set_muted(self, muted: bool) -> None:
        self.program_output.setMuted(muted)

    def preview_path(self, path: str) -> None:
        self._load(self.preview, path)

    def stop_preview(self) -> None:
        self.preview.stop()
        self.preview.setSource(QUrl())

    @staticmethod
    def _loop_key(path: str) -> str:
        return str(QUrl.fromLocalFile(path).toLocalFile()).casefold()

    def start_loop(self, path: str) -> None:
        key = self._loop_key(path)
        if key in self._loop_players:
            return
        output = QAudioOutput(self)
        device = self.program_output.device()
        if not device.isNull():
            output.setDevice(device)
        player = QMediaPlayer(self)
        player.setAudioOutput(output)
        player.errorOccurred.connect(lambda _error, text, p=player: self._error(p, text))
        player.playbackStateChanged.connect(lambda state: self.loop_state.emit(state))
        player.positionChanged.connect(lambda p, media=player: self.loop_position.emit(p, media.duration()))
        player.setLoops(QMediaPlayer.Loops.Infinite)
        player.setSource(QUrl.fromLocalFile(path))
        self._loop_players[key] = (player, output)
        player.play()

    def stop_loop(self, path: str | None = None) -> None:
        keys = list(self._loop_players) if path is None else [self._loop_key(path)]
        for key in keys:
            pair = self._loop_players.pop(key, None)
            if pair:
                player, output = pair
                player.stop()
                player.setSource(QUrl())
                player.deleteLater()
                output.deleteLater()

    def loop_active(self, path: str) -> bool:
        return self._loop_key(path) in self._loop_players

    @staticmethod
    def available_outputs():
        return QMediaDevices.audioOutputs()

    def set_output(self, device_id: str) -> None:
        devices = QMediaDevices.audioOutputs()
        device = next((d for d in devices if bytes(d.id()).decode(errors="ignore") == device_id), None) if device_id else None
        chosen = device or QMediaDevices.defaultAudioOutput()
        if chosen.isNull() and devices:
            chosen = devices[0]
        if chosen.isNull():
            return
        outputs = [self.program_output, self.preview_output, *(output for _player, output in self._loop_players.values())]
        for output in outputs:
            output.setDevice(chosen)

    @staticmethod
    def format_ms(value: int) -> str:
        total = max(0, value) // 1000
        return f"{total // 60:02}:{total % 60:02}"

