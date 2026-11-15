from __future__ import annotations

import random
import sys
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QHeaderView
)

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"}


class PlaylistTable(QTableWidget):
    """Playlist that accepts files/folders and supports internal drag reordering."""

    def __init__(self, parent=None):
        super().__init__(0, 3, parent)
        self.setHorizontalHeaderLabels(["#", "Arquivo", "Duração"])
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setSelectionMode(QTableWidget.ExtendedSelection)
        self.setDragDropMode(QTableWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)
        self.setAlternatingRowColors(True)
        self.paths: list[Path] = []
        self.on_play_now = None
        self.on_play_next = None

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.source() is self:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls() or event.source() is self:
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.source() is self:
            super().dropEvent(event)
            self.sync_paths_from_rows()
            self.refresh_numbers()
            return
        added = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                added.extend(self.expand_path(Path(url.toLocalFile())))
        self.add_paths(added)
        event.acceptProposedAction()

    @staticmethod
    def expand_path(path: Path) -> list[Path]:
        if path.is_file():
            return [path] if path.suffix.lower() in AUDIO_EXTENSIONS else []
        if path.is_dir():
            files = [p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS]
            random.shuffle(files)
            return files
        return []

    def add_paths(self, paths: list[Path]):
        for path in paths:
            if not path.exists() or path.suffix.lower() not in AUDIO_EXTENSIONS:
                continue
            row = self.rowCount()
            self.insertRow(row)
            self.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            name_item = QTableWidgetItem(path.name)
            name_item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.setItem(row, 1, name_item)
            self.setItem(row, 2, QTableWidgetItem("—"))
            self.paths.append(path)

    def sync_paths_from_rows(self):
        # Qt's internal row move changes the visible rows; recover paths using hidden UserRole data.
        ordered = []
        for row in range(self.rowCount()):
            item = self.item(row, 1)
            path = item.data(Qt.ItemDataRole.UserRole) if item else None
            if path:
                ordered.append(Path(path))
        if len(ordered) == self.rowCount():
            self.paths = ordered

    def refresh_numbers(self):
        for row in range(self.rowCount()):
            self.setItem(row, 0, QTableWidgetItem(str(row + 1)))

    def show_context_menu(self, position):
        row = self.rowAt(position.y())
        if row >= 0 and not self.selectionModel().isRowSelected(row, self.rootIndex()):
            self.selectRow(row)
        menu = QMenu(self)
        play = menu.addAction("▶  Reproduzir agora")
        next_action = menu.addAction("⏭  Reproduzir a seguir")
        menu.addSeparator()
        remove = menu.addAction("Remover da playlist")
        menu.addAction("Abrir pasta de origem").triggered.connect(self.open_source_folder)
        chosen = menu.exec(self.viewport().mapToGlobal(position))
        if chosen == play and self.on_play_now:
            self.on_play_now(row)
        elif chosen == next_action and self.on_play_next:
            self.on_play_next(row)
        elif chosen == remove:
            self.remove_selected()

    def open_source_folder(self):
        rows = self.selectedItems()
        if not rows:
            return
        row = rows[0].row()
        if row < len(self.paths):
            import os
            os.startfile(str(self.paths[row].parent))

    def remove_selected(self):
        rows = sorted({index.row() for index in self.selectedIndexes()}, reverse=True)
        for row in rows:
            self.removeRow(row)
            if row < len(self.paths):
                self.paths.pop(row)
        self.refresh_numbers()


