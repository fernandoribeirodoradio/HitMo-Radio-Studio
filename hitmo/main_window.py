from __future__ import annotations

import csv
import ipaddress
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, datetime as _SystemDateTime
from pathlib import Path

from PySide6.QtCore import QDate, QProcess, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFontDatabase, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDateEdit, QDockWidget, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QInputDialog,
    QGroupBox, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QProgressBar, QPushButton, QSlider, QSpinBox, QStyle, QTableWidget, QTableWidgetItem,
    QTimeEdit, QVBoxLayout, QScrollArea, QTabWidget, QWidget,
)

from .audio_engine import AudioEngine
from .app_settings import AppSettingsDialog
from .config_manager import ConfigManagerDialog
from .database import Database
from .operator_dialog import OperatorDialog
from .programming_dialog import ProgramManagerDialog
from .playlist_compat import PlaylistConfigImporter
from .services import MediaLibrary, PlaybackHistoryService, PlaylistService, ProgrammingService, TimelineService

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
        self.history = PlaybackHistoryService(self.db)
        self._audio_mixer_settings_error = ""
        try:
            mixer_state = json.loads(self.db.setting("audio_mixer_state", "{}"))
            if not isinstance(mixer_state, dict):
                mixer_state = {}
                self._audio_mixer_settings_error = "As preferências de áudio salvas têm formato inválido; foram usados valores seguros."
        except (TypeError, json.JSONDecodeError):
            mixer_state = {}
            self._audio_mixer_settings_error = "As preferências de áudio salvas não puderam ser lidas; foram usados valores seguros."
        self.engine = AudioEngine(
            self.db.setting("audio_output_id"), self.db.setting("cue_output_id"),
            mixer_state=mixer_state,
        )
        self._current = None
        self._history_id = None
        self._history_offset = 0
        self._current_queue_id = None
        self._current_occurrence_item_id = None
        self._current_source_type = "manual"
        self._current_source_ref = None
        self._current_planned_for = ""
        self._muted = self.engine.channel_muted("program")
        self._fixed_transition_target = None
        self._fixed_transition_fade_ms = 1000
        self._fixed_fade_timer_target = None
        self._fixed_transition_due = False
        self._fixed_transition_fade_done = False
        self._fixed_transition_position_ms = None
        self._fixed_transition_late = False
        self._scheduler_first_tick = True
        self.db.execute("UPDATE playback_history SET status='interrupted',ended_at=CURRENT_TIMESTAMP WHERE status IN ('loading','playing','paused')")
        self.db.execute(
            "UPDATE cartouche_history SET status='interrupted',ended_at=CURRENT_TIMESTAMP,"
            "error_message='Aplicativo reiniciado durante a execução' WHERE status IN ('triggered','loading','playing','paused')"
        )
        self._cart_history_ids: dict[int, int] = {}
        self._cart_states = {slot: "Pronto" for slot in range(1, 13)}
        self.playlist.recover_interrupted()
        self.programming.recover_interrupted()
        self.setWindowTitle("HitMo Radio Studio")
        self.resize(1440, 900)
        self._build_ui()
        self._wire_audio()
        self._fixed_transition_timer = QTimer(self)
        self._fixed_transition_timer.setSingleShot(True)
        self._fixed_transition_timer.timeout.connect(self._begin_fixed_transition_fade)
        self._refresh_operators()
        self._refresh_playlist()
        self._refresh_library()
        self._refresh_timeline()
        self._refresh_program_blocks()
        self._refresh_history()
        self._refresh_cartouches()
        self._load_library_folders()
        self._restore_loop_tracks()
        self._refresh_library()
        self._apply_theme()
        self._restore_window()
        revision_row = self.db.one("SELECT revision FROM catalog_state WHERE id=1")
        self._observed_catalog_revision = int(revision_row["revision"]) if revision_row else 0
        self._catalog_poll = QTimer(self)
        self._catalog_poll.timeout.connect(self._poll_catalog_revision)
        self._catalog_poll.start(1500)
        self._api_process = QProcess(self)
        self._api_process.finished.connect(self._api_process_finished)
        self._api_process.errorOccurred.connect(self._api_process_error)
        self._api_host = self.db.setting("api_host", "127.0.0.1")
        try:
            configured_port = int(self.db.setting("api_port", "8765"))
        except ValueError:
            configured_port = 8765
        self._api_port = configured_port if 1 <= configured_port <= 65535 else 8765
        self._api_poll = QTimer(self)
        self._api_poll.timeout.connect(self._refresh_api_status)
        self._api_poll.start(2000)
        self._refresh_api_status()
        self._cue_notice_timer = QTimer(self)
        self._cue_notice_timer.setSingleShot(True)
        self._cue_notice_timer.timeout.connect(self._show_cue_configuration_notice)
        if self.engine.cue_configuration_error:
            self._cue_notice_timer.start(1000)
        elif not self.engine.cue_available:
            self._cue_notice_timer.start(1000)
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
        self.api_status_label = QLabel("API central · offline")
        toolbar.addWidget(self.api_status_label)
        api_menu = self.menuBar().addMenu("Serviço central")
        api_menu.addAction("Ativar serviço local…", self._start_api_service)
        api_menu.addAction("Parar serviço iniciado pelo Studio", self._stop_api_service)
        api_menu.addAction("Configurar endereço e porta…", self._configure_api_service)
        api_menu.addAction("Clientes autenticados recentes…", self._show_api_clients)
        api_menu.addAction("Abrir registro de erros", self._open_api_log)
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
        preview_controls = QHBoxLayout()
        pause_preview = QPushButton("Pausar pré-escuta")
        pause_preview.clicked.connect(self.engine.pause_preview)
        resume_preview = QPushButton("Retomar pré-escuta")
        resume_preview.clicked.connect(self.engine.resume_preview)
        stop_preview = QPushButton("Parar pré-escuta")
        stop_preview.clicked.connect(self.engine.stop_preview)
        preview_controls.addWidget(pause_preview)
        preview_controls.addWidget(resume_preview)
        preview_controls.addWidget(stop_preview)
        ll.addLayout(preview_controls)
        self.library_dock = QDockWidget("Biblioteca de mídia", self)
        self.library_dock.setObjectName("libraryDock")
        self.library_dock.setWidget(lib)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.library_dock)
        self.library_dock.setMinimumWidth(320)

        self.timeline_table = QTableWidget(0, 5)
        self.timeline_table.setHorizontalHeaderLabels(["Ativo", "Hora", "Evento", "Tipo", "Áudio"])
        self.timeline_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.timeline_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.timeline_table.horizontalHeader().setStretchLastSection(True)
        self.timeline_table.itemChanged.connect(self._timeline_toggled)

        # Visual 24-hour schedule: 12 five-minute cells per hour. Cells are
        # populated only from persisted timeline events; empty cells stay empty.
        self.timeline_grid = QTableWidget(24, 12)
        self.timeline_grid.setObjectName("timelineGrid")
        self.timeline_grid.setHorizontalHeaderLabels([f"{minute:02d}" for minute in range(0, 60, 5)])
        self.timeline_grid.setVerticalHeaderLabels([f"{hour:02d}:00" for hour in range(24)])
        self.timeline_grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.timeline_grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.timeline_grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.timeline_grid.horizontalHeader().setDefaultSectionSize(78)
        self.timeline_grid.verticalHeader().setDefaultSectionSize(38)
        self.timeline_grid.horizontalHeader().setStretchLastSection(True)
        self.timeline_grid.verticalHeader().setMinimumWidth(58)
        self.timeline_grid.cellDoubleClicked.connect(self._timeline_grid_activated)
        timeline_tabs = QTabWidget()
        timeline_tabs.addTab(self.timeline_grid, "GRADE 24 HORAS")
        timeline_tabs.addTab(self.timeline_table, "LISTA DE EVENTOS")
        timeline_panel = QWidget()
        tl = QVBoxLayout(timeline_panel)
        tl.setContentsMargins(8, 8, 8, 8)
        tl.addWidget(timeline_tabs, 1)
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
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.timeline_dock)
        self.timeline_dock.setMinimumWidth(360)

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
        self.program_dock.setMinimumHeight(190)

        history_filters = QHBoxLayout()
        self.history_period_enabled = QCheckBox("Período")
        self.history_from = QDateEdit(QDate.currentDate().addDays(-7))
        self.history_from.setCalendarPopup(True)
        self.history_to = QDateEdit(QDate.currentDate())
        self.history_to.setCalendarPopup(True)
        self.history_from.setEnabled(False)
        self.history_to.setEnabled(False)
        self.history_material = QLineEdit()
        self.history_material.setPlaceholderText("Material, artista, caminho ou ID")
        self.history_category = QComboBox()
        self.history_category.addItem("Todas as categorias", "")
        self.history_status = QComboBox()
        self.history_status.addItem("Todas as situações", "")
        self.history_apply = QPushButton("Consultar")
        history_filters.addWidget(self.history_period_enabled)
        history_filters.addWidget(self.history_from)
        history_filters.addWidget(QLabel("até"))
        history_filters.addWidget(self.history_to)
        history_filters.addWidget(self.history_material, 1)
        history_filters.addWidget(self.history_category)
        history_filters.addWidget(self.history_status)
        history_filters.addWidget(self.history_apply)
        self.history_count = QLabel()
        self.history_prev = QPushButton("‹ Anterior")
        self.history_next = QPushButton("Próxima ›")
        self.history_prev.clicked.connect(lambda: self._history_page(-1))
        self.history_next.clicked.connect(lambda: self._history_page(1))
        self.history_table = QTableWidget(0, 17)
        self.history_table.setHorizontalHeaderLabels([
            "Evento no software", "Registro", "Início observado", "Fim observado", "Material", "ID material", "Categoria",
            "Operador", "Situação do item", "Seleção", "Situação da ocorrência", "Programado para", "Selecionado em", "Bloco", "Ocorrência", "Segundos", "Motivo/erro",
        ])
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.horizontalHeader().setStretchLastSection(True)
        history_panel = QWidget()
        hl = QVBoxLayout(history_panel)
        hl.setContentsMargins(8, 8, 8, 8)
        hl.addLayout(history_filters)
        history_paging = QHBoxLayout()
        history_paging.addWidget(self.history_count, 1)
        history_paging.addWidget(self.history_prev)
        history_paging.addWidget(self.history_next)
        hl.addLayout(history_paging)
        hl.addWidget(QLabel("Registro de eventos observados pelo HitMo; não comprova o sinal efetivamente transmitido."))
        hl.addWidget(self.history_table)
        export = QPushButton("Exportar CSV…")
        export.clicked.connect(self._export_history)
        hl.addWidget(export)
        self.history_dock = QDockWidget("Eventos de reprodução · histórico", self)
        self.history_dock.setObjectName("historyDock")
        self.history_dock.setWidget(history_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.history_dock)
        cart_panel = QWidget()
        cart_layout = QVBoxLayout(cart_panel)
        cart_layout.setContentsMargins(8, 8, 8, 8)
        cart_layout.addWidget(QLabel("Cartuchos locais · destinos PGM/CUE · execução do software não comprova transmissão."))
        self.cart_grid = QGridLayout()
        self.cart_grid.setSpacing(8)
        self.cart_slots = {}
        self.cart_shortcuts = []
        for slot in range(1, 13):
            card = QWidget()
            card.setObjectName("cartoucheCard")
            card.setStyleSheet("QWidget#cartoucheCard { background:#191d24; border:1px solid #303642; border-radius:6px; }")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(8, 7, 8, 7)
            title = QLabel(f"Cartucho {slot}")
            title.setObjectName("cartoucheTitle")
            title.setWordWrap(True)
            category = QLabel("Vazio · F" + str(slot))
            category.setObjectName("mutedText")
            category.setWordWrap(True)
            state = QLabel("Vazio")
            play = QPushButton(f"PLAY · F{slot}")
            pause = QPushButton("PAUSE")
            stop = QPushButton("STOP")
            assign = QPushButton("Associar material…")
            remove = QPushButton("Remover associação")
            play.clicked.connect(lambda _checked=False, number=slot: self._fire_cartouche(number))
            pause.clicked.connect(lambda _checked=False, number=slot: self._pause_cartouche(number))
            stop.clicked.connect(lambda _checked=False, number=slot: self._stop_cartouche(number))
            assign.clicked.connect(lambda _checked=False, number=slot: self._assign_cartouche(number))
            remove.clicked.connect(lambda _checked=False, number=slot: self._remove_cartouche(number))
            card_layout.addWidget(title)
            card_layout.addWidget(category)
            card_layout.addWidget(state)
            controls = QHBoxLayout()
            controls.addWidget(play)
            controls.addWidget(pause)
            controls.addWidget(stop)
            card_layout.addLayout(controls)
            card_layout.addWidget(assign)
            card_layout.addWidget(remove)
            self.cart_grid.addWidget(card, (slot - 1) // 4, (slot - 1) % 4)
            self.cart_slots[slot] = {"title": title, "category": category, "state": state,
                                     "play": play, "pause": pause, "stop": stop,
                                     "assign": assign, "remove": remove}
            shortcut = QShortcut(QKeySequence(f"F{slot}"), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda number=slot: self._fire_cartouche(number))
            self.cart_shortcuts.append(shortcut)
        cart_layout.addLayout(self.cart_grid)
        stop_all_carts = QPushButton("PARAR TODOS OS CARTUCHOS")
        stop_all_carts.clicked.connect(self._stop_all_cartouches)
        cart_layout.addWidget(stop_all_carts)
        cart_layout.addStretch(1)
        self.cart_dock = QDockWidget("Cartucheira · 12 posições", self)
        self.cart_dock.setObjectName("cartoucheDock")
        self.cart_dock.setWidget(cart_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.cart_dock)
        self._build_audio_console()
        self.tabifyDockWidget(self.library_dock, self.history_dock)
        self.tabifyDockWidget(self.library_dock, self.cart_dock)
        self.library_dock.raise_()
        self.console_dock.raise_()

        self._build_menus()
        self.history_period_enabled.toggled.connect(self._toggle_history_period)
        self.history_apply.clicked.connect(self._apply_history_filters)
        self.history_material.returnPressed.connect(self._apply_history_filters)
        self._refresh_history_filter_options()

        self.statusBar().showMessage("Automação pronta · programação persistente")
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, activated=self._start_or_resume)
        QShortcut(QKeySequence("Ctrl+Right"), self, activated=self.play_next)
        QShortcut(QKeySequence("Ctrl+M"), self, activated=lambda: self.mute_button.toggle())

    def _build_audio_console(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)

        self.output_summary = QLabel()
        self.output_summary.setWordWrap(True)
        layout.addWidget(self.output_summary)
        self.program_audio_state = QLabel("PGM · parado")
        self.cue_audio_state = QLabel("CUE · parado")
        states = QHBoxLayout()
        states.addWidget(self.program_audio_state)
        states.addWidget(self.cue_audio_state)
        layout.addLayout(states)

        self.bus_widgets = {}
        bus_row = QHBoxLayout()
        for bus in ("PGM", "CUE"):
            group = QGroupBox(bus)
            group_layout = QVBoxLayout(group)
            meter = QProgressBar()
            meter.setRange(0, 600)
            meter.setTextVisible(False)
            meter.setFixedHeight(18)
            meter.setStyleSheet("QProgressBar::chunk { background:#63d6a5; }" if bus == "PGM" else "QProgressBar::chunk { background:#60a8ff; }")
            level = QLabel("Pico −60 dBFS · RMS −60 dBFS")
            gain = QSlider(Qt.Orientation.Horizontal)
            gain.setRange(0, 100)
            gain.setValue(round(self.engine.bus_gain(bus) * 100))
            mute = QCheckBox(f"Silenciar barramento {bus}")
            gain.valueChanged.connect(lambda value, target=bus: self.engine.set_bus_gain(target, value / 100))
            mute.toggled.connect(lambda checked, target=bus: self.engine.set_bus_muted(target, checked))
            group_layout.addWidget(meter)
            group_layout.addWidget(level)
            group_layout.addWidget(QLabel("Ganho do barramento"))
            group_layout.addWidget(gain)
            group_layout.addWidget(mute)
            bus_row.addWidget(group)
            self.bus_widgets[bus] = {"meter": meter, "level": level, "gain": gain, "mute": mute}
        layout.addLayout(bus_row)

        master_row = QHBoxLayout()
        master_row.addWidget(QLabel("Master"))
        self.master_gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.master_gain_slider.setRange(0, 100)
        self.master_gain_slider.setValue(round(self.engine.master_gain * 100))
        self.master_gain_value = QLabel(f"{self.engine.master_gain:.2f}")
        self.master_gain_slider.valueChanged.connect(self._master_gain_changed)
        master_row.addWidget(self.master_gain_slider, 1)
        master_row.addWidget(self.master_gain_value)
        layout.addLayout(master_row)

        source_row = QHBoxLayout()
        self.program_channel_gain = QSlider(Qt.Orientation.Horizontal)
        self.program_channel_gain.setRange(0, 100)
        self.program_channel_gain.setValue(round(self.engine.channel_gain("program") * 100))
        self.program_channel_gain.valueChanged.connect(lambda value: self.engine.set_channel_gain("program", value / 100))
        self.program_channel_mute = QCheckBox("Mute player PGM")
        self.program_channel_mute.toggled.connect(self._toggle_mute)
        self.preview_channel_gain = QSlider(Qt.Orientation.Horizontal)
        self.preview_channel_gain.setRange(0, 100)
        self.preview_channel_gain.setValue(round(self.engine.channel_gain("preview") * 100))
        self.preview_channel_gain.valueChanged.connect(lambda value: self.engine.set_channel_gain("preview", value / 100))
        self.preview_channel_mute = QCheckBox("Mute pré-escuta")
        self.preview_channel_mute.toggled.connect(lambda checked: self.engine.set_channel_muted("preview", checked))
        source_row.addWidget(QLabel("Canal player PGM"))
        source_row.addWidget(self.program_channel_gain, 1)
        source_row.addWidget(self.program_channel_mute)
        source_row.addWidget(QLabel("Canal pré-escuta CUE"))
        source_row.addWidget(self.preview_channel_gain, 1)
        source_row.addWidget(self.preview_channel_mute)
        layout.addLayout(source_row)

        layout.addWidget(QLabel("Cartuchos · destino, ganho e mute por fonte"))
        self.cart_mixer_table = QTableWidget(12, 5)
        self.cart_mixer_table.setHorizontalHeaderLabels(["Posição", "Material", "Destino", "Ganho", "Mute"])
        self.cart_mixer_table.horizontalHeader().setStretchLastSection(True)
        self.cart_mixer_table.setMinimumHeight(270)
        layout.addWidget(self.cart_mixer_table)

        layout.addWidget(QLabel("Trilhas em loop · destino salvo, ganho e mute"))
        self.loop_mixer_table = QTableWidget(0, 5)
        self.loop_mixer_table.setHorizontalHeaderLabels(["Trilha", "Destino", "Ganho", "Mute", "Controle"])
        self.loop_mixer_table.horizontalHeader().setStretchLastSection(True)
        self.loop_mixer_table.setMaximumHeight(190)
        layout.addWidget(self.loop_mixer_table)

        fade_row = QHBoxLayout()
        fade_row.addWidget(QLabel("Crossfade entre fontes ativas"))
        self.crossfade_bus = QComboBox()
        self.crossfade_bus.addItems(["PGM", "CUE"])
        self.crossfade_bus.currentTextChanged.connect(self._populate_crossfade_sources)
        self.crossfade_source_a = QComboBox()
        self.crossfade_source_b = QComboBox()
        self.crossfade_duration = QSpinBox()
        self.crossfade_duration.setRange(100, 15000)
        self.crossfade_duration.setValue(self.engine.crossfade_duration_ms)
        self.crossfade_duration.setSuffix(" ms")
        self.crossfade_duration.valueChanged.connect(self.engine.set_crossfade_duration)
        self.crossfade_button = QPushButton("Aplicar")
        self.crossfade_button.clicked.connect(self._apply_crossfade)
        fade_row.addWidget(self.crossfade_bus)
        fade_row.addWidget(self.crossfade_source_a, 1)
        fade_row.addWidget(self.crossfade_source_b, 1)
        fade_row.addWidget(self.crossfade_duration)
        fade_row.addWidget(self.crossfade_button)
        layout.addLayout(fade_row)
        layout.addWidget(QLabel("Medidores mostram amostras do mixer após ganho e limitador; não comprovam transmissão."))

        self.console_dock = QDockWidget("Console de áudio · PGM / CUE", self)
        self.console_dock.setObjectName("audioConsoleDock")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(panel)
        self.console_dock.setWidget(scroll)
        self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, self.console_dock)
        self.console_dock.setMinimumHeight(190)
        self.console_dock.setMaximumHeight(310)
        self._refresh_audio_console()

    def _master_gain_changed(self, value: int):
        self.engine.set_master_gain(value / 100)
        self.master_gain_value.setText(f"{value / 100:.2f}")

    def _route_combo(self, current: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem("PGM", "PGM")
        cue_label = "CUE · compartilhado" if self.engine._shared_output else ("CUE" if self.engine.cue_available else "CUE · sem saída")
        combo.addItem(cue_label, "CUE")
        if not self.engine.cue_available:
            combo.model().item(1).setEnabled(False)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        return combo

    def _refresh_audio_console(self):
        if not hasattr(self, "cart_mixer_table"):
            return
        self.master_gain_slider.blockSignals(True)
        self.master_gain_slider.setValue(round(self.engine.master_gain * 100))
        self.master_gain_slider.blockSignals(False)
        self.master_gain_value.setText(f"{self.engine.master_gain:.2f}")
        self.program_channel_gain.blockSignals(True)
        self.program_channel_gain.setValue(round(self.engine.channel_gain("program") * 100))
        self.program_channel_gain.blockSignals(False)
        self.preview_channel_gain.blockSignals(True)
        self.preview_channel_gain.setValue(round(self.engine.channel_gain("preview") * 100))
        self.preview_channel_gain.blockSignals(False)
        for bus, widgets in self.bus_widgets.items():
            widgets["gain"].blockSignals(True)
            widgets["gain"].setValue(round(self.engine.bus_gain(bus) * 100))
            widgets["gain"].blockSignals(False)
            widgets["mute"].blockSignals(True)
            widgets["mute"].setChecked(self.engine.bus_muted(bus))
            widgets["mute"].blockSignals(False)
        self.program_channel_mute.blockSignals(True)
        self.program_channel_mute.setChecked(self.engine.channel_muted("program"))
        self.program_channel_mute.blockSignals(False)
        self.mute_button.blockSignals(True)
        self.mute_button.setChecked(self.engine.channel_muted("program"))
        self.mute_button.setText("Ativar som" if self.engine.channel_muted("program") else "Silenciar")
        self.mute_button.blockSignals(False)
        self.preview_channel_mute.blockSignals(True)
        self.preview_channel_mute.setChecked(self.engine.channel_muted("preview"))
        self.preview_channel_mute.blockSignals(False)
        pgm_name = self._output_name(self.engine.pgm_device_id) or "sem saída"
        cue_name = (self._output_name(self.engine.cue_device_id) + " (compartilhada)") if self.engine.cue_available and self.engine._shared_output else (self._output_name(self.engine.cue_device_id) if self.engine.cue_available else "desativado")
        self.output_summary.setText(f"PGM → {pgm_name}    |    CUE → {cue_name}")
        self.program_audio_state.setText(f"PGM · {self._player_state_text(self.engine.program.playbackState())}")
        self.cue_audio_state.setText(f"CUE · {self._player_state_text(self.engine.preview.playbackState())}")

        self.cart_mixer_table.setRowCount(12)
        for slot in range(1, 13):
            row = self.db.one(
                "SELECT s.media_id,s.route,m.title FROM cartouche_slots s LEFT JOIN media m ON m.id=s.media_id WHERE s.slot=?",
                (slot,),
            )
            material = str(row["title"] or "Vazio") if row else "Vazio"
            route = str(row["route"] or "PGM") if row else "PGM"
            self.cart_mixer_table.setItem(slot - 1, 0, QTableWidgetItem(f"{slot:02}"))
            self.cart_mixer_table.setItem(slot - 1, 1, QTableWidgetItem(material))
            route_combo = self._route_combo(route)
            player = self.engine._cart_players[slot][0]
            route_combo.setEnabled(not self.engine.cartouche_active(slot) and not player.source().isValid())
            route_combo.currentIndexChanged.connect(lambda _index, number=slot, widget=route_combo: self._set_cart_mixer_route(number, str(widget.currentData())))
            self.cart_mixer_table.setCellWidget(slot - 1, 2, route_combo)
            gain = QSlider(Qt.Orientation.Horizontal)
            gain.setRange(0, 100)
            gain.setValue(round(self.engine.channel_gain(f"cart:{slot}") * 100))
            gain.valueChanged.connect(lambda value, number=slot: self.engine.set_channel_gain(f"cart:{number}", value / 100))
            self.cart_mixer_table.setCellWidget(slot - 1, 3, gain)
            mute = QCheckBox("Mute")
            mute.setChecked(self.engine.channel_muted(f"cart:{slot}"))
            mute.toggled.connect(lambda checked, number=slot: self.engine.set_channel_muted(f"cart:{number}", checked))
            self.cart_mixer_table.setCellWidget(slot - 1, 4, mute)

        loop_rows = self.db.all(
            "SELECT l.media_id,l.route,l.enabled,m.path,m.title FROM loop_tracks l JOIN media m ON m.id=l.media_id ORDER BY m.title COLLATE NOCASE"
        )
        self.loop_mixer_table.setRowCount(len(loop_rows))
        for index, row in enumerate(loop_rows):
            path = str(row["path"])
            source_id = f"loop:{self.engine._loop_key(path)}"
            active = self.engine.loop_active(path)
            self.loop_mixer_table.setItem(index, 0, QTableWidgetItem(str(row["title"])))
            route_combo = self._route_combo(str(row["route"] or "PGM"))
            route_combo.setEnabled(not active)
            route_combo.currentIndexChanged.connect(lambda _index, media=int(row["media_id"]), widget=route_combo: self._set_loop_mixer_route(media, str(widget.currentData())))
            self.loop_mixer_table.setCellWidget(index, 1, route_combo)
            gain = QSlider(Qt.Orientation.Horizontal)
            gain.setRange(0, 100)
            gain.setValue(round(self.engine.channel_gain(source_id) * 100) if active else 100)
            gain.setEnabled(active)
            gain.valueChanged.connect(lambda value, media_path=path: self.engine.set_channel_gain(f"loop:{self.engine._loop_key(media_path)}", value / 100))
            self.loop_mixer_table.setCellWidget(index, 2, gain)
            mute = QCheckBox("Mute")
            mute.setEnabled(active)
            mute.setChecked(self.engine.channel_muted(source_id) if active else False)
            mute.toggled.connect(lambda checked, channel=source_id: self.engine.set_channel_muted(channel, checked))
            self.loop_mixer_table.setCellWidget(index, 3, mute)
            action = QPushButton("Parar" if active else "Tocar")
            action.clicked.connect(lambda _checked=False, media=int(row["media_id"]), audio_path=path, target=route_combo: self._toggle_mixer_loop(media, audio_path, str(target.currentData())))
            self.loop_mixer_table.setCellWidget(index, 4, action)
        self._populate_crossfade_sources()

    def _output_name(self, device_id: str) -> str:
        for device in AudioEngine.available_outputs():
            if bytes(device.id()).decode(errors="ignore") == device_id:
                return device.description()
        return ""

    @staticmethod
    def _player_state_text(state) -> str:
        from PySide6.QtMultimedia import QMediaPlayer
        if state == QMediaPlayer.PlaybackState.PlayingState:
            return "tocando"
        if state == QMediaPlayer.PlaybackState.PausedState:
            return "pausado"
        return "parado"

    def _set_cart_mixer_route(self, slot: int, route: str):
        try:
            self.engine.set_cartouche_route(slot, route)
            self.db.execute("UPDATE cartouche_slots SET route=? WHERE slot=?", (route, slot))
            self.statusBar().showMessage(f"Cartucho {slot} direcionado para {route}", 4000)
        except (RuntimeError, ValueError) as exc:
            self.statusBar().showMessage(str(exc), 7000)
        QTimer.singleShot(0, self._refresh_audio_console)

    def _set_loop_mixer_route(self, media_id: int, route: str):
        self.db.execute("UPDATE loop_tracks SET route=? WHERE media_id=?", (route, media_id))
        self.statusBar().showMessage(f"Destino da trilha salvo: {route}", 4000)

    def _toggle_mixer_loop(self, media_id: int, path: str, route: str):
        if self.engine.loop_active(path):
            self.engine.stop_loop(path)
            self.db.execute("UPDATE loop_tracks SET enabled=0 WHERE media_id=?", (media_id,))
        else:
            try:
                self.engine.start_loop(path, route)
                self.db.execute("UPDATE loop_tracks SET enabled=1,route=? WHERE media_id=?", (route, media_id))
            except (RuntimeError, ValueError) as exc:
                self.statusBar().showMessage(str(exc), 7000)
        self._refresh_audio_console()

    def _populate_crossfade_sources(self, *_args):
        if not hasattr(self, "crossfade_source_a"):
            return
        bus = self.crossfade_bus.currentText()
        sources = self.engine.active_sources(bus)
        for combo in (self.crossfade_source_a, self.crossfade_source_b):
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for source_id in sources:
                combo.addItem(self._audio_source_label(source_id), source_id)
            selected = combo.findData(current)
            if selected >= 0:
                combo.setCurrentIndex(selected)
            combo.blockSignals(False)
        if len(sources) > 1 and self.crossfade_source_a.currentData() == self.crossfade_source_b.currentData():
            self.crossfade_source_b.setCurrentIndex(1)
        self.crossfade_button.setEnabled(len(sources) >= 2)

    def _audio_source_label(self, source_id: str) -> str:
        if source_id == "program":
            return "Player principal"
        if source_id == "preview":
            return "Pré-escuta"
        if source_id.startswith("cart:"):
            return f"Cartucho {source_id.split(':', 1)[1]}"
        if source_id.startswith("loop:"):
            path_key = source_id.split(":", 1)[1]
            row = next((item for item in self.db.all("SELECT path,title FROM media") if self.engine._loop_key(str(item["path"])) == path_key), None)
            return f"Trilha · {row['title']}" if row else "Trilha em loop"
        return source_id

    def _apply_crossfade(self):
        source_a, source_b = self.crossfade_source_a.currentData(), self.crossfade_source_b.currentData()
        if not source_a or not source_b or source_a == source_b:
            self.statusBar().showMessage("Escolha duas fontes ativas diferentes no mesmo barramento", 6000)
            return
        try:
            self.engine.crossfade(str(source_a), str(source_b), self.crossfade_duration.value())
            self.statusBar().showMessage(f"Crossfade aplicado em {self.crossfade_bus.currentText()}", 4000)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 7000)

    def _audio_meter_updated(self, bus: str, peak: float, rms: float):
        widgets = self.bus_widgets.get(bus)
        if not widgets:
            return
        db_peak = 20 * math.log10(max(peak, 0.001))
        db_rms = 20 * math.log10(max(rms, 0.001))
        widgets["meter"].setValue(max(0, min(600, round((db_peak + 60) * 10))))
        widgets["level"].setText(f"Pico {db_peak:.1f} dBFS · RMS {db_rms:.1f} dBFS")

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
        for dock in (self.library_dock, self.program_dock, self.timeline_dock, self.history_dock, self.cart_dock, self.console_dock):
            view_menu.addAction(dock.toggleViewAction())

    def _wire_audio(self):
        self.engine.program_state.connect(self._playback_state)
        self.engine.preview_state.connect(self._preview_playback_state)
        self.engine.loop_state.connect(lambda _state: self._refresh_audio_console())
        self.engine.bus_meter.connect(self._audio_meter_updated)
        self.engine.program_position.connect(self._position_changed)
        self.engine.program_finished.connect(self._playback_finished)
        self.engine.program_faded_out.connect(self._fixed_transition_faded_out)
        self.engine.program_error.connect(self._audio_error)
        self.engine.loop_error.connect(lambda message: self.statusBar().showMessage(f"Erro na trilha: {message}", 8000))
        self.engine.preview_error.connect(lambda message: self.statusBar().showMessage(f"Erro na pré-escuta: {message}", 8000))
        self.engine.cart_state.connect(self._cartouche_player_state)
        self.engine.cart_finished.connect(self._cartouche_finished)
        self.engine.cart_error.connect(self._cartouche_error)
        self.engine.bus_error.connect(lambda bus, message: self.statusBar().showMessage(f"Saída {bus}: {message}", 10000))
        self.engine.bus_status.connect(self._audio_bus_status)
        self.engine.settings_changed.connect(self._persist_audio_mixer_settings)
        if self._audio_mixer_settings_error:
            QTimer.singleShot(0, lambda message=self._audio_mixer_settings_error: self.statusBar().showMessage(message, 12000))
        for bus in ("PGM", "CUE"):
            error = self.engine._buses[bus].error_message
            if error:
                QTimer.singleShot(0, lambda name=bus, text=error: self.statusBar().showMessage(f"Saída {name}: {text}", 10000))

    def _audio_bus_status(self, bus: str, available: bool, message: str):
        if not available:
            self.statusBar().showMessage(f"Saída {bus}: {message}", 10000)

    def _persist_audio_mixer_settings(self, state: dict):
        self.db.set_setting("audio_mixer_state", json.dumps(state, ensure_ascii=False, separators=(",", ":")))

    def _apply_theme(self):
        # The offscreen Qt plugin may not enumerate Windows' installed fonts.
        # Load Segoe UI from the OS font directory when it is available; it is
        # never copied into the project or redistributed with the application.
        windows_dir = Path(os.environ.get("WINDIR", "C:/Windows"))
        system_font = windows_dir / "Fonts" / "segoeui.ttf"
        if system_font.is_file():
            QFontDatabase.addApplicationFont(str(system_font))
        self.setStyleSheet("""
            /* HitMo Master Console — metallic silver and cobalt blue */
            QMainWindow, QWidget {
                background:#c8cdd3; color:#172536;
                font-family:'Segoe UI'; font-size:9pt;
            }
            QMenuBar, QToolBar, QStatusBar {
                background:#e5e8ec; color:#172536; border:0;
            }
            QToolBar {
                spacing:8px; padding:7px 12px;
                border-bottom:1px solid #9da8b4;
            }
            QDockWidget { font-weight:600; }
            QDockWidget::title {
                background:#174b7d; color:#fff; padding:9px 11px;
                border:1px solid #0d355b;
            }
            QLineEdit, QComboBox, QTimeEdit, QDateEdit, QSpinBox {
                background:#f8fafc; color:#172536;
                border:1px solid #9aa8b6; border-radius:4px;
                padding:7px; selection-background-color:#1769aa;
            }
            QListWidget, QTableWidget {
                background:#f7f9fb; color:#172536;
                alternate-background-color:#e9eef3;
                border:1px solid #9aa8b6; border-radius:4px;
                gridline-color:#d0d8e0;
            }
            QListWidget::item, QTableWidget::item {
                padding:6px; border-bottom:1px solid #dce2e8;
            }
            QListWidget::item:selected, QTableWidget::item:selected {
                background:#c7def2; color:#102b45;
            }
            QHeaderView::section {
                background:#d9e0e7; color:#193a59; padding:8px;
                border:0; border-bottom:2px solid #27679d;
                font-weight:700;
            }
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #f9fbfd, stop:1 #cbd3db);
                color:#18334d; border:1px solid #8796a5;
                border-radius:4px; padding:8px 12px; font-weight:600;
            }
            QPushButton:hover { background:#e5f1fb; border-color:#276da6; }
            QPushButton:pressed { background:#b9cbdc; }
            QPushButton:checked {
                background:#174f82; color:#fff; border-color:#0e3c66;
            }
            QPushButton#primaryButton {
                background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #287dbb, stop:1 #124a79);
                color:#fff; border:1px solid #0c3b63; font-weight:800;
            }
            QPushButton#primaryButton:hover { background:#328bc9; }
            QLabel#stationName { color:#35536e; font-size:10pt; font-weight:700; }
            QLabel#nowTitle { font-size:22pt; font-weight:800; color:#123c62; }
            QLabel#mutedText { color:#536b80; }
            QLabel#clockLabel {
                color:#0c4a78; font-size:14pt; font-weight:800; padding:0 12px;
            }
            QLabel#autoBadge {
                background:#d7eadf; color:#17623b; border:1px solid #8dbda0;
                border-radius:4px; padding:6px 10px; font-weight:800;
            }
            QProgressBar { background:#aebbc7; border:1px solid #8293a3; border-radius:2px; }
            QProgressBar::chunk { background:#ed8b24; border-radius:1px; }
            QScrollBar:vertical { background:#dce2e8; width:11px; }
            QScrollBar::handle:vertical {
                background:#8da3b7; border-radius:4px; min-height:25px;
            }
            QGroupBox {
                border:1px solid #9aa9b7; border-radius:4px;
                margin-top:10px; padding-top:8px; font-weight:700;
                color:#19466d;
            }
            QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }
        """)

    def _restore_window(self):
        try:
            state = self.db.setting("window_state")
            geometry = self.db.setting("window_geometry")
            layout_version = self.db.setting("dock_layout_version", "")
            if geometry:
                self.restoreGeometry(bytes.fromhex(geometry))
            # Apply the redesigned dock arrangement once without deleting the
            # previous saved state; subsequent launches restore the new layout.
            if state and layout_version == "2":
                self.restoreState(bytes.fromhex(state))
            self.db.set_setting("dock_layout_version", "2")
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

    def _show_cue_configuration_notice(self):
        if self.engine.cue_configuration_error:
            self.statusBar().showMessage(f"CUE indisponível: {self.engine.cue_configuration_error}", 10000)
        elif not self.engine.cue_available:
            self.statusBar().showMessage("CUE desativado · configure uma saída de áudio em Preferências", 8000)

    def _operator_changed(self, name: str):
        if hasattr(self, "operator_label"):
            self.operator_label.setText(f"Operador identificado: {name or 'não selecionado'}")

    def _refresh_cartouches(self):
        rows = self.db.all(
            "SELECT s.slot,s.media_id,s.route,m.title,m.category,m.path,m.active,COALESCE(c.material_kind,'audio') AS material_kind "
            "FROM cartouche_slots s LEFT JOIN media m ON m.id=s.media_id "
            "LEFT JOIN material_categories c ON c.name=m.category ORDER BY s.slot"
        )
        by_slot = {int(row["slot"]): row for row in rows}
        for slot, widgets in self.cart_slots.items():
            row = by_slot.get(slot)
            is_active = self.engine.cartouche_active(slot)
            has_material = bool(row and row["media_id"] is not None and row["title"])
            if not has_material:
                label, state = f"Cartucho {slot}", "Vazio"
            else:
                label = str(row["title"])
                state = self._cart_states.get(slot, "Pronto")
            widgets["title"].setText(label)
            widgets["category"].setText((str(row["category"]) if has_material else "Vazio") + f" · F{slot}")
            widgets["state"].setText(state)
            widgets["play"].setText("RETOMAR" if is_active and state == "Pausado" else f"PLAY · F{slot}")
            widgets["play"].setEnabled(has_material and (not is_active or state == "Pausado"))
            widgets["pause"].setEnabled(is_active and state == "Tocando")
            widgets["stop"].setEnabled(is_active)
            widgets["assign"].setEnabled(not is_active)
            widgets["remove"].setEnabled(bool(row and row["media_id"] is not None) and not is_active)
        self._refresh_audio_console()

    def _assign_cartouche(self, slot: int):
        if self.engine.cartouche_active(slot):
            self.statusBar().showMessage("Pare o cartucho antes de trocar sua associação", 5000)
            return
        materials = self.db.all(
            "SELECT m.id,m.title,m.artist,m.category FROM media m "
            "LEFT JOIN material_categories c ON c.name=m.category "
            "WHERE m.active=1 AND COALESCE(c.material_kind,'audio')='audio' ORDER BY m.title COLLATE NOCASE"
        )
        if not materials:
            self.statusBar().showMessage("Não há materiais de áudio ativos para associar", 6000)
            return
        labels = [f"[{int(row['id'])}] {row['title']} · {row['category']}" for row in materials]
        label, accepted = QInputDialog.getItem(self, f"Associar cartucho {slot}", "Material cadastrado:", labels, 0, False)
        if not accepted:
            return
        media_id = int(label.split("]", 1)[0][1:])
        self.db.execute(
            "INSERT INTO cartouche_slots(slot,media_id) VALUES(?,?) "
            "ON CONFLICT(slot) DO UPDATE SET media_id=excluded.media_id", (slot, media_id)
        )
        self._cart_states[slot] = "Pronto"
        self._refresh_cartouches()

    def _remove_cartouche(self, slot: int):
        if self.engine.cartouche_active(slot):
            self.statusBar().showMessage("Pare o cartucho antes de remover sua associação", 5000)
            return
        self.db.execute("UPDATE cartouche_slots SET media_id=NULL WHERE slot=?", (slot,))
        self._cart_states[slot] = "Vazio"
        self._refresh_cartouches()

    def _fire_cartouche(self, slot: int):
        row = self.db.one(
            "SELECT m.id,m.title,m.artist,m.path,m.category,m.active,s.route,COALESCE(c.material_kind,'audio') AS material_kind "
            "FROM cartouche_slots s LEFT JOIN media m ON m.id=s.media_id "
            "LEFT JOIN material_categories c ON c.name=m.category WHERE s.slot=?", (slot,)
        )
        if not row or row["id"] is None:
            self.statusBar().showMessage(f"Cartucho {slot} vazio", 4000)
            return False
        if self.engine.cartouche_active(slot):
            if self.engine.cartouche_paused(slot):
                self.engine.resume_cartouche(slot)
                return True
            self.statusBar().showMessage(f"Cartucho {slot} já está ativo; disparo duplicado ignorado", 5000)
            return False
        snapshot = dict(row)
        if not row["active"] or row["material_kind"] != "audio":
            error = "Material inativo ou não classificado como áudio"
            return self._record_cartouche_failure(slot, snapshot, error)
        if not Path(row["path"]).is_file():
            return self._record_cartouche_failure(slot, snapshot, "Arquivo de áudio não encontrado")
        route = str(row["route"] or "PGM").upper()
        try:
            self.engine.set_cartouche_route(slot, route)
        except (RuntimeError, ValueError) as exc:
            return self._record_cartouche_failure(slot, snapshot, str(exc))
        history_id = self.db.execute(
            "INSERT INTO cartouche_history(slot,media_id_snapshot,title,path,category,operator,status) "
            "VALUES(?,?,?,?,?,?, 'loading')",
            (slot, int(row["id"]), row["title"], row["path"], row["category"], self.operator_combo.currentText()),
        )
        self._cart_history_ids[slot] = history_id
        if not self.engine.play_cartouche(slot, str(row["path"])):
            self._cart_history_ids.pop(slot, None)
            self.db.execute(
                "UPDATE cartouche_history SET ended_at=CURRENT_TIMESTAMP,status='failed',error_message='O player recusou o disparo' WHERE id=?",
                (history_id,),
            )
            self._cart_states[slot] = "Erro"
            self._refresh_cartouches()
            self._refresh_history()
            self.statusBar().showMessage(f"Cartucho {slot}: o player recusou o disparo", 6000)
            return False
        self._cart_states[slot] = "Pronto"
        self._refresh_cartouches()
        self.statusBar().showMessage(f"Cartucho {slot} disparado · {row['title']} · destino {route}", 7000)
        self._refresh_history()
        return True

    def _record_cartouche_failure(self, slot: int, row: dict, error: str):
        self.db.execute(
            "INSERT INTO cartouche_history(slot,media_id_snapshot,title,path,category,operator,ended_at,status,error_message) "
            "VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP,'failed',?)",
            (slot, row.get("id"), row.get("title", ""), row.get("path", ""), row.get("category", ""),
             self.operator_combo.currentText(), error),
        )
        self._cart_states[slot] = "Erro"
        self._refresh_cartouches()
        self.statusBar().showMessage(f"Cartucho {slot}: {error}", 8000)
        self._refresh_history()
        return False

    def _pause_cartouche(self, slot: int):
        if self.engine.cartouche_active(slot):
            self.engine.pause_cartouche(slot)

    def _stop_cartouche(self, slot: int):
        if not self.engine.cartouche_active(slot):
            return
        self.engine.stop_cartouche(slot)
        history_id = self._cart_history_ids.pop(slot, None)
        if history_id:
            self.db.execute(
                "UPDATE cartouche_history SET ended_at=CURRENT_TIMESTAMP,status='stopped' WHERE id=? AND status IN ('loading','playing','paused')",
                (history_id,),
            )
        self._cart_states[slot] = "Pronto"
        self._refresh_cartouches()
        self._refresh_history()

    def _stop_all_cartouches(self):
        for slot in range(1, 13):
            self._stop_cartouche(slot)
        self.engine.stop_all_cartouches()
        self.statusBar().showMessage("Todos os cartuchos ativos foram parados", 5000)

    def _cartouche_player_state(self, slot: int, state):
        from PySide6.QtMultimedia import QMediaPlayer
        history_id = self._cart_history_ids.get(slot)
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._cart_states[slot] = "Tocando"
            if history_id:
                self.db.execute(
                    "UPDATE cartouche_history SET started_at=CASE WHEN started_at='' THEN CURRENT_TIMESTAMP ELSE started_at END,status='playing' WHERE id=?",
                    (history_id,),
                )
        elif state == QMediaPlayer.PlaybackState.PausedState:
            self._cart_states[slot] = "Pausado"
            if history_id:
                self.db.execute("UPDATE cartouche_history SET status='paused' WHERE id=?", (history_id,))
        self._refresh_cartouches()
        self._refresh_audio_console()

    def _cartouche_finished(self, slot: int):
        history_id = self._cart_history_ids.pop(slot, None)
        if history_id:
            self.db.execute(
                "UPDATE cartouche_history SET ended_at=CURRENT_TIMESTAMP,status='completed' WHERE id=? AND status IN ('loading','playing','paused')",
                (history_id,),
            )
        self._cart_states[slot] = "Pronto"
        self._refresh_cartouches()
        self._refresh_audio_console()
        self._refresh_history()

    def _cartouche_error(self, slot: int, message: str):
        history_id = self._cart_history_ids.pop(slot, None)
        if history_id:
            self.db.execute(
                "UPDATE cartouche_history SET ended_at=CURRENT_TIMESTAMP,status='failed',error_message=? WHERE id=?",
                (message, history_id),
            )
        elif message == "Arquivo de áudio não encontrado.":
            # Engine-originated missing files are already logged by the UI path when possible.
            return
        self._cart_states[slot] = "Erro"
        self._refresh_cartouches()
        self._refresh_audio_console()
        self.statusBar().showMessage(f"Falha no cartucho {slot}: {message}", 8000)
        self._refresh_history()

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
            self._refresh_audio_console()

    def _configure_api_service(self):
        host, accepted = QInputDialog.getText(self, "Serviço central", "Endereço local (LAN com TLS deve ser iniciada pela linha de comando):", text=self._api_host)
        if not accepted:
            return
        host = host.strip()
        if not host:
            QMessageBox.warning(self, "Endereço inválido", "Informe um endereço de escuta.")
            return
        try:
            loopback = host.casefold() == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            QMessageBox.warning(self, "Endereço protegido", "A interface inicia HTTP somente em loopback. Para LAN, inicie o serviço com TLS pela linha de comando; veja docs/service-api.md.")
            return
        port, accepted = QInputDialog.getInt(self, "Serviço central", "Porta:", value=self._api_port, min=1, max=65535)
        if not accepted:
            return
        self._api_host, self._api_port = host, port
        self.db.set_setting("api_host", host)
        self.db.set_setting("api_port", str(port))
        self._refresh_api_status()

    def _start_api_service(self):
        if self._api_process.state() != QProcess.ProcessState.NotRunning:
            self.statusBar().showMessage("O serviço iniciado pelo Studio já está ativo.", 5000)
            return
        self._api_process.setWorkingDirectory(str(Path(__file__).resolve().parent.parent))
        self._api_process.start(sys.executable, ["-m", "hitmo.server", "--host", self._api_host, "--port", str(self._api_port)])
        self.statusBar().showMessage("Iniciando o serviço central…", 5000)
        QTimer.singleShot(1500, self._refresh_api_status)

    def _stop_api_service(self):
        if self._api_process.state() == QProcess.ProcessState.NotRunning:
            self.statusBar().showMessage("Nenhum serviço iniciado pelo Studio está ativo.", 5000)
            return
        self._api_process.terminate()
        if not self._api_process.waitForFinished(2500):
            self._api_process.kill()
            self._api_process.waitForFinished(1500)
        self._refresh_api_status()

    def _refresh_api_status(self):
        from .api_client import HitMoAPIClient
        client = HitMoAPIClient(f"http://{self._api_host}:{self._api_port}", "", "", timeout=0.35)
        try:
            status = client.health()
        except (ConnectionError, ValueError):
            state = "iniciando" if self._api_process.state() != QProcess.ProcessState.NotRunning else "offline"
            self.api_status_label.setText(f"API central · {state}")
            return
        self.api_status_label.setText(f"API central · online · v{status.get('version', '?')}")

    def _api_process_finished(self, exit_code, exit_status):
        self._refresh_api_status()
        if exit_code:
            self.statusBar().showMessage(f"O serviço central encerrou com código {exit_code}. Consulte o registro de erros.", 9000)

    def _api_process_error(self, error):
        self.api_status_label.setText("API central · erro ao iniciar")
        self.statusBar().showMessage(f"Não foi possível iniciar o serviço central: {self._api_process.errorString()}", 10000)

    def _show_api_clients(self):
        username, accepted = QInputDialog.getText(self, "Clientes recentes", "Usuário administrador da API:")
        if not accepted or not username.strip():
            return
        password, accepted = QInputDialog.getText(self, "Clientes recentes", "Senha:", QLineEdit.EchoMode.Password)
        if not accepted:
            return
        from .api_client import HitMoAPIClient
        try:
            clients = HitMoAPIClient(f"http://{self._api_host}:{self._api_port}", username.strip(), password, timeout=1.0).clients()["clients"]
        except (ConnectionError, RuntimeError) as exc:
            QMessageBox.warning(self, "Serviço central", str(exc))
            return
        listing = "\n".join(f"{row['username']} · {row['address']}" for row in clients) or "Nenhum cliente autenticado recentemente."
        QMessageBox.information(self, "Clientes recentes", listing)

    def _open_api_log(self):
        from .database import default_data_dir
        path = default_data_dir() / "logs" / "api.log"
        if not path.is_file():
            QMessageBox.information(self, "Registro de erros", "O serviço ainda não criou o arquivo de registro.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

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

    def _poll_catalog_revision(self):
        row = self.db.one("SELECT revision FROM catalog_state WHERE id=1")
        if not row:
            return
        revision = int(row["revision"])
        if revision == self._observed_catalog_revision:
            return
        self._observed_catalog_revision = revision
        self._refresh_category_filter()
        self._refresh_library()
        self.statusBar().showMessage("Catálogo atualizado por outra estação; a execução atual foi mantida.", 5000)

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
        for row in self.db.all("SELECT m.path,m.title,l.route FROM loop_tracks l JOIN media m ON m.id=l.media_id WHERE l.enabled=1"):
            if Path(row["path"]).is_file():
                try:
                    self.engine.start_loop(str(row["path"]), str(row["route"] or "PGM"))
                except RuntimeError as exc:
                    self.statusBar().showMessage(f"Trilha {row['title']}: {exc}", 8000)
            else:
                self.statusBar().showMessage(f"Trilha salva indisponível: {row['title']}", 8000)
        self._refresh_audio_console()

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
                self._refresh_audio_console()
                self.statusBar().showMessage(f"Trilha interrompida: {row['title']}", 5000)
            else:
                self.engine.start_loop(row["path"])
                self.db.execute("INSERT INTO loop_tracks(media_id,enabled,route) VALUES(?,1,'PGM') ON CONFLICT(media_id) DO UPDATE SET enabled=1", (media_id,))
                self._refresh_audio_console()
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

    def _insert_playback_history(
        self, row: dict, *, status: str, source_type: str, source_ref: int | None,
        scheduled_for: str, occurrence_item_id: int | None = None, error_message: str = "",
    ) -> int:
        material_id = row.get("id", row.get("media_id"))
        context = self.db.one(
            "SELECT o.id AS occurrence_id,o.block_id,o.block_name_snapshot "
            "FROM program_occurrence_items i JOIN program_occurrences o ON o.id=i.occurrence_id WHERE i.id=?",
            (occurrence_item_id,),
        ) if occurrence_item_id else None
        return self.db.execute(
            "INSERT INTO playback_history(media_id,title,artist,path,category,operator,started_at,ended_at,status,planned_for,"
            "source_type,source_ref,material_id_snapshot,occurrence_id,block_id,block_name_snapshot,error_message) "
            "VALUES(?,?,?,?,?,?, '',CASE WHEN ?='failed' THEN CURRENT_TIMESTAMP ELSE '' END,?,?,?,?,?,?,?,?,?)",
            (
                material_id, row.get("title", ""), row.get("artist", ""), row.get("path", ""),
                row.get("category", "Música"), self.operator_combo.currentText(), status, status,
                scheduled_for, source_type, source_ref, material_id,
                int(context["occurrence_id"]) if context else None,
                int(context["block_id"]) if context and context["block_id"] is not None else None,
                str(context["block_name_snapshot"] or "") if context else "", error_message,
            ),
        )

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
            self._insert_playback_history(
                row, status="failed", source_type=source_type, source_ref=source_ref,
                scheduled_for=scheduled_for, occurrence_item_id=occurrence_item_id, error_message=message,
            )
            self._refresh_history()
            return False
        if not Path(row["path"]).is_file():
            operator = self.operator_combo.currentText()
            self._insert_playback_history(
                row, status="failed", source_type=source_type, source_ref=source_ref,
                scheduled_for=scheduled_for, occurrence_item_id=occurrence_item_id,
                error_message="Arquivo não encontrado",
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
        self._history_id = self._insert_playback_history(
            row, status="loading", source_type=source_type, source_ref=source_ref,
            scheduled_for=scheduled_for, occurrence_item_id=occurrence_item_id,
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

    def _end_current(self, status: str, error_message: str = "", played_ms: int | None = None):
        self._finish_history(status, error_message, played_ms)
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
        if hasattr(self, "program_channel_mute") and self.program_channel_mute.isChecked() != muted:
            self.program_channel_mute.blockSignals(True)
            self.program_channel_mute.setChecked(muted)
            self.program_channel_mute.blockSignals(False)

    def _stop_loops(self):
        self.engine.stop_loop()
        self.db.execute("UPDATE loop_tracks SET enabled=0")
        self._refresh_audio_console()
        self.statusBar().showMessage("Todas as trilhas em loop foram interrompidas", 5000)

    def _playback_state(self, state):
        from PySide6.QtMultimedia import QMediaPlayer
        self.program_audio_state.setText(f"PGM · {self._player_state_text(state)}")
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
        self._populate_crossfade_sources()

    def _preview_playback_state(self, state):
        self.cue_audio_state.setText(f"CUE · {self._player_state_text(state)}")
        self._populate_crossfade_sources()

    def _start_history(self):
        if not self._current:
            return
        if self._history_id:
            self.db.execute("UPDATE playback_history SET started_at=CASE WHEN started_at='' THEN CURRENT_TIMESTAMP ELSE started_at END,status='playing' WHERE id=?", (self._history_id,))
        else:
            self._history_id = self._insert_playback_history(
                self._current, status="playing", source_type=self._current_source_type,
                source_ref=self._current_source_ref, scheduled_for=self._current_planned_for,
                occurrence_item_id=self._current_occurrence_item_id,
            )
        self._refresh_history()

    def _finish_history(self, status: str = "completed", error_message: str = "", played_ms: int | None = None):
        if self._history_id:
            position_ms = self.engine.program.position() if played_ms is None else played_ms
            seconds = max(0.0, float(position_ms) / 1000.0)
            self.db.execute("UPDATE playback_history SET ended_at=CURRENT_TIMESTAMP,played_seconds=?,status=?,error_message=? WHERE id=?", (seconds, status, error_message, self._history_id))
            self._history_id = None
            self._refresh_history()

    def _fixed_transition_faded_out(self, played_ms: int):
        self._fixed_transition_fade_done = True
        self._fixed_transition_position_ms = int(played_ms)
        if self._fixed_transition_due:
            self._finish_fixed_transition()

    def _begin_fixed_transition_fade(self):
        self._fixed_fade_timer_target = None
        target = self._fixed_transition_target
        if target is None or not self._current or self._fixed_transition_due:
            return
        now = datetime.now()
        cursor_text = self.db.setting("scheduler_last_checked_at", "")
        try:
            scheduler_cursor = _SystemDateTime.fromisoformat(cursor_text) if cursor_text else None
        except ValueError:
            scheduler_cursor = None
        if scheduler_cursor is not None and now < scheduler_cursor:
            self._fixed_transition_timer.stop()
            self.engine.cancel_program_fade()
            self._fixed_transition_target = None
            self._fixed_transition_fade_done = False
            return
        remaining_ms = int((target - now).total_seconds() * 1000)
        if remaining_ms <= 0:
            self._fixed_transition_late = True
            self.engine.fade_out_program(0)
            return
        self.engine.fade_out_program(min(self._fixed_transition_fade_ms, remaining_ms))

    def _finish_fixed_transition(self, natural_end: bool = False):
        if self._current and not natural_end:
            target_text = self._fixed_transition_target.strftime("%Y-%m-%d %H:%M") if self._fixed_transition_target else "horário programado"
            fade_ms = 0 if self._fixed_transition_late else self._fixed_transition_fade_ms
            reason = f"Interrompido pela transição Fixo para bloco comercial em {target_text}; fade-out configurado: {fade_ms} ms."
            if self._fixed_transition_late:
                reason += " O corte foi imediato porque o limite foi detectado após o horário previsto."
            self._end_current("interrupted", reason, self._fixed_transition_position_ms)
        self._clear_current()
        self._fixed_transition_timer.stop()
        self._fixed_fade_timer_target = None
        self._fixed_transition_target = None
        self._fixed_transition_due = False
        self._fixed_transition_fade_done = False
        self._fixed_transition_position_ms = None
        self._fixed_transition_late = False
        self.now_title.setText("Transição de bloco concluída")
        self.now_artist.setText("Programação comercial no horário fixo")
        QTimer.singleShot(0, self._play_next_pending)

    def _playback_finished(self):
        fixed_due = self._fixed_transition_due
        if self._current:
            self._end_current("completed")
        self._clear_current()
        if fixed_due:
            self._finish_fixed_transition(natural_end=True)
            return
        self.now_title.setText("Faixa concluída")
        self.now_artist.setText("Pronto para a próxima inserção")
        if self.programming.next_occurrence_item() or self.db.setting("auto_advance", "1") == "1":
            QTimer.singleShot(0, self._play_scheduled_or_next)

    def _play_scheduled_or_next(self):
        self._play_next_pending()

    def _audio_error(self, message):
        fixed_due = self._fixed_transition_due
        if self._current:
            self._end_current("failed", message)
        self._clear_current()
        self.statusBar().showMessage(f"Falha ao reproduzir áudio: {message}", 10000)
        if fixed_due:
            self._finish_fixed_transition(natural_end=True)
            return
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
        self._refresh_timeline_grid()

    def _refresh_timeline_grid(self):
        if not hasattr(self, "timeline_grid"):
            return
        self.timeline_grid.clearContents()
        events = self.timeline.events()
        buckets = {}
        for event in events:
            try:
                hour_text, minute_text = str(event["time_of_day"]).split(":", 1)
                hour, minute = int(hour_text), int(minute_text)
            except (TypeError, ValueError):
                continue
            if not (0 <= hour < 24 and 0 <= minute < 60):
                continue
            column = minute // 5
            buckets.setdefault((hour, column), []).append(event)
        for hour in range(24):
            for column in range(12):
                entries = buckets.get((hour, column), [])
                if entries:
                    labels = []
                    for event in entries:
                        status = "●" if event["enabled"] else "Ⅱ"
                        labels.append(f"{status} {event['time_of_day']}  {event['title']}")
                    item = QTableWidgetItem("\\n".join(labels))
                    item.setData(ROLE, int(entries[0]["id"]))
                    details = []
                    for event in entries:
                        details.append(
                            f"{event['time_of_day']} · {event['title']} · {event['category']} · "
                            f"{'Ativo' if event['enabled'] else 'Pausado'} · Dias: {event['days']} · "
                            f"Áudio: {event['media_title'] or 'não associado'}"
                        )
                    item.setToolTip("\\n".join(details))
                    category = str(entries[0]["category"]).casefold()
                    if "comercial" in category:
                        item.setBackground(QColor("#245b43"))
                    elif "música" in category or "musica" in category:
                        item.setBackground(QColor("#244d83"))
                    else:
                        item.setBackground(QColor("#67416f"))
                    item.setForeground(QColor("#ffffff"))
                else:
                    item = QTableWidgetItem("")
                    item.setToolTip(f"{hour:02d}:{column * 5:02d} · Sem evento cadastrado")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.timeline_grid.setItem(hour, column, item)

    def _timeline_grid_activated(self, hour: int, column: int):
        item = self.timeline_grid.item(hour, column)
        if not item or item.data(ROLE) is None:
            return
        event_id = int(item.data(ROLE))
        for row in range(self.timeline_table.rowCount()):
            cell = self.timeline_table.item(row, 0)
            if cell and int(cell.data(ROLE) or -1) == event_id:
                self.timeline_table.selectRow(row)
                self.timeline_table.scrollToItem(cell)
                return

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

    def _schedule_fixed_transition(self, now: datetime):
        cursor_text = self.db.setting("scheduler_last_checked_at", "")
        try:
            scheduler_cursor = _SystemDateTime.fromisoformat(cursor_text) if cursor_text else None
        except ValueError:
            scheduler_cursor = None
        if scheduler_cursor is not None and now < scheduler_cursor:
            self._fixed_transition_timer.stop()
            if self.engine.program_fade_active:
                self.engine.cancel_program_fade()
            self._fixed_transition_target = None
            self._fixed_fade_timer_target = None
            self._fixed_transition_fade_done = False
            return
        upcoming = self.programming.next_fixed_transition(now)
        if upcoming is None:
            if (
                self._fixed_transition_target is not None
                and now.strftime("%Y-%m-%d %H:%M") == self._fixed_transition_target.strftime("%Y-%m-%d %H:%M")
            ):
                # Preserve a fade that reaches its boundary before the 500 ms
                # programming tick materializes that minute's occurrence.
                return
            if not self._fixed_transition_due:
                self._fixed_transition_timer.stop()
                if self.engine.program_fade_active:
                    self.engine.cancel_program_fade()
                self._fixed_transition_target = None
                self._fixed_fade_timer_target = None
                self._fixed_transition_fade_done = False
            return
        target = upcoming["at"]
        if target != self._fixed_transition_target:
            self._fixed_transition_timer.stop()
            if self.engine.program_fade_active:
                self.engine.cancel_program_fade()
            self._fixed_transition_target = target
            self._fixed_transition_fade_ms = int(upcoming["fade_ms"])
            self._fixed_fade_timer_target = None
            self._fixed_transition_fade_done = False
            self._fixed_transition_late = False
        if not self._current or self._fixed_transition_due or self.engine.program_fade_active:
            return
        remaining_ms = int((target - now).total_seconds() * 1000)
        if remaining_ms <= 0:
            return
        if self._fixed_fade_timer_target == target and self._fixed_transition_timer.isActive():
            return
        self._fixed_fade_timer_target = target
        self._fixed_transition_timer.start(max(0, remaining_ms - self._fixed_transition_fade_ms))

    def _tick(self, now: datetime | None = None):
        now = now or datetime.now()
        self.clock_label.setText(now.strftime("%H:%M:%S"))
        self._schedule_fixed_transition(now)
        dispatch = self.programming.dispatch_window(
            now,
            preserve_item_id=self._current_occurrence_item_id,
            force_recovery=self._scheduler_first_tick,
            operator=self.operator_combo.currentText(),
        )
        self._scheduler_first_tick = False
        due = dispatch["dispatch_ids"]
        if dispatch["clock_rollback"]:
            self.statusBar().showMessage("Relógio retrocedeu · despacho pausado para evitar duplicidade", 8000)
            return
        if dispatch["missed_ids"] or dispatch["ignored_ids"]:
            self.statusBar().showMessage(
                f"Reconciliação do agendamento · {len(dispatch['missed_ids'])} ocorrência(s) perdida(s) · "
                f"{len(dispatch['ignored_ids'])} ignorada(s) · áudio atual preservado",
                8000,
            )
        if due:
            pending_items = self.db.one(
                "SELECT COUNT(*) AS n FROM program_occurrence_items WHERE status='planned'"
            )["n"]
            self.statusBar().showMessage(
                f"Programação agendada · {len(due)} ocorrência(s) persistida(s) · {pending_items} inserção(ões) pendente(s)",
                8000,
            )
            placeholders = ",".join("?" for _ in due)
            fixed_occurrence = self.db.one(
                f"SELECT scheduled_for,fixed_fade_ms FROM program_occurrences "
                f"WHERE id IN ({placeholders}) AND origin_type='block' AND transition_policy='fixed' "
                "ORDER BY scheduled_for,id LIMIT 1",
                tuple(due),
            )
            if fixed_occurrence:
                self._fixed_transition_due = True
                self._fixed_transition_target = datetime.strptime(fixed_occurrence["scheduled_for"], "%Y-%m-%d %H:%M")
                self._fixed_transition_fade_ms = int(fixed_occurrence["fixed_fade_ms"])
                if self._current:
                    if self._fixed_transition_fade_done:
                        self._finish_fixed_transition()
                    elif not self.engine.program_fade_active:
                        # If the application woke after the boundary, don't delay
                        # the fixed commercial further; preserve the late-cut reason.
                        self._fixed_transition_late = True
                        self.engine.fade_out_program(0)
                else:
                    self._finish_fixed_transition(natural_end=True)
                self._refresh_timeline()
                self._refresh_program_blocks()
                return
            # A due occurrence is prepared in SQLite immediately, but PGM changes
            # source only when no item is active. Natural end/error advances it.
            if not self._current:
                self._play_next_pending()
            self._refresh_timeline()
            self._refresh_program_blocks()
        elif dispatch["missed_ids"] or dispatch["ignored_ids"]:
            self._refresh_timeline()
            self._refresh_program_blocks()

    def _refresh_history(self):
        if not hasattr(self, "history_table"):
            return
        filters = self._history_filter_values()
        result = self.history.query(**filters, limit=250, offset=self._history_offset)
        self.history_table.setRowCount(0)
        for row in result["history"]:
            index = self.history_table.rowCount()
            self.history_table.insertRow(index)
            title = f"{row['title']} · {row['artist']}" if row["artist"] else row["title"]
            if row["path"]:
                title = f"{title} · {Path(row['path']).name}"
            values = (
                row["event_time"], (
                    "Evento do player PGM" if row["record_type"] == "player_event" else
                    "Evento local de cartucho · sem confirmação de transmissão" if row["record_type"] == "cartouche_event" else
                    "Item de ocorrência"
                ),
                row["started_at"], row["ended_at"], title, row["material_id"], row["category"], row["operator"],
                row["status"], row["selection_state"], row["occurrence_status"], row["planned_for"], row["selected_at"], row["block_name_snapshot"], row["occurrence_id"],
                f"{float(row['played_seconds'] or 0):.3f}", row["error_message"],
            )
            for col, value in enumerate(values):
                self.history_table.setItem(index, col, QTableWidgetItem(str(value)))
        self.history_count.setText(
            f"Exibindo {self._history_offset + 1 if result['history'] else 0}–"
            f"{self._history_offset + len(result['history'])} de {result['total']} evento(s)."
        )
        self.history_prev.setEnabled(self._history_offset > 0)
        self.history_next.setEnabled(self._history_offset + len(result["history"]) < result["total"])

    def _history_filter_values(self) -> dict[str, str]:
        return {
            "start_date": self.history_from.date().toString("yyyy-MM-dd") if self.history_period_enabled.isChecked() else "",
            "end_date": self.history_to.date().toString("yyyy-MM-dd") if self.history_period_enabled.isChecked() else "",
            "material": self.history_material.text().strip(),
            "category": str(self.history_category.currentData() or ""),
            "status": str(self.history_status.currentData() or ""),
        }

    def _toggle_history_period(self, enabled: bool):
        self.history_from.setEnabled(enabled)
        self.history_to.setEnabled(enabled)
        if hasattr(self, "history_table"):
            self._history_offset = 0
            self._refresh_history()

    def _apply_history_filters(self):
        self._history_offset = 0
        self._refresh_history()

    def _history_page(self, direction: int):
        self._history_offset = max(0, self._history_offset + direction * 250)
        self._refresh_history()

    def _refresh_history_filter_options(self):
        if not hasattr(self, "history_category"):
            return
        options = self.history.filter_options()
        current_category = self.history_category.currentData()
        current_status = self.history_status.currentData()
        self.history_category.clear()
        self.history_category.addItem("Todas as categorias", "")
        for category in options["categories"]:
            self.history_category.addItem(category, category)
        self.history_status.clear()
        self.history_status.addItem("Todas as situações", "")
        status_values = sorted(
            set(options["statuses"]) |
            {"planned", "selected", "loading", "playing", "paused", "completed", "interrupted", "discarded", "ignored", "failed", "ejected", "stopped", "skipped", "missed", "delayed", "running"},
            key=str.casefold,
        )
        for status in status_values:
            self.history_status.addItem(status, status)
        if current_category:
            index = self.history_category.findData(current_category)
            if index >= 0:
                self.history_category.setCurrentIndex(index)
        if current_status:
            index = self.history_status.findData(current_status)
            if index >= 0:
                self.history_status.setCurrentIndex(index)

    def _export_history(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exportar histórico", "hitmo-historico.csv", "CSV (*.csv)")
        if not path:
            return
        filters = self._history_filter_values()
        rows = []
        offset = 0
        while True:
            page = self.history.query(**filters, limit=1000, offset=offset)["history"]
            rows.extend(page)
            if len(page) < 1000:
                break
            offset += len(page)
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow([
                "Evento no software", "Tipo de registro", "Início observado", "Fim observado", "Título", "Artista", "ID do material", "Arquivo", "Categoria",
                "Operador", "Segundos do player", "Situação do item", "Seleção", "Situação da ocorrência", "Programado para", "Selecionado em", "Origem", "Referência da origem",
                "ID da ocorrência", "ID do bloco", "Snapshot do bloco", "Erro/motivo",
            ])
            writer.writerows([(
                row["event_time"], row["record_type"], row["started_at"], row["ended_at"], row["title"], row["artist"], row["material_id"], row["path"],
                row["category"], row["operator"], row["played_seconds"], row["status"], row["selection_state"], row["occurrence_status"], row["planned_for"], row["selected_at"],
                row["source_type"], row["source_ref"], row["occurrence_id"], row["block_id"],
                row["block_name_snapshot"], row["error_message"],
            ) for row in rows])
        self.statusBar().showMessage(f"Histórico exportado: {path}", 7000)

    def closeEvent(self, event):
        for timer_name in ("_timer", "_catalog_poll", "_api_poll", "_fixed_transition_timer"):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                timer.stop()
        if self._current:
            queue_id = self._current_queue_id
            self._end_current("interrupted", "Aplicativo encerrado durante a execução")
            if queue_id:
                self.playlist.set_status(queue_id, "planned")
            self._clear_current()
        for slot, history_id in tuple(self._cart_history_ids.items()):
            self.db.execute(
                "UPDATE cartouche_history SET ended_at=CURRENT_TIMESTAMP,status='interrupted',"
                "error_message='Aplicativo encerrado durante a execução' WHERE id=? AND status IN ('loading','playing','paused')",
                (history_id,),
            )
        self._cart_history_ids.clear()
        self.engine.shutdown()
        if self._api_process.state() != QProcess.ProcessState.NotRunning:
            self._api_process.terminate()
            if not self._api_process.waitForFinished(2000):
                self._api_process.kill()
                self._api_process.waitForFinished(1000)
        self.db.set_setting("window_geometry", bytes(self.saveGeometry()).hex())
        self.db.set_setting("window_state", bytes(self.saveState()).hex())
        event.accept()

