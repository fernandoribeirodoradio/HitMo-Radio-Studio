from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QTimeEdit, QVBoxLayout,
)

from .database import Database
from .services import MediaLibrary, ProgrammingService

ROLE = Qt.ItemDataRole.UserRole


class _BlockDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Novo bloco programado")
        self.name = QComboBox()
        self.name.setEditable(True)
        self.name.addItems(["Bloco musical", "Bloco comercial"])
        self.block_type = QComboBox()
        self.block_type.addItems(["Música", "Comercial"])
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        self.days = []
        day_row = QHBoxLayout()
        for day in ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"):
            check = QCheckBox(day)
            check.setChecked(True)
            self.days.append(check)
            day_row.addWidget(check)
        form = QFormLayout()
        form.addRow("Nome", self.name)
        form.addRow("Tipo", self.block_type)
        form.addRow("Horário de início", self.time)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Dias de recorrência"))
        layout.addLayout(day_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return (
            self.name.currentText().strip(),
            self.block_type.currentText(),
            self.time.time().toString("HH:mm"),
            [i for i, day in enumerate(self.days) if day.isChecked()],
        )


class ProgramManagerDialog(QDialog):
    """Editor for recurring music/commercial blocks and their ordered media items."""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.service = ProgrammingService(db)
        self.library = MediaLibrary(db)
        self.setWindowTitle("Programação · Blocos musicais e comerciais")
        self.resize(980, 600)
        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        right = QVBoxLayout()
        layout.addLayout(left, 1)
        layout.addLayout(right, 2)
        self.blocks = QTableWidget(0, 5)
        self.blocks.setHorizontalHeaderLabels(["Ativo", "Hora", "Bloco", "Tipo", "Dias"])
        self.blocks.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.blocks.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.blocks.horizontalHeader().setStretchLastSection(True)
        self.blocks.itemSelectionChanged.connect(self.refresh_items)
        left.addWidget(self.blocks, 1)
        block_actions = QHBoxLayout()
        left.addLayout(block_actions)
        for label, callback in (("Novo bloco", self.add_block), ("Ativar/pausar", self.toggle_block), ("Remover bloco", self.remove_block)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            block_actions.addWidget(button)
        self.items = QTableWidget(0, 4)
        self.items.setHorizontalHeaderLabels(["Ordem", "Material", "Categoria", "Arquivo"])
        self.items.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.items.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.items.horizontalHeader().setStretchLastSection(True)
        right.addWidget(self.items, 1)
        item_actions = QHBoxLayout()
        right.addLayout(item_actions)
        for label, callback in (("Adicionar material", self.add_item), ("Subir", lambda: self.move_item(-1)), ("Descer", lambda: self.move_item(1)), ("Remover do bloco", self.remove_item)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            item_actions.addWidget(button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        right.addWidget(buttons)
        self.refresh_blocks()

    def _selected_block(self):
        row = self.blocks.currentRow()
        if row < 0 or not self.blocks.item(row, 0):
            return None
        return int(self.blocks.item(row, 0).data(ROLE))

    def refresh_blocks(self):
        selected = self._selected_block()
        self.blocks.blockSignals(True)
        self.blocks.setRowCount(0)
        selected_row = -1
        for block in self.service.blocks():
            row = self.blocks.rowCount()
            self.blocks.insertRow(row)
            labels = ("Ativo" if block["enabled"] else "Pausado", block["time_of_day"], block["name"], block["block_type"], block["days"])
            for col, label in enumerate(labels):
                cell = QTableWidgetItem(str(label))
                cell.setData(ROLE, int(block["id"]))
                self.blocks.setItem(row, col, cell)
            if int(block["id"]) == selected:
                selected_row = row
        self.blocks.resizeColumnsToContents()
        if selected_row >= 0:
            self.blocks.selectRow(selected_row)
        self.blocks.blockSignals(False)
        self.refresh_items()

    def refresh_items(self):
        self.items.setRowCount(0)
        block_id = self._selected_block()
        if not block_id:
            return
        for item in self.service.block_items(block_id):
            row = self.items.rowCount()
            self.items.insertRow(row)
            labels = (str(item["position"] + 1), item["title"] or item["label"] or "Material indisponível", item["category"] or "—", item["path"] or "—")
            for col, label in enumerate(labels):
                cell = QTableWidgetItem(label)
                cell.setData(ROLE, int(item["block_item_id"]))
                self.items.setItem(row, col, cell)
        self.items.resizeColumnsToContents()

    def add_block(self):
        dialog = _BlockDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.service.add_block(*dialog.values())
            self.refresh_blocks()
        except Exception as exc:
            QMessageBox.warning(self, "Programação", str(exc))

    def toggle_block(self):
        block_id = self._selected_block()
        if not block_id:
            return
        row = self.db.one("SELECT enabled FROM program_blocks WHERE id=?", (block_id,))
        self.service.set_block_enabled(block_id, not bool(row["enabled"]))
        self.refresh_blocks()

    def remove_block(self):
        block_id = self._selected_block()
        if block_id:
            self.service.remove_block(block_id)
            self.refresh_blocks()

    def add_item(self):
        block_id = self._selected_block()
        if not block_id:
            return
        rows = self.db.all(
            "SELECT m.id,m.title,m.artist,m.category,m.path,COALESCE(c.material_kind,'audio') AS material_kind FROM media m LEFT JOIN material_categories c ON c.name=m.category WHERE COALESCE(c.material_kind,'audio')='audio' ORDER BY m.title COLLATE NOCASE"
        )
        if not rows:
            QMessageBox.information(self, "Programação", "Cadastre ou importe materiais de áudio no Config Manager primeiro.")
            return
        labels = [f"{row['title']} · {row['category']} · {row['path']}" for row in rows]
        selected, ok = QInputDialog.getItem(self, "Adicionar material", "Material", labels, 0, False)
        if not ok:
            return
        try:
            self.service.add_block_item(block_id, int(rows[labels.index(selected)]["id"]))
            self.refresh_items()
        except Exception as exc:
            QMessageBox.warning(self, "Programação", str(exc))

    def remove_item(self):
        row = self.items.currentRow()
        if row < 0:
            return
        self.service.remove_block_item(int(self.items.item(row, 0).data(ROLE)))
        self.refresh_items()

    def move_item(self, offset: int):
        block_id = self._selected_block()
        row = self.items.currentRow()
        if not block_id or row < 0:
            return
        items = self.service.block_items(block_id)
        target = row + offset
        if not 0 <= target < len(items):
            return
        ids = [int(item["block_item_id"]) for item in items]
        ids[row], ids[target] = ids[target], ids[row]
        self.service.reorder_block_items(block_id, ids)
        self.refresh_items()
        self.items.selectRow(target)