class RadioAutomation(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HitMo Radio Automation")
        self.resize(1180, 720)
        self.setMinimumSize(850, 520)
        self.queue: list[Path] = []
        self.current_index = -1

        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.mediaStatusChanged.connect(self.on_media_status)
        self.player.errorOccurred.connect(self.on_audio_error)

        root = QWidget()
        layout = QVBoxLayout(root)
        header = QHBoxLayout()
        brand = QLabel("HITMO  |  RADIO AUTOMATION")
        brand.setObjectName("brand")
        header.addWidget(brand)
        header.addStretch()
        self.status = QLabel("Pronto para programação")
        self.status.setObjectName("status")
        header.addWidget(self.status)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.playlist = PlaylistTable()
        self.playlist.on_play_now = self.play_row
        self.playlist.on_play_next = self.queue_next
        splitter.addWidget(self.playlist)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.addWidget(QLabel("CONTROLES"))
        for label, callback in [
            ("＋  Adicionar arquivos", self.add_files),
            ("▣  Adicionar pasta (aleatório)", self.add_folder),
            ("▶  PLAY", self.play),
            ("Ⅱ  PAUSE", self.player.pause),
            ("■  STOP", self.player.stop),
            ("⏭  PRÓXIMA", self.next_track),
            ("⌫  REMOVER", self.playlist.remove_selected),
        ]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            side_layout.addWidget(button)
        side_layout.addStretch()
        splitter.addWidget(side)
        splitter.setSizes([850, 260])
        layout.addWidget(splitter)
        self.setCentralWidget(root)

        delete_action = QAction(self)
        delete_action.setShortcut(QKeySequence.StandardKey.Delete)
        delete_action.triggered.connect(self.playlist.remove_selected)
        self.addAction(delete_action)

        self.setStyleSheet("""
            QMainWindow, QWidget { background: #17191f; color: #e9edf5; font-family: Segoe UI; font-size: 10pt; }
            QLabel#brand { color: #00d2ff; font-size: 16pt; font-weight: 700; padding: 8px; }
            QLabel#status { color: #aab4c5; padding: 8px; }
            QTableWidget { background: #20242d; alternate-background-color: #252a35; gridline-color: #343b49; border: 1px solid #343b49; }
            QHeaderView::section { background: #292f3b; color: #00d2ff; padding: 8px; border: 0; }
            QPushButton { background: #292f3b; border: 1px solid #414b5d; border-radius: 5px; padding: 10px; text-align: left; }
            QPushButton:hover { background: #354154; border-color: #00d2ff; }
        """)

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Adicionar áudio", "", "Áudio (*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.wma)")
        self.playlist.add_paths([Path(f) for f in files])

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Adicionar pasta")
        if folder:
            self.playlist.add_paths(self.playlist.expand_path(Path(folder)))

    def play_row(self, row: int):
        if 0 <= row < len(self.playlist.paths):
            self.current_index = row
            self.play_path(self.playlist.paths[row])

    def play_path(self, path: Path):
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()
        self.status.setText(f"Tocando: {path.name}")

    def play(self):
        if self.player.source().isValid():
            self.player.play()
        elif self.playlist.paths:
            self.play_row(0)

    def next_track(self):
        if not self.playlist.paths:
            return
        self.current_index = (self.current_index + 1) % len(self.playlist.paths)
        self.play_path(self.playlist.paths[self.current_index])

    def queue_next(self, row: int):
        if 0 <= row < len(self.playlist.paths):
            path = self.playlist.paths.pop(row)
            insert_at = max(0, self.current_index + 1)
            self.playlist.paths.insert(insert_at, path)
            self.playlist.removeRow(row)
            self.playlist.insertRow(insert_at)
            self.playlist.setItem(insert_at, 0, QTableWidgetItem(str(insert_at + 1)))
            name_item = QTableWidgetItem(path.name)
            name_item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.playlist.setItem(insert_at, 1, name_item)
            self.playlist.setItem(insert_at, 2, QTableWidgetItem("—"))
            self.playlist.refresh_numbers()

    def on_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.next_track()

    def on_audio_error(self, error, message):
        if error != QMediaPlayer.Error.NoError:
            self.status.setText("Erro ao reproduzir áudio")
            QMessageBox.warning(self, "Falha na reprodução", message or "Não foi possível reproduzir este arquivo.")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("HitMo Radio Automation")
    window = RadioAutomation()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
