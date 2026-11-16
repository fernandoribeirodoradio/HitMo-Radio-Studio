from __future__ import annotations

import copy
import sys
from datetime import date

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget,
    QTimeEdit, QVBoxLayout, QWidget,
)

from .api_client import HitMoAPIClient
from .config_manager_app import ConnectionDialog

ROLE = Qt.ItemDataRole.UserRole
DAY_NAMES = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")


class BlockDialog(QDialog):
    def __init__(self, record: dict | None = None, parent=None):
        super().__init__(parent)
        self.record = record
        self.setWindowTitle("Editar bloco" if record else "Novo bloco")
        self.setMinimumWidth(420)
        self.name = QLineEdit(str(record.get("name", "")) if record else "")
        self.block_type = QComboBox()
        self.block_type.addItems(("Música", "Comercial"))
        if record:
            self.block_type.setCurrentText(record["block_type"])
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        if record:
            self.time.setTime(QTime.fromString(record["time_of_day"], "HH:mm"))
        self.days = []
        day_row = QHBoxLayout()
        selected_days = set(record.get("days", [])) if record else set(range(7))
        if record and isinstance(record.get("days"), str):
            selected_days = {int(day) for day in record["days"].split(",") if day.isdigit()}
        for index, label in enumerate(DAY_NAMES):
            box = QCheckBox(label)
            box.setChecked(index in selected_days)
            self.days.append(box)
            day_row.addWidget(box)
        form = QFormLayout()
        form.addRow("Nome do bloco", self.name)
        form.addRow("Tipo", self.block_type)
        form.addRow("Horário de início", self.time)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Dias de recorrência"))
        layout.addLayout(day_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Bloco", "Informe um nome para o bloco.")
            return
        if not any(day.isChecked() for day in self.days):
            QMessageBox.warning(self, "Bloco", "Selecione pelo menos um dia de execução.")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "block_type": self.block_type.currentText(),
            "time_of_day": self.time.time().toString("HH:mm"),
            "days": [index for index, day in enumerate(self.days) if day.isChecked()],
            "enabled": bool(self.record.get("enabled", True)) if self.record else True,
        }


