from __future__ import annotations

import csv
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFontDatabase, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDockWidget, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QProgressBar, QPushButton, QStyle, QTableWidget, QTableWidgetItem,
    QTimeEdit, QVBoxLayout, QWidget,
)

from .audio_engine import AudioEngine
from .app_settings import AppSettingsDialog
from .config_manager import ConfigManagerDialog
from .database import Database
from .operator_dialog import OperatorDialog
from .programming_dialog import ProgramManagerDialog
from .playlist_compat import PlaylistConfigImporter
from .services import MediaLibrary, PlaylistService, ProgrammingService, TimelineService

ROLE = Qt.ItemDataRole.UserRole


class PlaylistWidget(QListWidget):
    def __init__(self, dropped, parent=None):
        super().__init__(parent)
        self.dropped = dropped
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
            self.dropped(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class MediaListWidget(QListWidget):
    def startDrag(self, supported_actions):
        item = self.currentItem()
        if not item:
            return
        path = str(item.data(ROLE + 1) or "")
        if not path:
            return
        from PySide6.QtGui import QDrag
        from PySide6.QtCore import QMimeData
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path)])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class TimelineEventDialog(QDialog):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Agendar evento")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        self.title = QLineEdit()
        self.category = QComboBox()
        self.category.addItems(["Comercial", "Música", "Vinheta", "Locução"])
        self.media = QComboBox()
        self.media.addItem("Escolha o áudio…", None)
        for row in db.all("SELECT m.id,m.title,m.artist,m.category FROM media m LEFT JOIN material_categories c ON c.name=m.category WHERE COALESCE(c.material_kind,'audio')='audio' ORDER BY m.title COLLATE NOCASE"):
            label = f"{row['title']} · {row['artist']}" if row["artist"] else row["title"]
            self.media.addItem(label, int(row["id"]))
        self.days = []
        day_row = QHBoxLayout()
        for index, day in enumerate(("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")):
            box = QCheckBox(day)
            box.setChecked(True)
            self.days.append(box)
            day_row.addWidget(box)
        form.addRow("Horário", self.time)
        form.addRow("Identificação", self.title)
        form.addRow("Tipo de evento", self.category)
        form.addRow("Áudio", self.media)
        layout.addLayout(form)
        layout.addLayout(day_row)
        layout.addWidget(QLabel("O evento interrompe a faixa em andamento no horário programado."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return (
            self.time.time().toString("HH:mm"),
            [i for i, box in enumerate(self.days) if box.isChecked()],
            self.title.text().strip() or self.category.currentText(),
            self.media.currentData(),
            self.category.currentText(),
        )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = Database()
        self.library = MediaLibrary(self.db)
        self.playlist = PlaylistService(self.db)
        self.timeline = TimelineService(self.db)
        self.programming = ProgrammingService(self.db)
        self.engine = AudioEngine(self.db.setting("audio_output_id"))
        self._current = None
        self._history_id = None
        self._current_queue_id = None
        self._current_occurrence_item_id = None
        self._current_source_type = "manual"
        self._current_source_ref = None
        self._current_planned_for = ""
        self._muted = False
        self.db.execute("UPDATE playback_history SET status='interrupted',ended_at=CURRENT_TIMESTAMP WHERE status IN ('loading','playing','paused')")
        self.playlist.recover_interrupted()
        self.programming.recover_interrupted()
        self.setWindowTitle("HitMo Radio Studio")
        self.resize(1440, 900)
        self._build_ui()
        self._wire_audio()
        self._refresh_operators()
        self._refresh_playlist()
        self._refresh_library()
        self._refresh_timeline()
        self._refresh_program_blocks()
        self._refresh_history()
        self._load_library_folders()
        self._restore_loop_tracks()
        self._refresh_library()
        self._apply_theme()
        self._restore_window()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(500)
        if self.db.setting("startup_play", "0") == "1":
            QTimer.singleShot(600, self.play_next)

    def _build_ui(self):
        toolbar = self.addToolBar("Operação")
        toolbar.setMovable(False)
        toolbar.addWidget(QLabel("  HITMO  "))
        self.operator_label = QLabel("Operador identificado: não selecionado")
        toolbar.addWidget(self.operator_label)
        self.operator_combo = QComboBox()
        self.operator_combo.setMinimumWidth(180)
        self.operator_combo.currentTextChanged.connect(self._operator_changed)
        toolbar.addWidget(self.operator_combo)
        manage_ops = QPushButton("Operadores")
        manage_ops.clicked.connect(self._manage_operators)
        toolbar.addWidget(manage_ops)
        toolbar.addSeparator()
        materials = QPushButton("Config Manager · Materiais")
        materials.clicked.connect(self._open_config)
        toolbar.addWidget(materials)
        settings = QPushButton("Preferências")
        settings.clicked.connect(self._open_settings)
        toolbar.addWidget(settings)
        toolbar.addSeparator()
        self.clock_label = QLabel("--:--:--")
        self.clock_label.setObjectName("clockLabel")
        toolbar.addWidget(self.clock_label)

        center = QWidget()
        root = QVBoxLayout(center)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(12)
        self.station_label = QLabel(self.db.setting("station_name", "HitMo Radio"))
        self.station_label.setObjectName("stationName")
        root.addWidget(self.station_label)
        self.now_title = QLabel("Pronto para o ar")
        self.now_title.setObjectName("nowTitle")
        self.now_title.setWordWrap(True)
        self.now_artist = QLabel("Adicione faixas à playlist ou escolha um item na biblioteca")
        self.now_artist.setObjectName("mutedText")
        root.addWidget(self.now_title)
        root.addWidget(self.now_artist)
        self.progress = QProgressBar()
        self.progress.setObjectName("progressSpacer")
        self.progress.setFixedHeight(4)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)
        root.addWidget(self.progress)
        self.position_label = QLabel("00:00 / 00:00")
        self.position_label.setObjectName("mutedText")
        root.addWidget(self.position_label)

        controls = QHBoxLayout()
        style = self.style()
        self.play_button = QPushButton("PLAY")
        self.play_button.setObjectName("primaryButton")
        self.play_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_button.setToolTip("Iniciar ou retomar a programação")
        self.play_button.clicked.connect(self._start_or_resume)
        self.pause_button = QPushButton("PAUSA")
        self.pause_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        self.pause_button.setToolTip("Pausar mantendo a posição e a programação")
        self.pause_button.clicked.connect(self._pause)
        self.next_button = QPushButton("PRÓXIMO")
        self.next_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaSkipForward))
        self.next_button.setToolTip("Encerrar o item atual como pulado e iniciar o próximo")
        self.next_button.clicked.connect(self.play_next)
        self.stop_button = QPushButton("PARAR")
        self.stop_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_button.setToolTip("Parar o item atual; manter o item manual programado para retomada")
        self.stop_button.clicked.connect(self._stop)
        self.mute_button = QPushButton("Silenciar")
        self.mute_button.setCheckable(True)
        self.mute_button.toggled.connect(self._toggle_mute)
        self.eject_button = QPushButton("EXPULSAR")
        self.eject_button.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DriveCDIcon))
        self.eject_button.setToolTip("Descarregar o material atual sem avançar a programação")
        self.eject_button.clicked.connect(self._eject)
        for button in (self.play_button, self.pause_button, self.next_button, self.stop_button, self.eject_button, self.mute_button):
            button.setMinimumHeight(40)
            controls.addWidget(button)
        root.addLayout(controls)

        root.addWidget(QLabel("Programação manual pendente"))
        self.queue = PlaylistWidget(self._drop_to_playlist)
        self.queue.setObjectName("playlist")
        self.queue.setAlternatingRowColors(True)
        self.queue.itemDoubleClicked.connect(self._play_queue_item)
        self.queue.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.queue.customContextMenuRequested.connect(self._playlist_menu)
        self.queue.model().rowsMoved.connect(self._persist_queue_order)
        self.queue.setMinimumHeight(240)
        root.addWidget(self.queue, 1)
        queue_actions = QHBoxLayout()
        clear_button = QPushButton("Limpar playlist")
        clear_button.clicked.connect(self._clear_playlist)
        remove_button = QPushButton("Remover selecionada")
        remove_button.clicked.connect(self._remove_selected_queue)
        stop_loops = QPushButton("Parar trilhas")
        stop_loops.clicked.connect(self._stop_loops)
        self.auto_badge = QLabel("AUTO · ON" if self.db.setting("auto_advance", "1") == "1" else "AUTO · OFF")
        self.auto_badge.setObjectName("autoBadge")
        queue_actions.addWidget(clear_button)
        queue_actions.addWidget(remove_button)
        queue_actions.addWidget(stop_loops)
        queue_actions.addStretch(1)
        queue_actions.addWidget(self.auto_badge)
        root.addLayout(queue_actions)
        self.setCentralWidget(center)

        self.library_list = MediaListWidget()
        self.library_list.setDragEnabled(True)
        self.library_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.library_list.customContextMenuRequested.connect(self._library_menu)
        self.library_list.itemDoubleClicked.connect(lambda _: self._play_library_selected())
        lib = QWidget()
        ll = QVBoxLayout(lib)
        ll.setContentsMargins(8, 8, 8, 8)
        self.library_search = QLineEdit()
        self.library_search.setPlaceholderText("Buscar por título, artista ou arquivo…")
        self.library_search.textChanged.connect(self._refresh_library)
        self.category_filter = QComboBox()
        self.category_filter.addItem("Todos")
        self._refresh_category_filter()
        self.category_filter.currentTextChanged.connect(self._refresh_library)
        ll.addWidget(self.library_search)
        ll.addWidget(self.category_filter)
        ll.addWidget(self.library_list, 1)
        lib_buttons = QVBoxLayout()
        add_selected = QPushButton("Adicionar à playlist")
        add_selected.clicked.connect(self._add_library_selected)
        import_folder = QPushButton("Importar pasta")
        import_folder.clicked.connect(self._import_folder)
        lib_buttons.addWidget(add_selected)
        lib_buttons.addWidget(import_folder)
        ll.addLayout(lib_buttons)
        stop_preview = QPushButton("Parar pré-escuta")
        stop_preview.clicked.connect(self.engine.stop_preview)
        ll.addWidget(stop_preview)
        self.library_dock = QDockWidget("Biblioteca de mídia", self)
        self.library_dock.setObjectName("libraryDock")
        self.library_dock.setWidget(lib)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.library_dock)

        self.timeline_table = QTableWidget(0, 5)
        self.timeline_table.setHorizontalHeaderLabels(["Ativo", "Hora", "Evento", "Tipo", "Áudio"])
        self.timeline_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.timeline_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.timeline_table.horizontalHeader().setStretchLastSection(True)
        self.timeline_table.itemChanged.connect(self._timeline_toggled)
        timeline_panel = QWidget()
        tl = QVBoxLayout(timeline_panel)
        tl.setContentsMargins(8, 8, 8, 8)
        tl.addWidget(self.timeline_table)
        tl_buttons = QHBoxLayout()
        add_event = QPushButton("Novo evento")
        add_event.clicked.connect(self._add_timeline_event)
        remove_event = QPushButton("Remover evento")
        remove_event.clicked.connect(self._remove_timeline_event)
        tl_buttons.addWidget(add_event)
        tl_buttons.addWidget(remove_event)
        tl_buttons.addStretch(1)
        tl.addLayout(tl_buttons)
        self.timeline_dock = QDockWidget("Timeline musical e comercial", self)
        self.timeline_dock.setObjectName("timelineDock")
        self.timeline_dock.setWidget(timeline_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.timeline_dock)

        self.program_table = QTableWidget(0, 6)
        self.program_table.setHorizontalHeaderLabels(["Ativo", "Hora", "Bloco", "Tipo", "Dias", "Itens"])
        self.program_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.program_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.program_table.horizontalHeader().setStretchLastSection(True)
        program_panel = QWidget()
        pl = QVBoxLayout(program_panel)
        pl.setContentsMargins(8, 8, 8, 8)
        pl.addWidget(self.program_table)
        manage_program = QPushButton("Gerenciar blocos programados…")
        manage_program.clicked.connect(self._open_program_manager)
        pl.addWidget(manage_program)
        self.program_dock = QDockWidget("Grade · blocos recorrentes", self)
        self.program_dock.setObjectName("programDock")
        self.program_dock.setWidget(program_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.program_dock)

        self.history_table = QTableWidget(0, 7)
        self.history_table.setHorizontalHeaderLabels(["Início", "Fim", "Material", "Categoria", "Operador", "Situação", "Programado para"])
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.horizontalHeader().setStretchLastSection(True)
        history_panel = QWidget()
        hl = QVBoxLayout(history_panel)
        hl.setContentsMargins(8, 8, 8, 8)
        hl.addWidget(self.history_table)
        export = QPushButton("Exportar CSV…")
        export.clicked.connect(self._export_history)
        hl.addWidget(export)
        self.history_dock = QDockWidget("Histórico de execução", self)
        self.history_dock.setObjectName("historyDock")
        self.history_dock.setWidget(history_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.history_dock)
        self.tabifyDockWidget(self.timeline_dock, self.history_dock)
        self.tabifyDockWidget(self.program_dock, self.timeline_dock)
        self.program_dock.raise_()

        self._build_menus()

        self.statusBar().showMessage("Automação pronta · programação persistente")
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self._start_or_resume)
        QShortcut(QKeySequence("Ctrl+Right"), self, activated=self.play_next)
        QShortcut(QKeySequence("Ctrl+M"), self, activated=lambda: self.mute_button.toggle())

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("Arquivo")
        import_action = file_menu.addAction("Importar mídia…")
        import_action.triggered.connect(self._import_folder)
        file_menu.addSeparator()
        exit_action = file_menu.addAction("Sair")
        exit_action.triggered.connect(self.close)

        operation_menu = self.menuBar().addMenu("Operação")
        for label, slot in (("PLAY · Iniciar/retomar", self._start_or_resume), ("PAUSA", self._pause), ("PRÓXIMO", self.play_next), ("PARAR", self._stop), ("EXPULSAR", self._eject)):
            action = operation_menu.addAction(label)
            action.triggered.connect(slot)

        programming_menu = self.menuBar().addMenu("Programação")
        edit_program = programming_menu.addAction("Gerenciar blocos musicais/comerciais…")
        edit_program.triggered.connect(self._open_program_manager)
        add_event = programming_menu.addAction("Novo evento de horário…")
        add_event.triggered.connect(self._add_timeline_event)

        tools_menu = self.menuBar().addMenu("Cadastro")
        manager = tools_menu.addAction("Config Manager · materiais, pastas e categorias…")
        manager.triggered.connect(self._open_config)
        playlist_catalog = tools_menu.addAction("Ler catálogo do Playlist (Folders.xml)…")
        playlist_catalog.triggered.connect(self._import_playlist_catalog)
        operators = tools_menu.addAction("Operadores…")
        operators.triggered.connect(self._manage_operators)
        settings_menu = self.menuBar().addMenu("Preferências")
        audio_settings = settings_menu.addAction("Aplicativo e dispositivo de áudio…")
        audio_settings.triggered.connect(self._open_settings)

        view_menu = self.menuBar().addMenu("Exibir")
        for dock in (self.library_dock, self.program_dock, self.timeline_dock, self.history_dock):
            view_menu.addAction(dock.toggleViewAction())

    def _wire_audio(self):
        self.engine.program_state.connect(self._playback_state)
        self.engine.program_position.connect(self._position_changed)
        self.engine.program_finished.connect(self._playback_finished)
        self.engine.program_error.connect(self._audio_error)
        self.engine.loop_error.connect(lambda message: self.statusBar().showMessage(f"Erro na trilha: {message}", 8000))
        self.engine.preview_error.connect(lambda message: self.statusBar().showMessage(f"Erro na pré-escuta: {message}", 8000))

    def _apply_theme(self):
        # The offscreen Qt plugin may not enumerate Windows' installed fonts.
        # Load Segoe UI from the OS font directory when it is available; it is
        # never copied into the project or redistributed with the application.
        windows_dir = Path(os.environ.get("WINDIR", "C:/Windows"))
        system_font = windows_dir / "Fonts" / "segoeui.ttf"
        if system_font.is_file():
            QFontDatabase.addApplicationFont(str(system_font))
        self.setStyleSheet("""
            QMainWindow, QWidget { background:#111317; color:#e7e9ee; font-family:'Segoe UI'; font-size:10pt; }
            QMenuBar, QToolBar, QStatusBar { background:#171a20; border:0; }
            QToolBar { spacing:8px; padding:8px 12px; }
            QDockWidget { font-weight:600; }
            QDockWidget::title { background:#1c2028; padding:9px; border-bottom:1px solid #2a2f39; }
            QLineEdit, QComboBox, QTimeEdit { background:#191d24; border:1px solid #303642; border-radius:6px; padding:8px; selection-background-color:#6d5dfc; }
            QListWidget, QTableWidget { background:#15181e; alternate-background-color:#191d24; border:1px solid #292e38; border-radius:8px; gridline-color:#242a33; }
            QListWidget::item, QTableWidget::item { padding:7px; border-bottom:1px solid #222731; }
            QListWidget::item:selected, QTableWidget::item:selected { background:#302c53; color:white; }
            QHeaderView::section { background:#1b1f27; color:#9da5b3; padding:8px; border:0; border-bottom:1px solid #303642; }
            QPushButton { background:#242933; border:1px solid #353c49; border-radius:7px; padding:8px 12px; }
            QPushButton:hover { background:#303746; }
            QPushButton:checked { background:#4d45b6; border-color:#6d5dfc; }
            QPushButton#primaryButton { background:#6d5dfc; color:white; border:0; font-weight:700; }
            QPushButton#primaryButton:hover { background:#7e70ff; }
            QLabel#stationName { color:#8993a4; font-size:10pt; font-weight:600; }
            QLabel#nowTitle { font-size:25pt; font-weight:700; color:#fff; }
            QLabel#mutedText { color:#8993a4; }
            QLabel#clockLabel { color:#81d9af; font-size:14pt; font-weight:700; padding:0 12px; }
            QLabel#autoBadge { background:#203d31; color:#85e1b4; border-radius:10px; padding:6px 10px; font-weight:700; }
            QProgressBar { background:#20242c; border:0; border-radius:2px; }
            QProgressBar::chunk { background:#6d5dfc; border-radius:2px; }
            QScrollBar:vertical { background:#15181e; width:10px; } QScrollBar::handle:vertical { background:#343b48; border-radius:5px; min-height:25px; }
        """)

    def _restore_window(self):
        try:
            state = self.db.setting("window_state")
            geometry = self.db.setting("window_geometry")
            if geometry:
                self.restoreGeometry(bytes.fromhex(geometry))
            if state:
                self.restoreState(bytes.fromhex(state))
        except (ValueError, TypeError):
            pass

    def _refresh_operators(self):
        current = self.operator_combo.currentData()
        self.operator_combo.blockSignals(True)
        self.operator_combo.clear()
        for row in self.db.all("SELECT id,name FROM operators WHERE active=1 ORDER BY name COLLATE NOCASE"):
            self.operator_combo.addItem(row["name"], int(row["id"]))
        index = self.operator_combo.findData(current)
        self.operator_combo.setCurrentIndex(index if index >= 0 else 0)
        self.operator_combo.blockSignals(False)
        self._operator_changed(self.operator_combo.currentText())

    def _operator_changed(self, name: str):
        if hasattr(self, "operator_label"):
            self.operator_label.setText(f"Operador identificado: {name or 'não selecionado'}")

    def _manage_operators(self):
        OperatorDialog(self.db, self).exec()
        self._refresh_operators()

    def _open_config(self):
        ConfigManagerDialog(self.db, self).exec()
        self._refresh_category_filter()
        self._refresh_library()
        self._refresh_playlist()

    def _import_playlist_catalog(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar configuração de pastas do Playlist Digital",
            str(Path.home()),
            "Configuração XML (Folders.xml);;XML (*.xml)",
        )
        if not path:
            return
        try:
            result = PlaylistConfigImporter(self.db).import_file(path)
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Catálogo Playlist", str(exc))
            return
        missing = self.db.all(
            "SELECT playlist_id,title,target FROM playlist_folder_entries WHERE catalog_id=? AND target_exists=0 ORDER BY position",
            (result.snapshot_id,),
        )
        missing_text = ""
        if missing:
            missing_text = "\n\nPastas ausentes neste computador:\n" + "\n".join(
                f"• {row['title']}: {row['target'] or '(caminho vazio)'}" for row in missing[:12]
            )
            if len(missing) > 12:
                missing_text += f"\n… e mais {len(missing) - 12}"
        QMessageBox.information(
            self,
            "Catálogo Playlist importado",
            f"Snapshot somente de leitura salvo no SQLite.\n"
            f"Pastas catalogadas: {result.folder_count}\n"
            f"Diretórios encontrados: {result.existing_target_count}\n"
            f"Diretórios ausentes: {result.missing_target_count}\n\n"
            "Nenhuma mídia foi varrida e o arquivo de origem não foi alterado."
            f"{missing_text}",
        )

    def _open_settings(self):
        if AppSettingsDialog(self.db, self.engine, self).exec():
            self.station_label.setText(self.db.setting("station_name", "HitMo Radio"))
            self.auto_badge.setText("AUTO · ON" if self.db.setting("auto_advance", "1") == "1" else "AUTO · OFF")

    def _open_program_manager(self):
        ProgramManagerDialog(self.db, self).exec()
        self._refresh_program_blocks()

    def _refresh_category_filter(self):
        if not hasattr(self, "category_filter"):
            return
        current = self.category_filter.currentText()
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("Todos")
        self.category_filter.addItems([row["name"] for row in self.library.categories()])
        index = self.category_filter.findText(current)
        self.category_filter.setCurrentIndex(max(0, index))
        self.category_filter.blockSignals(False)

    def _refresh_program_blocks(self):
        if not hasattr(self, "program_table"):
            return
        self.program_table.setRowCount(0)
        for block in self.programming.blocks():
            row = self.program_table.rowCount()
            self.program_table.insertRow(row)
            n = self.db.one("SELECT COUNT(*) AS n FROM program_block_items WHERE block_id=?", (int(block["id"]),))["n"]
            labels = ("Ativo" if block["enabled"] else "Pausado", block["time_of_day"], block["name"], block["block_type"], block["days"], str(n))
            for col, label in enumerate(labels):
                cell = QTableWidgetItem(str(label))
                cell.setData(ROLE, int(block["id"]))
                self.program_table.setItem(row, col, cell)
        self.program_table.resizeColumnsToContents()

    def _restore_loop_tracks(self):
        for row in self.db.all("SELECT m.path,m.title FROM loop_tracks l JOIN media m ON m.id=l.media_id WHERE l.enabled=1"):
            if Path(row["path"]).is_file():
                self.engine.start_loop(str(row["path"]))
            else:
                self.statusBar().showMessage(f"Trilha salva indisponível: {row['title']}", 8000)

    def _refresh_playlist(self):
        self.queue.blockSignals(True)
        self.queue.clear()
        for row in self.playlist.items():
            title = f"{row['title']}  ·  {row['artist']}" if row["artist"] else row["title"]
            item = QListWidgetItem(f"{int(row['position']) + 1:02}   {title}   ·   {self._duration(row['duration'])}")
            item.setData(ROLE, int(row["item_id"]))
            item.setData(ROLE + 1, str(row["path"]))
            item.setData(ROLE + 2, int(row["id"]))
            item.setData(ROLE + 3, str(row["category"]))
            if row["category"] == "Comercial":
                item.setForeground(QColor("#f4c36a"))
            self.queue.addItem(item)
        self.queue.blockSignals(False)

    def _persist_queue_order(self, *args):
        ids = [int(self.queue.item(i).data(ROLE)) for i in range(self.queue.count())]
        self.playlist.reorder(ids)
        for i in range(self.queue.count()):
            item = self.queue.item(i)
            item.setText(f"{i + 1:02}   {self._display_queue_item(item)}")

    def _display_queue_item(self, item):
        row = self.db.one("SELECT title,artist,duration FROM media WHERE id=?", (int(item.data(ROLE + 2)),))
        if not row:
            return "Mídia indisponível"
        title = f"{row['title']}  ·  {row['artist']}" if row["artist"] else row["title"]
        return f"{title}   ·   {self._duration(row['duration'])}"

    def _refresh_library(self, *args):
        if not hasattr(self, "library_list"):
            return
        self.library_list.clear()
        category = self.category_filter.currentText() if hasattr(self, "category_filter") else "Todos"
        text = self.library_search.text() if hasattr(self, "library_search") else ""
        for row in self.library.search(text, category):
            title = f"{row['title']}  ·  {row['artist']}" if row["artist"] else row["title"]
            item = QListWidgetItem(f"{title}   ·   {row['category']}   ·   {self._duration(row['duration'])}")
            item.setData(ROLE, int(row["id"]))
            item.setData(ROLE + 1, str(row["path"]))
            item.setData(ROLE + 2, str(row["category"]))
            if row["icon_path"] and Path(row["icon_path"]).is_file():
                item.setIcon(QIcon(row["icon_path"]))
            self.library_list.addItem(item)

    def _add_media_row(self, row):
        if row and self._is_audio_material(row["category"]):
            self.playlist.append(int(row["id"]))
            self._refresh_playlist()
        elif row:
            QMessageBox.information(self, "Material cadastrado", "Este material não é classificado como áudio e não pode entrar na fila executável.")

    def _is_audio_material(self, category: str) -> bool:
        kind = self.db.one("SELECT material_kind FROM material_categories WHERE name=?", (category,))
        return not kind or kind["material_kind"] == "audio"

    def _add_library_selected(self):
        item = self.library_list.currentItem()
        if not item:
            return
        self._add_media_row(self.db.one("SELECT * FROM media WHERE id=?", (int(item.data(ROLE)),)))

    def _play_library_selected(self):
        item = self.library_list.currentItem()
        if not item:
            return
        row = self.db.one("SELECT * FROM media WHERE id=?", (int(item.data(ROLE)),))
        if row:
            self._play_media(row)

    def _drop_to_playlist(self, paths):
        try:
            added, _total = self.library.import_paths(paths)
            media_by_path = set()
            for raw in paths:
                path = Path(raw)
                candidates = path.rglob("*") if path.is_dir() else (path,)
                media_by_path.update(str(p.resolve()).casefold() for p in candidates if p.is_file() and p.suffix.casefold() in {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus"})
            rows = self.db.all("SELECT id,path FROM media")
            for row in rows:
                if str(row["path"]).casefold() in media_by_path:
                    self.playlist.append(int(row["id"]))
            self._refresh_playlist()
            self._refresh_library()
            self.statusBar().showMessage(f"{added} mídia(s) importada(s) e adicionada(s) à playlist", 5000)
        except Exception as exc:
            QMessageBox.warning(self, "Importação", str(exc))

    def _playlist_menu(self, point):
        item = self.queue.itemAt(point)
        if not item:
            return
        menu = QMenu(self)
        play = menu.addAction("Tocar agora")
        remove = menu.addAction("Remover da playlist")
        action = menu.exec(self.queue.mapToGlobal(point))
        if action == play:
            row = self.playlist.claim(int(item.data(ROLE)))
            if row:
                self._refresh_playlist()
                self._play_media(row, source_type="manual_queue", source_ref=int(item.data(ROLE)), queue_id=int(item.data(ROLE)))
        elif action == remove:
            self.playlist.remove(int(item.data(ROLE)))
            self._refresh_playlist()

    def _play_queue_item(self, item):
        if not item:
            return
        row = self.playlist.claim(int(item.data(ROLE)))
        if row:
            self._refresh_playlist()
            self._play_media(row, source_type="manual_queue", source_ref=int(item.data(ROLE)), queue_id=int(item.data(ROLE)))

    def _remove_selected_queue(self):
        selected = self.queue.selectedItems()
        for item in selected:
            self.playlist.remove(int(item.data(ROLE)))
        self._refresh_playlist()

    def _clear_playlist(self):
        if not self.queue.count():
            return
        self.db.execute("DELETE FROM playlist_items WHERE status='planned'")
        self._refresh_playlist()

    def _library_menu(self, point):
        item = self.library_list.itemAt(point)
        if not item:
            return
        menu = QMenu(self)
        media_id = int(item.data(ROLE))
        row = self.db.one("SELECT * FROM media WHERE id=?", (media_id,))
        if not row:
            return
        if not self._is_audio_material(row["category"]):
            open_file = menu.addAction("Abrir arquivo cadastrado")
            action = menu.exec(self.library_list.mapToGlobal(point))
            if action == open_file:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(row["path"])))
            return
        add = menu.addAction("Adicionar à programação manual")
        preview = menu.addAction("Pré-escuta")
        play = menu.addAction("Intervenção · tocar no programa")
        loop = menu.addAction("Parar esta trilha" if self.engine.loop_active(str(row["path"])) else "Tocar como trilha em loop")
        show_file = menu.addAction("Abrir localização do arquivo")
        action = menu.exec(self.library_list.mapToGlobal(point))
        if action == add:
            self._add_media_row(row)
        elif action == preview:
            self.engine.preview_path(row["path"])
            self.statusBar().showMessage(f"Pré-escuta: {row['title']}", 5000)
        elif action == play:
            self._play_media(row, source_type="operator_override", source_ref=media_id)
        elif action == loop:
            if self.engine.loop_active(row["path"]):
                self.engine.stop_loop(row["path"])
                self.db.execute("UPDATE loop_tracks SET enabled=0 WHERE media_id=?", (media_id,))
                self.statusBar().showMessage(f"Trilha interrompida: {row['title']}", 5000)
            else:
                self.engine.start_loop(row["path"])
                self.db.execute("INSERT INTO loop_tracks(media_id,enabled) VALUES(?,1) ON CONFLICT(media_id) DO UPDATE SET enabled=1", (media_id,))
                self.statusBar().showMessage(f"Trilha em loop: {row['title']}", 5000)
        elif action == show_file:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(row["path"]).parent)))

    def _toggle_play(self):
        self._start_or_resume()

    def _start_or_resume(self):
        from PySide6.QtMultimedia import QMediaPlayer
        state = self.engine.program.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            return
        if state == QMediaPlayer.PlaybackState.PausedState:
            self.engine.resume_program()
            return
        if self._current and self.engine.program.source().isValid():
            self.engine.resume_program()
            return
        self._play_next_pending()

    def _pause(self):
        from PySide6.QtMultimedia import QMediaPlayer
        if self.engine.program.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.engine.pause_program()

    def _play_media(self, row, *, source_type="operator_override", source_ref=None, queue_id=None, occurrence_item_id=None, scheduled_for=""):
        # Database queries return sqlite3.Row, which is indexable but does not
        # implement dict.get(); normalize once for all playback sources.
        row = dict(row)
        if not self._is_audio_material(row["category"]):
            message = "Material não classificado como áudio."
            self.statusBar().showMessage(message, 8000)
            if queue_id:
                self.playlist.set_status(queue_id, "failed")
            if occurrence_item_id:
                self.programming.set_occurrence_item_status(occurrence_item_id, "failed", message)
            self.db.execute(
                "INSERT INTO playback_history(media_id,title,artist,path,category,operator,started_at,ended_at,status,planned_for,source_type,source_ref,error_message) VALUES(?,?,?,?,?,?, '',CURRENT_TIMESTAMP,'failed',?,?,?,?)",
                (row.get("id", row.get("media_id")), row.get("title", ""), row.get("artist", ""), row.get("path", ""), row.get("category", ""), self.operator_combo.currentText(), scheduled_for, source_type, source_ref, message),
            )
            self._refresh_history()
            return False
        if not Path(row["path"]).is_file():
            operator = self.operator_combo.currentText()
            self.db.execute(
                "INSERT INTO playback_history(media_id,title,artist,path,category,operator,started_at,ended_at,status,planned_for,source_type,source_ref,error_message) VALUES(?,?,?,?,?,?, '',CURRENT_TIMESTAMP,'failed',?,?,?,?)",
                (row.get("id", row.get("media_id")), row.get("title", "Material ausente"), row.get("artist", ""), row.get("path", ""), row.get("category", ""), operator, scheduled_for, source_type, source_ref, "Arquivo não encontrado"),
            )
            if queue_id:
                self.playlist.set_status(queue_id, "failed")
            if occurrence_item_id:
                self.programming.set_occurrence_item_status(occurrence_item_id, "failed", "Arquivo não encontrado")
            QMessageBox.warning(self, "Arquivo ausente", f"O arquivo não existe mais:\n{row['path']}")
            self._refresh_history()
            return False
        if self._current:
            previous_queue_id = self._current_queue_id
            self._end_current("interrupted")
            # A live operator override interrupts current playback but leaves
            # the queued item available for the automation to resume later.
            if source_type == "operator_override" and previous_queue_id:
                self.playlist.set_status(previous_queue_id, "planned")
        self._current = dict(row)
        self._history_id = None
        self._current_queue_id = queue_id
        self._current_occurrence_item_id = occurrence_item_id
        self._current_source_type = source_type
        self._current_source_ref = source_ref
        self._current_planned_for = scheduled_for
        self.now_title.setText(row["title"])
        self.now_artist.setText(f"{row['artist']}   ·   {row['category']}" if row["artist"] else str(row["category"]))
        self.position_label.setText(f"00:00 / {self._duration(row['duration'])}")
        self._history_id = self.db.execute(
            "INSERT INTO playback_history(media_id,title,artist,path,category,operator,started_at,status,planned_for,source_type,source_ref) VALUES(?,?,?,?,?,?, '', 'loading',?,?,?)",
            (row.get("id", row.get("media_id")), row.get("title", ""), row.get("artist", ""), row.get("path", ""), row.get("category", "Música"), self.operator_combo.currentText(), scheduled_for, source_type, source_ref),
        )
        if queue_id:
            self.playlist.set_status(queue_id, "loading")
        if occurrence_item_id:
            self.programming.set_occurrence_item_status(occurrence_item_id, "loading")
        self.engine.play_program(str(row["path"]))
        self.statusBar().showMessage(f"Preparando áudio: {row['title']}")
        self._refresh_history()
        return True

    def play_next(self):
        if self._current:
            self._end_current("skipped")
            self.engine.eject_program()
            self._clear_current()
        self._play_next_pending()

    def _play_next_pending(self):
        scheduled = self.programming.next_occurrence_item()
        if scheduled:
            row = dict(scheduled)
            row["id"] = row.get("media_id")
            source = "scheduled_block" if self.db.one("SELECT origin_type FROM program_occurrences WHERE id=?", (int(scheduled["occurrence_id"]),))["origin_type"] == "block" else "scheduled_event"
            if not self._play_media(row, source_type=source, source_ref=int(scheduled["id"]), occurrence_item_id=int(scheduled["id"]), scheduled_for=scheduled["scheduled_for"]):
                QTimer.singleShot(0, self._play_next_pending)
            return
        item = self.playlist.pop_next()
        self._refresh_playlist()
        if item:
            queue_id = int(item["item_id"])
            if not self._play_media(item, source_type="manual_queue", source_ref=queue_id, queue_id=queue_id):
                QTimer.singleShot(0, self._play_next_pending)
            return
        self.statusBar().showMessage("Sem itens programados pendentes", 4000)

    def _clear_current(self):
        self._current = None
        self._current_queue_id = None
        self._current_occurrence_item_id = None
        self._current_source_ref = None
        self._current_planned_for = ""

    def _end_current(self, status: str, error_message: str = ""):
        self._finish_history(status, error_message)
        if self._current_queue_id:
            self.playlist.set_status(self._current_queue_id, status)
            self._refresh_playlist()
        if self._current_occurrence_item_id:
            self.programming.set_occurrence_item_status(self._current_occurrence_item_id, status, error_message)

    def _stop(self):
        if self._current:
            # Stop ends this attempt but keeps a manual queue entry available for restart.
            self._finish_history("stopped")
            if self._current_queue_id:
                self.playlist.set_status(self._current_queue_id, "planned")
            if self._current_occurrence_item_id:
                self.programming.set_occurrence_item_status(self._current_occurrence_item_id, "stopped")
            self._clear_current()
            self._refresh_playlist()
        self.engine.stop_program()
        self.statusBar().showMessage("Programa parado")

    def _eject(self):
        if self._current:
            self._finish_history("ejected")
            # Eject unloads the active source but preserves its planned occurrence.
            if self._current_queue_id:
                self.playlist.set_status(self._current_queue_id, "planned")
            if self._current_occurrence_item_id:
                self.programming.set_occurrence_item_status(self._current_occurrence_item_id, "planned")
            self._clear_current()
            self._refresh_playlist()
        self.engine.eject_program()
        self.now_title.setText("Pronto para o ar")
        self.now_artist.setText("Programação preservada · pronto para executar")
        self.position_label.setText("00:00 / 00:00")
        self.statusBar().showMessage("Fonte descarregada · nenhuma faixa foi avançada")

    def _toggle_mute(self, muted):
        self._muted = muted
        self.engine.set_muted(muted)
        self.mute_button.setText("Ativar som" if muted else "Silenciar")

    def _stop_loops(self):
        self.engine.stop_loop()
        self.db.execute("UPDATE loop_tracks SET enabled=0")
        self.statusBar().showMessage("Todas as trilhas em loop foram interrompidas", 5000)

    def _playback_state(self, state):
        from PySide6.QtMultimedia import QMediaPlayer
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._start_history()
            if self._current_queue_id:
                self.playlist.set_status(self._current_queue_id, "playing")
            if self._current_occurrence_item_id:
                self.programming.set_occurrence_item_status(self._current_occurrence_item_id, "playing")
        elif state == QMediaPlayer.PlaybackState.PausedState:
            if self._history_id:
                self.db.execute("UPDATE playback_history SET status='paused' WHERE id=?", (self._history_id,))
            if self._current_queue_id:
                self.playlist.set_status(self._current_queue_id, "paused")
            if self._current_occurrence_item_id:
                self.programming.set_occurrence_item_status(self._current_occurrence_item_id, "paused")
            self.statusBar().showMessage("Programa pausado")

    def _start_history(self):
        if not self._current:
            return
        if self._history_id:
            self.db.execute("UPDATE playback_history SET started_at=CASE WHEN started_at='' THEN CURRENT_TIMESTAMP ELSE started_at END,status='playing' WHERE id=?", (self._history_id,))
        else:
            self._history_id = self.db.execute(
                "INSERT INTO playback_history(media_id,title,artist,path,category,operator,status,planned_for,source_type,source_ref) VALUES(?,?,?,?,?,?,'playing',?,?,?)",
                (self._current.get("id", self._current.get("media_id")), self._current.get("title", ""), self._current.get("artist", ""), self._current.get("path", ""), self._current.get("category", "Música"), self.operator_combo.currentText(), self._current_planned_for, self._current_source_type, self._current_source_ref),
            )
        self._refresh_history()

    def _finish_history(self, status: str = "completed", error_message: str = ""):
        if self._history_id:
            seconds = max(0.0, float(self.engine.program.position()) / 1000.0)
            self.db.execute("UPDATE playback_history SET ended_at=CURRENT_TIMESTAMP,played_seconds=?,status=?,error_message=? WHERE id=?", (seconds, status, error_message, self._history_id))
            self._history_id = None
            self._refresh_history()

    def _playback_finished(self):
        if self._current:
            self._end_current("completed")
        self._clear_current()
        self.now_title.setText("Faixa concluída")
        self.now_artist.setText("Pronto para a próxima inserção")
        if self.programming.next_occurrence_item() or self.db.setting("auto_advance", "1") == "1":
            QTimer.singleShot(0, self._play_scheduled_or_next)

    def _play_scheduled_or_next(self):
        self._play_next_pending()

    def _audio_error(self, message):
        if self._current:
            self._end_current("failed", message)
        self._clear_current()
        self.statusBar().showMessage(f"Falha ao reproduzir áudio: {message}", 10000)
        if self.programming.next_occurrence_item() or self.db.setting("auto_advance", "1") == "1":
            QTimer.singleShot(0, self._play_scheduled_or_next)

    def _position_changed(self, position, duration):
        self.position_label.setText(f"{self.engine.format_ms(position)} / {self.engine.format_ms(duration)}")
        self.progress.setValue(int(1000 * min(1, position / duration)) if duration else 0)

    def _duration(self, seconds):
        value = max(0, int(float(seconds or 0)))
        return f"{value // 60:02}:{value % 60:02}"

    def _import_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecionar pasta de áudio")
        if not folder:
            return
        try:
            added, total = self.library.import_paths([folder])
            folders = json.loads(self.db.setting("library_folders", "[]"))
            if folder not in folders:
                folders.append(folder)
            self.db.set_setting("library_folders", json.dumps(folders, ensure_ascii=False))
            self._refresh_library()
            self.statusBar().showMessage(f"{added} novo(s) arquivo(s) importado(s) · {total} na biblioteca", 6000)
        except Exception as exc:
            QMessageBox.warning(self, "Importação", str(exc))

    def _load_library_folders(self):
        try:
            folders = json.loads(self.db.setting("library_folders", "[]"))
            for folder in folders:
                if Path(folder).is_dir():
                    self.library.import_paths([folder])
            for folder in self.library.folders():
                if Path(folder["path"]).is_dir():
                    category = self.db.one("SELECT material_kind FROM material_categories WHERE id=?", (folder["category_id"],)) if folder["category_id"] else None
                    self.library.import_paths(
                        [folder["path"]],
                        category=folder["category"],
                        folder_id=int(folder["id"]),
                        include_non_audio=bool(category and category["material_kind"] in {"text", "file"}),
                    )
        except (ValueError, OSError):
            pass

    def _refresh_timeline(self):
        self.timeline_table.blockSignals(True)
        self.timeline_table.setRowCount(0)
        for row in self.timeline.events():
            index = self.timeline_table.rowCount()
            self.timeline_table.insertRow(index)
            values = ["Ativo" if row["enabled"] else "Pausado", row["time_of_day"], row["title"], row["category"], row["media_title"] or "Áudio ausente"]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if col == 0:
                    cell.setData(ROLE, int(row["id"]))
                    cell.setFlags(cell.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                    cell.setCheckState(Qt.CheckState.Checked if row["enabled"] else Qt.CheckState.Unchecked)
                self.timeline_table.setItem(index, col, cell)
        self.timeline_table.resizeColumnsToContents()
        self.timeline_table.blockSignals(False)

    def _timeline_toggled(self, item):
        if item.column() != 0:
            return
        event_id = int(item.data(ROLE))
        enabled = int(item.checkState() == Qt.CheckState.Checked)
        self.db.execute("UPDATE timeline_events SET enabled=? WHERE id=?", (enabled, event_id))
        self.timeline_table.blockSignals(True)
        item.setText("Ativo" if enabled else "Pausado")
        self.timeline_table.blockSignals(False)

    def _add_timeline_event(self):
        if not self.db.one("SELECT id FROM media LIMIT 1"):
            QMessageBox.information(self, "Timeline", "Importe arquivos de áudio para a biblioteca primeiro.")
            return
        dialog = TimelineEventDialog(self.db, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        time_of_day, days, title, media_id, category = dialog.values()
        try:
            if media_id is None:
                raise ValueError("Escolha um áudio para o evento.")
            self.timeline.add(time_of_day, days, title, media_id, category)
            self._refresh_timeline()
        except ValueError as exc:
            QMessageBox.warning(self, "Timeline", str(exc))

    def _remove_timeline_event(self):
        row = self.timeline_table.currentRow()
        if row < 0:
            return
        event_id = int(self.timeline_table.item(row, 0).data(ROLE))
        self.db.execute("DELETE FROM timeline_events WHERE id=?", (event_id,))
        self._refresh_timeline()

    def _tick(self):
        now = datetime.now()
        self.clock_label.setText(now.strftime("%H:%M:%S"))
        due = self.programming.due(now)
        if due:
            if self._current:
                queue_id = self._current_queue_id
                self._end_current("interrupted", "Interrompido pelo início de programação agendada")
                if queue_id:
                    self.playlist.set_status(queue_id, "planned")
                self.engine.stop_program()
                self._clear_current()
            self.statusBar().showMessage(f"Programação agendada · {len(due)} ocorrência(s) persistida(s)", 8000)
            self._play_next_pending()
            self._refresh_timeline()
            self._refresh_program_blocks()

    def _refresh_history(self):
        if not hasattr(self, "history_table"):
            return
        self.history_table.setRowCount(0)
        rows = self.db.all("SELECT started_at,ended_at,title,artist,path,category,operator,status,planned_for FROM playback_history ORDER BY id DESC LIMIT 250")
        for row in rows:
            index = self.history_table.rowCount()
            self.history_table.insertRow(index)
            title = f"{row['title']} · {row['artist']}" if row["artist"] else row["title"]
            if row["path"]:
                title = f"{title} · {Path(row['path']).name}"
            for col, value in enumerate((row["started_at"], row["ended_at"], title, row["category"], row["operator"], row["status"], row["planned_for"])):
                self.history_table.setItem(index, col, QTableWidgetItem(str(value)))

    def _export_history(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exportar histórico", "hitmo-historico.csv", "CSV (*.csv)")
        if not path:
            return
        rows = self.db.all("SELECT started_at,ended_at,title,artist,path,category,operator,played_seconds,status,planned_for,source_type,source_ref,error_message FROM playback_history ORDER BY id")
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["Início", "Fim", "Título", "Artista", "Arquivo", "Categoria", "Operador", "Segundos executados", "Situação", "Programado para", "Origem", "Referência da origem", "Erro"])
            writer.writerows([tuple(row) for row in rows])
        self.statusBar().showMessage(f"Histórico exportado: {path}", 7000)

    def closeEvent(self, event):
        if self._current:
            queue_id = self._current_queue_id
            self._end_current("interrupted", "Aplicativo encerrado durante a execução")
            if queue_id:
                self.playlist.set_status(queue_id, "planned")
            self._clear_current()
        self.engine.stop_program()
        self.engine.stop_preview()
        self.engine.stop_loop()
        self.db.set_setting("window_geometry", bytes(self.saveGeometry()).hex())
        self.db.set_setting("window_state", bytes(self.saveState()).hex())
        event.accept()