class ProgrammingManagerWindow(QMainWindow):
    """Remote programming editor that only writes revisioned server drafts."""

    def __init__(self, api: HitMoAPIClient, identity: dict, parent=None):
        super().__init__(parent)
        self.api = api
        self.identity = identity
        self.can_edit = identity.get("role") in {"admin", "editor"}
        self.saved_revision = 0
        self.base_revision = 0
        self.saved_blocks: list[dict] = []
        self.blocks: list[dict] = []
        self.materials: list[dict] = []
        self.folders: list[dict] = []
        self.drafts: list[dict] = []
        self.draft_id: int | None = None
        self.draft_revision: int | None = None
        self.draft_status = "local"
        self.draft_name = ""
        self.dirty = False
        self.setWindowTitle("HitMo Programação Remota")
        self.resize(1340, 820)
        self._build_ui()
        self._apply_theme()
        self.refresh_remote()

    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        header = QHBoxLayout()
        self.identity_label = QLabel(f"Conectado: {self.identity.get('authenticated_as', 'usuário')} · {self.api.base_url}")
        self.revision_label = QLabel("Grade publicada · revisão —")
        header.addWidget(self.identity_label, 1)
        header.addWidget(self.revision_label)
        self.refresh_button = QPushButton("Atualizar servidor")
        self.refresh_button.clicked.connect(self.refresh_remote)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        warning = QLabel("Rascunhos são isolados. Publicar atualiza blocos futuros; ocorrências já geradas, inclusive a que está no ar, mantêm seus itens congelados.")
        warning.setWordWrap(True)
        warning.setObjectName("notice")
        layout.addWidget(warning)

        self.tabs = QTabWidget()
        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        draft_row = QHBoxLayout()
        self.draft_selector = QComboBox()
        self.draft_selector.currentIndexChanged.connect(self._draft_selected)
        self.load_published_button = QPushButton("Recarregar grade publicada")
        self.load_published_button.clicked.connect(self.load_published)
        draft_row.addWidget(QLabel("Rascunho salvo"))
        draft_row.addWidget(self.draft_selector, 1)
        draft_row.addWidget(self.load_published_button)
        editor_layout.addLayout(draft_row)
        self.blocks_table = self._table(["Estado", "Hora", "Bloco", "Tipo", "Dias", "Itens"])
        self.blocks_table.itemSelectionChanged.connect(self._render_items)
        editor_layout.addWidget(self.blocks_table, 1)
        block_actions = QHBoxLayout()
        self.add_block_button = QPushButton("Novo bloco")
        self.add_block_button.clicked.connect(self.add_block_dialog)
        self.edit_block_button = QPushButton("Editar bloco")
        self.edit_block_button.clicked.connect(self.edit_block_dialog)
        self.enable_block_button = QPushButton("Ativar / pausar")
        self.enable_block_button.clicked.connect(self.toggle_block)
        self.remove_block_button = QPushButton("Remover do rascunho")
        self.remove_block_button.clicked.connect(self.remove_block)
        for button in (self.add_block_button, self.edit_block_button, self.enable_block_button, self.remove_block_button):
            button.setEnabled(self.can_edit)
            block_actions.addWidget(button)
        block_actions.addStretch(1)
        editor_layout.addLayout(block_actions)
        self.items_table = self._table(["Ordem", "Material", "Categoria", "Arquivo / referência"])
        self.items_table.itemSelectionChanged.connect(self._update_actions)
        editor_layout.addWidget(self.items_table, 1)
        item_actions = QHBoxLayout()
        self.add_item_button = QPushButton("Adicionar material")
        self.add_item_button.clicked.connect(self.add_item_dialog)
        self.add_folder_button = QPushButton("Adicionar pasta com seleção")
        self.add_folder_button.clicked.connect(self.add_folder_dialog)
        self.move_up_button = QPushButton("Subir")
        self.move_up_button.clicked.connect(lambda: self.move_item(-1))
        self.move_down_button = QPushButton("Descer")
        self.move_down_button.clicked.connect(lambda: self.move_item(1))
        self.remove_item_button = QPushButton("Remover do bloco")
        self.remove_item_button.clicked.connect(self.remove_item)
        for button in (self.add_item_button, self.add_folder_button, self.move_up_button, self.move_down_button, self.remove_item_button):
            button.setEnabled(self.can_edit)
            item_actions.addWidget(button)
        item_actions.addStretch(1)
        editor_layout.addLayout(item_actions)
        save_row = QHBoxLayout()
        self.dirty_label = QLabel("Sem alterações locais")
        self.save_draft_button = QPushButton("Salvar rascunho")
        self.save_draft_button.clicked.connect(self.save_draft_dialog)
        self.publish_button = QPushButton("Publicar programação")
        self.publish_button.clicked.connect(self.publish_draft)
        self.save_draft_button.setEnabled(self.can_edit)
        self.publish_button.setEnabled(self.can_edit)
        save_row.addWidget(self.dirty_label, 1)
        save_row.addWidget(self.save_draft_button)
        save_row.addWidget(self.publish_button)
        editor_layout.addLayout(save_row)
        self.tabs.addTab(editor, "Grade e rascunhos")

        self.occurrence_start = QDateEdit()
        self.occurrence_start.setCalendarPopup(True)
        self.occurrence_start.setDate(date.today())
        self.occurrence_end = QDateEdit()
        self.occurrence_end.setCalendarPopup(True)
        self.occurrence_end.setDate(date.today())
        self.occurrence_status = QComboBox()
        self.occurrence_status.addItem("Todas", "")
        for value, label in (("planned", "Programado"), ("running", "Em execução"), ("completed", "Concluído"), ("interrupted", "Interrompido"), ("failed", "Falhou"), ("skipped", "Ignorado"), ("stopped", "Parado")):
            self.occurrence_status.addItem(label, value)
        self.occurrence_table = self._table(["Horário", "Origem", "Material", "Categoria", "Situação", "Operador", "Início", "Fim", "Erro"])
        occurrence_panel = QWidget()
        occurrence_layout = QVBoxLayout(occurrence_panel)
        occurrence_filters = QHBoxLayout()
        self.refresh_occurrences_button = QPushButton("Consultar programação/execuções")
        self.refresh_occurrences_button.clicked.connect(self.refresh_occurrences)
        occurrence_filters.addWidget(QLabel("De"))
        occurrence_filters.addWidget(self.occurrence_start)
        occurrence_filters.addWidget(QLabel("Até"))
        occurrence_filters.addWidget(self.occurrence_end)
        occurrence_filters.addWidget(self.occurrence_status)
        occurrence_filters.addWidget(self.refresh_occurrences_button)
        occurrence_layout.addLayout(occurrence_filters)
        occurrence_layout.addWidget(self.occurrence_table, 1)
        self.tabs.addTab(occurrence_panel, "Ocorrências programadas")

        self.history_table = self._table(["Início real", "Fim real", "Material", "Artista", "Categoria", "Operador", "Situação", "Programado para", "Erro"])
        history_panel = QWidget()
        history_layout = QVBoxLayout(history_panel)
        self.refresh_history_button = QPushButton("Consultar histórico de execução")
        self.refresh_history_button.clicked.connect(self.refresh_history)
        history_layout.addWidget(self.refresh_history_button)
        history_layout.addWidget(self.history_table, 1)
        self.tabs.addTab(history_panel, "Histórico executado")
        layout.addWidget(self.tabs, 1)
        self.status = QLabel("Pronto")
        layout.addWidget(self.status)
        self.setCentralWidget(root)
        for name in ("edit_block_button", "enable_block_button", "remove_block_button", "add_item_button", "move_up_button", "move_down_button", "remove_item_button"):
            getattr(self, name).setEnabled(False)

    @staticmethod
    def _table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background:#111317; color:#e7e9ee; font-family:'Segoe UI'; font-size:10pt; }
            QLineEdit, QComboBox, QTimeEdit, QDateEdit { background:#191d24; border:1px solid #303642; border-radius:5px; padding:6px; }
            QTableWidget { background:#15181e; alternate-background-color:#191d24; gridline-color:#292e38; }
            QHeaderView::section { background:#1b1f27; color:#cbd0d9; padding:7px; border:0; }
            QPushButton { background:#252a34; border:1px solid #373e4b; border-radius:5px; padding:7px 11px; }
            QPushButton:hover { background:#303746; }
            QPushButton:disabled { color:#757b86; }
            QTabBar::tab { background:#1b1f27; padding:8px 14px; }
            QTabBar::tab:selected { background:#302c53; }
            QLabel#notice { background:#252318; color:#f4d58c; border:1px solid #645525; border-radius:6px; padding:9px; }
        """)

    def refresh_remote(self):
        if self.dirty and QMessageBox.question(self, "Descartar alterações locais?", "Atualizar do servidor descarta alterações ainda não salvas.") != QMessageBox.StandardButton.Yes:
            return
        try:
            identity = self.api.version()
            program = self.api.programs()
            self.materials = [item for item in self.api.materials()["materials"] if item.get("material_kind", "audio") == "audio"]
            self.folders = self.api.folders()["folders"]
            self.saved_revision = int(program["saved_revision"])
            self.saved_blocks = self._normalize_blocks(program["blocks"])
            self.identity_label.setText(f"Conectado: {identity.get('authenticated_as', 'usuário')} ({identity.get('role', 'reader')}) · {self.api.base_url}")
            self.revision_label.setText(f"Grade publicada · revisão {self.saved_revision} · {len(program.get('running', []))} ocorrência(s) no ar")
            self.drafts = self.api.drafts()["drafts"]
            self._render_drafts()
            self.draft_selector.setCurrentIndex(0)
            self._load_working(self.saved_blocks, None, "Grade publicada")
            self.refresh_occurrences()
            self.refresh_history()
            self.status.setText("Dados consultados do servidor central.")
        except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
            self.status.setText("Desconectado; use Atualizar servidor para tentar novamente.")
            QMessageBox.warning(self, "Servidor indisponível", str(exc))

    @staticmethod
    def _normalize_blocks(blocks: list[dict]) -> list[dict]:
        normalized = []
        for source in blocks:
            raw_items = source.get("items", [])
            if raw_items and isinstance(raw_items[0], dict):
                raw_items = sorted(raw_items, key=lambda item: (int(item.get("position", 0)), int(item.get("item_id", 0))))
            days = source.get("days", "0,1,2,3,4,5,6")
            day_values = [int(day) for day in days.split(",") if day.isdigit()] if isinstance(days, str) else list(days)
            normalized.append({
                "id": int(source["id"]) if source.get("id") is not None else None,
                "name": str(source.get("name", "")),
                "block_type": str(source.get("block_type", "Música")),
                "time_of_day": str(source.get("time_of_day", "00:00")),
                "days": day_values,
                "enabled": bool(source.get("enabled", True)),
                "items": [
                    ({"folder_id": int(item["folder_id"]), "selection_mode": str(item.get("selection_mode", "random"))}
                     if isinstance(item, dict) and item.get("folder_id") is not None
                     else (int(item["media_id"]) if item.get("media_id") is not None else None)) if isinstance(item, dict)
                    else (int(item) if item is not None else None)
                    for item in raw_items
                ],
            })
        return normalized

    @staticmethod
    def _payload(blocks: list[dict]) -> list[dict]:
        return [{key: copy.deepcopy(block[key]) for key in ("id", "name", "block_type", "time_of_day", "days", "enabled", "items")} for block in blocks]

    def _load_working(self, blocks: list[dict], draft: dict | None, label: str):
        self.blocks = copy.deepcopy(blocks)
        self.draft_id = int(draft["id"]) if draft else None
        self.draft_revision = int(draft["draft_revision"]) if draft else None
        self.base_revision = int(draft["base_revision"]) if draft else self.saved_revision
        self.draft_status = str(draft["status"]) if draft else "local"
        self.draft_name = str(draft["name"]) if draft else ""
        self.dirty = False
        self._render_blocks()
        self.dirty_label.setText(label + (f" · base v{self.base_revision}" if draft else ""))
        self._update_actions()

    def _render_drafts(self):
        selected = self.draft_selector.currentData()
        self.draft_selector.blockSignals(True)
        self.draft_selector.clear()
        self.draft_selector.addItem("Selecione um rascunho…", None)
        for draft in self.drafts:
            self.draft_selector.addItem(f"{draft['name']} · {draft['status']} · v{draft['base_revision']}", int(draft["id"]))
        index = self.draft_selector.findData(selected)
        self.draft_selector.setCurrentIndex(max(0, index))
        self.draft_selector.blockSignals(False)

    def _draft_selected(self, index: int):
        draft_id = self.draft_selector.itemData(index)
        if draft_id is None:
            return
        if self.dirty and QMessageBox.question(self, "Descartar alterações locais?", "Carregar outro rascunho descartará as mudanças ainda não salvas.") != QMessageBox.StandardButton.Yes:
            return
        try:
            draft = self.api.get_draft(int(draft_id))
            self._load_working(self._normalize_blocks(draft["payload"]["blocks"]), draft, f"{draft['name']} · {draft['status']}")
            self.status.setText("Rascunho carregado do servidor.")
        except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
            self.status.setText(str(exc))
            QMessageBox.warning(self, "Rascunho", str(exc))

    def load_published(self):
        if self.dirty and QMessageBox.question(self, "Descartar alterações locais?", "Recarregar a grade publicada descarta mudanças locais não salvas.") != QMessageBox.StandardButton.Yes:
            return
        try:
            program = self.api.programs()
            self.saved_revision = int(program["saved_revision"])
            self.saved_blocks = self._normalize_blocks(program["blocks"])
            self._load_working(self.saved_blocks, None, "Grade publicada carregada")
            self._render_drafts()
            self.draft_selector.setCurrentIndex(0)
            self.status.setText("Grade publicada recarregada; nenhum bloco em execução foi alterado.")
        except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Grade", str(exc))

    def _selected_block_index(self) -> int | None:
        row = self.blocks_table.currentRow()
        if row < 0 or row >= len(self.blocks):
            return None
        return row

    def _render_blocks(self):
        selected = self._selected_block_index()
        self.blocks_table.blockSignals(True)
        self.blocks_table.setRowCount(0)
        for index, block in enumerate(self.blocks):
            row = self.blocks_table.rowCount()
            self.blocks_table.insertRow(row)
            labels = (
                "Ativo" if block["enabled"] else "Pausado", block["time_of_day"], block["name"],
                block["block_type"], ", ".join(DAY_NAMES[day] for day in block["days"] if 0 <= day < 7),
                str(len(block["items"])),
            )
            for column, label in enumerate(labels):
                cell = QTableWidgetItem(label)
                cell.setData(ROLE, index)
                self.blocks_table.setItem(row, column, cell)
            if index == selected:
                self.blocks_table.selectRow(row)
        self.blocks_table.resizeColumnsToContents()
        self.blocks_table.blockSignals(False)
        self._render_items()
        self._update_actions()

    def _render_items(self):
        self.items_table.setRowCount(0)
        index = self._selected_block_index()
        if index is not None:
            block = self.blocks[index]
            materials_by_id = {int(item["id"]): item for item in self.materials}
            folders_by_id = {int(item["id"]): item for item in self.folders}
            for position, item in enumerate(block["items"]):
                if isinstance(item, dict):
                    folder = folders_by_id.get(int(item["folder_id"]))
                    folder_name = folder.get("name", "Pasta indisponível") if folder else f"Pasta {item['folder_id']} indisponível"
                    mode = {
                        "sequential": "Sequencial",
                        "random": "Aleatória sem repetição",
                        "jingle_rotation": "Rodízio equilibrado de vinhetas",
                    }.get(item["selection_mode"], "Modo indisponível")
                    labels = (str(position + 1), folder_name, f"Pasta · {mode}", str(folder.get("path", "—")) if folder else "—")
                else:
                    media_id = item
                    media = materials_by_id.get(media_id) if media_id is not None else None
                    labels = (
                        str(position + 1),
                        str(media.get("title", "Material indisponível")) if media else (f"Material {media_id} indisponível" if media_id else "Material sem ID"),
                        str(media.get("category", "—")) if media else "—",
                        str(media.get("path", "—")) if media else "—",
                    )
                row = self.items_table.rowCount()
                self.items_table.insertRow(row)
                for column, label in enumerate(labels):
                    cell = QTableWidgetItem(label)
                    cell.setData(ROLE, position)
                    self.items_table.setItem(row, column, cell)
        self.items_table.resizeColumnsToContents()
        self._update_actions()

    def _update_actions(self):
        editable = self.can_edit and self.draft_status in {"local", "draft", "published"}
        selected_block = self._selected_block_index() is not None
        selected_item = self.items_table.currentRow() >= 0
        for name, enabled in (
            ("add_block_button", editable),
            ("edit_block_button", editable and selected_block),
            ("enable_block_button", editable and selected_block),
            ("remove_block_button", editable and selected_block),
            ("add_item_button", editable and selected_block),
            ("add_folder_button", editable and selected_block),
            ("move_up_button", editable and selected_item),
            ("move_down_button", editable and selected_item),
            ("remove_item_button", editable and selected_item),
            ("save_draft_button", self.can_edit and self.dirty),
            ("publish_button", self.can_edit and self.draft_id is not None and self.draft_status == "draft" and not self.dirty),
        ):
            getattr(self, name).setEnabled(enabled)
        self.load_published_button.setEnabled(True)

    def _changed(self):
        self.dirty = True
        label = self.draft_name or "Rascunho local"
        self.dirty_label.setText(f"{label} · alterações não salvas")
        self._update_actions()

    def add_block_data(self, values: dict):
        self.blocks.append({"id": None, **copy.deepcopy(values), "items": []})
        self._changed()
        self._render_blocks()
        self.blocks_table.selectRow(len(self.blocks) - 1)

    def update_block_data(self, index: int, values: dict):
        self.blocks[index].update(copy.deepcopy(values))
        self._changed()
        self._render_blocks()
        self.blocks_table.selectRow(index)

    def add_material_to_block(self, media_id: int):
        index = self._selected_block_index()
        if index is None:
            raise ValueError("Selecione um bloco primeiro.")
        self.blocks[index]["items"].append(int(media_id))
        self._changed()
        self._render_items()

    def remove_item_at(self, position: int):
        index = self._selected_block_index()
        if index is None or not 0 <= position < len(self.blocks[index]["items"]):
            return
        self.blocks[index]["items"].pop(position)
        self._changed()
        self._render_items()

    def move_item_at(self, position: int, offset: int):
        index = self._selected_block_index()
        if index is None:
            return
        target = position + offset
        items = self.blocks[index]["items"]
        if not 0 <= position < len(items) or not 0 <= target < len(items):
            return
        items[position], items[target] = items[target], items[position]
        self._changed()
        self._render_items()
        self.items_table.selectRow(target)

    def add_block_dialog(self):
        dialog = BlockDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.add_block_data(dialog.values())

    def edit_block_dialog(self):
        index = self._selected_block_index()
        if index is None:
            return
        dialog = BlockDialog(self.blocks[index], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.update_block_data(index, dialog.values())

    def toggle_block(self):
        index = self._selected_block_index()
        if index is None:
            return
        values = {key: self.blocks[index][key] for key in ("name", "block_type", "time_of_day", "days")}
        values["enabled"] = not self.blocks[index]["enabled"]
        self.update_block_data(index, values)

    def remove_block(self):
        index = self._selected_block_index()
        if index is None:
            return
        if QMessageBox.question(self, "Remover bloco do rascunho", "O bloco será omitido no próximo publish e desativado; seu histórico não será apagado. Continuar?") != QMessageBox.StandardButton.Yes:
            return
        self.blocks.pop(index)
        self._changed()
        self._render_blocks()

    def add_item_dialog(self):
        index = self._selected_block_index()
        if index is None:
            return
        block_type = self.blocks[index]["block_type"]
        if block_type == "Comercial":
            candidates = [item for item in self.materials if item.get("category") in {"Comercial", "Vinheta"}]
        else:
            candidates = [item for item in self.materials if item.get("category") != "Comercial"]
        if not candidates:
            QMessageBox.information(self, "Programação", "Não há materiais ativos compatíveis com este tipo de bloco.")
            return
        labels = [f"{item['title']} · {item.get('category', '')} · {item.get('path', '')}" for item in candidates]
        selected, ok = QInputDialog.getItem(self, "Adicionar à sequência", "Material", labels, 0, False)
        if ok:
            self.add_material_to_block(int(candidates[labels.index(selected)]["id"]))

    def add_folder_dialog(self):
        index = self._selected_block_index()
        if index is None:
            return
        block_type = self.blocks[index]["block_type"]
        if block_type == "Comercial":
            candidates = [folder for folder in self.folders if folder.get("active", 1) and folder.get("category") in {"Comercial", "Vinheta"}]
        else:
            candidates = [folder for folder in self.folders if folder.get("active", 1) and folder.get("category") != "Comercial"]
        candidates = [folder for folder in candidates if int(folder.get("material_count", 0)) > 0]
        if not candidates:
            QMessageBox.information(self, "Programação", "Não há pastas com materiais compatíveis para este bloco.")
            return
        labels = [f"{folder['name']} · {folder.get('category') or 'Áudio'} · {folder.get('path', '')}" for folder in candidates]
        selected, ok = QInputDialog.getItem(self, "Adicionar pasta", "Pasta de seleção", labels, 0, False)
        if not ok:
            return
        folder = candidates[labels.index(selected)]
        mode_options = [
            ("Sequencial (ordem alfabética)", "sequential"),
            ("Aleatória equilibrada (baralho sem repetição até esgotar)", "random"),
        ]
        if folder.get("category") == "Vinheta" and folder.get("jingle_rotation_enabled"):
            mode_options.append(("Rodízio equilibrado de vinhetas (regra da pasta)", "jingle_rotation"))
        mode_labels = [label for label, _mode in mode_options]
        default_mode_index = next((index for index, (_label, mode) in enumerate(mode_options) if mode == "jingle_rotation"), 1)
        selected_mode, ok = QInputDialog.getItem(self, "Modo de seleção", "Como escolher os áudios?", mode_labels, default_mode_index, False)
        if not ok:
            return
        mode = mode_options[mode_labels.index(selected_mode)][1]
        self.add_folder_to_block(int(folder["id"]), mode)

    def add_folder_to_block(self, folder_id: int, selection_mode: str):
        index = self._selected_block_index()
        if index is None:
            raise ValueError("Selecione um bloco primeiro.")
        if selection_mode not in {"sequential", "random", "jingle_rotation"}:
            raise ValueError("Escolha um modo de seleção válido.")
        self.blocks[index]["items"].append({"folder_id": int(folder_id), "selection_mode": selection_mode})
        self._changed()
        self._render_items()

    def remove_item(self):
        row = self.items_table.currentRow()
        if row >= 0:
            self.remove_item_at(row)

    def move_item(self, offset: int):
        row = self.items_table.currentRow()
        if row >= 0:
            self.move_item_at(row, offset)

    def _validate_for_save(self):
        for index, block in enumerate(self.blocks, start=1):
            if any(item is None for item in block["items"]):
                raise ValueError(f"O bloco {index} contém item antigo sem media.id. Remova ou substitua esse item explicitamente antes de salvar.")
            for item in block["items"]:
                if isinstance(item, dict) and (item.get("selection_mode") not in {"sequential", "random", "jingle_rotation"} or not isinstance(item.get("folder_id"), int)):
                    raise ValueError(f"O bloco {index} contém um seletor de pasta inválido.")

    def save_draft(self, name: str):
        self._validate_for_save()
        payload = self._payload(self.blocks)
        if self.draft_id is not None and self.draft_status == "draft":
            result = self.api.update_draft(self.draft_id, int(self.draft_revision), payload)
            self.draft_revision = int(result["draft_revision"])
            self.draft_name = self.draft_name or name
        else:
            if not name.strip():
                raise ValueError("Informe um nome para o rascunho.")
            result = self.api.create_draft(name.strip(), self.base_revision, payload)
            self.draft_id = int(result["id"])
            self.draft_revision = int(result["draft_revision"])
            self.draft_name = name.strip()
            self.draft_status = "draft"
        self.dirty = False
        self.dirty_label.setText(f"{self.draft_name} · rascunho salvo · base v{self.base_revision}")
        self.drafts = self.api.drafts()["drafts"]
        self._render_drafts()
        draft_index = self.draft_selector.findData(self.draft_id)
        if draft_index >= 0:
            self.draft_selector.setCurrentIndex(draft_index)
        self._update_actions()
        return result

    def save_draft_dialog(self):
        name = self.draft_name
        if self.draft_id is None or self.draft_status != "draft":
            name, accepted = QInputDialog.getText(self, "Salvar rascunho", "Nome do rascunho:")
            if not accepted:
                return
        try:
            self.save_draft(name)
            self.status.setText(f"Rascunho salvo no servidor · {self.draft_name}.")
        except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Rascunho não salvo", f"{exc}\n\nEm conflito HTTP 409, atualize a grade e crie um novo rascunho a partir da revisão vigente.")
            self.status.setText(str(exc))

    def publish_draft(self, confirmed: bool = False):
        if self.draft_id is None or self.draft_status != "draft" or self.dirty:
            QMessageBox.information(self, "Publicação", "Salve as alterações em um rascunho antes de publicar.")
            return
        if any(block["enabled"] and not block["items"] for block in self.blocks):
            QMessageBox.warning(self, "Bloco sem sequência", "Todo bloco ativo precisa ter pelo menos um material antes da publicação.")
            return
        if confirmed:
            pass
        elif not any(block["enabled"] for block in self.blocks):
            if QMessageBox.question(self, "Grade sem blocos ativos", "Publicar esta grade desativará todos os blocos para futuras execuções. A execução atual permanece intacta. Confirmar?") != QMessageBox.StandardButton.Yes:
                return
        elif QMessageBox.question(
            self,
            "Publicar programação",
            "Publicar a revisão salva para execuções futuras? Ocorrências já iniciadas mantêm sua sequência e não serão interrompidas.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.api.publish_draft(self.draft_id, int(self.draft_revision))
            self.draft_status = "published"
            self.saved_revision = int(result["published_revision"])
            self.saved_blocks = self._normalize_blocks(self.api.programs()["blocks"])
            self.draft_id = None
            self.draft_revision = None
            self.draft_status = "local"
            self.draft_name = ""
            self._load_working(self.saved_blocks, None, "Grade publicada")
            self.drafts = self.api.drafts()["drafts"]
            self._render_drafts()
            self.draft_selector.setCurrentIndex(0)
            self.revision_label.setText(f"Grade publicada · revisão {self.saved_revision}")
            self.refresh_occurrences()
            self.status.setText(f"Programação publicada (revisão {self.saved_revision}); execução atual preservada.")
        except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Publicação recusada", f"{exc}\n\nO servidor mantém o rascunho e a programação vigente; atualize e reconcilie o conflito antes de tentar novamente.")
            self.status.setText(str(exc))

    def refresh_occurrences(self):
        try:
            start = self.occurrence_start.date().toString("yyyy-MM-dd")
            end = self.occurrence_end.date().toString("yyyy-MM-dd")
            status = self.occurrence_status.currentData()
            occurrences = self.api.occurrences(start, end, status, limit=500)["occurrences"]
            self.occurrence_table.setRowCount(0)
            for occurrence in occurrences:
                for item in occurrence.get("items", []) or [{"title": "—", "category": "—", "status": occurrence["status"]}]:
                    row = self.occurrence_table.rowCount()
                    self.occurrence_table.insertRow(row)
                    values = (
                        occurrence["scheduled_for"],
                        occurrence.get("origin_type", "") + (f" · bloco {occurrence['block_id']}" if occurrence.get("block_id") else ""),
                        item.get("title", ""), item.get("category", ""), item.get("status", occurrence["status"]),
                        occurrence.get("operator", ""), item.get("started_at", ""), item.get("ended_at", ""), item.get("error_message", ""),
                    )
                    for column, value in enumerate(values):
                        self.occurrence_table.setItem(row, column, QTableWidgetItem(str(value or "")))
            self.occurrence_table.resizeColumnsToContents()
            self.status.setText(f"{len(occurrences)} ocorrência(s) consultada(s); isso não representa horários futuros ainda não disparados.")
        except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
            self.status.setText(f"Falha ao consultar ocorrências: {exc}")

    def refresh_history(self):
        try:
            history = self.api.history(limit=500)["history"]
            self.history_table.setRowCount(0)
            for entry in history:
                row = self.history_table.rowCount()
                self.history_table.insertRow(row)
                values = (entry.get("started_at"), entry.get("ended_at"), entry.get("title"), entry.get("artist"), entry.get("category"), entry.get("operator"), entry.get("status"), entry.get("planned_for"), entry.get("error_message"))
                for column, value in enumerate(values):
                    self.history_table.setItem(row, column, QTableWidgetItem(str(value or "")))
            self.history_table.resizeColumnsToContents()
        except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
            self.status.setText(f"Falha ao consultar histórico: {exc}")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("HitMo Programação Remota")
    dialog = ConnectionDialog()
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.client is None or dialog.identity is None:
        return 0
    window = ProgrammingManagerWindow(dialog.client, dialog.identity)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
