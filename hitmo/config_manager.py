from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .database import Database
from .services import MediaLibrary

ROLE = Qt.ItemDataRole.UserRole


class _CategoryDialog(QDialog):
    def __init__(self, categories, parent=None, record=None):
        super().__init__(parent)
        self.setWindowTitle("Categoria de material")
        self.name = QLineEdit(record["name"] if record else "")
        self.kind = QComboBox()
        self.kind.addItem("Áudio", "audio")
        self.kind.addItem("Texto/documento", "text")
        self.kind.addItem("Arquivo", "file")
        self.icon = QLineEdit(record["icon_path"] if record else "")
        browse = QPushButton("Escolher ícone…")
        browse.clicked.connect(self._browse_icon)
        row = QHBoxLayout()
        row.addWidget(self.icon, 1)
        row.addWidget(browse)
        form = QFormLayout()
        form.addRow("Nome", self.name)
        form.addRow("Tipo de material", self.kind)
        form.addRow("Ícone", row)
        if record:
            self.kind.setCurrentIndex(max(0, self.kind.findData(record["material_kind"])))
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_icon(self):
        path, _ = QFileDialog.getOpenFileName(self, "Ícone da categoria", "", "Imagens (*.png *.ico *.svg *.jpg *.jpeg);;Todos os arquivos (*)")
        if path:
            self.icon.setText(path)


class _FolderDialog(QDialog):
    def __init__(self, library: MediaLibrary, parent=None, record=None):
        super().__init__(parent)
        self.library = library
        self.setWindowTitle("Pasta de materiais")
        self.name = QLineEdit(record["name"] if record else "")
        self.path = QLineEdit(record["path"] if record else "")
        browse = QPushButton("Selecionar diretório…")
        browse.clicked.connect(self._browse_folder)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path, 1)
        path_row.addWidget(browse)
        self.category = QComboBox()
        self.category.addItem("Sem categoria", None)
        for category in library.categories():
            self.category.addItem(category["name"], int(category["id"]))
        self.icon = QLineEdit(record["icon_path"] if record else "")
        icon_button = QPushButton("Ícone…")
        icon_button.clicked.connect(self._browse_icon)
        icon_row = QHBoxLayout()
        icon_row.addWidget(self.icon, 1)
        icon_row.addWidget(icon_button)
        form = QFormLayout()
        form.addRow("Nome", self.name)
        form.addRow("Diretório", path_row)
        form.addRow("Categoria", self.category)
        form.addRow("Ícone", icon_row)
        if record:
            self.category.setCurrentIndex(max(0, self.category.findData(record["category_id"])))
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Selecionar diretório", self.path.text())
        if path:
            self.path.setText(path)
            if not self.name.text().strip():
                self.name.setText(Path(path).name)

    def _browse_icon(self):
        path, _ = QFileDialog.getOpenFileName(self, "Ícone da pasta", "", "Imagens (*.png *.ico *.svg *.jpg *.jpeg);;Todos os arquivos (*)")
        if path:
            self.icon.setText(path)


class _MaterialDialog(QDialog):
    def __init__(self, library: MediaLibrary, record, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Propriedades do material")
        self.record = record
        self.title = QLineEdit(record["title"] or "")
        self.artist = QLineEdit(record["artist"] or "")
        self.category = QComboBox()
        for category in library.categories():
            self.category.addItem(category["name"], category["name"])
        idx = self.category.findData(record["category"])
        self.category.setCurrentIndex(max(0, idx))
        self.description = QLineEdit(record["description"] or "")
        self.icon = QLineEdit(record["icon_path"] or "")
        browse = QPushButton("Ícone…")
        browse.clicked.connect(self._browse_icon)
        icon_row = QHBoxLayout()
        icon_row.addWidget(self.icon, 1)
        icon_row.addWidget(browse)
        form = QFormLayout()
        form.addRow("Título", self.title)
        form.addRow("Artista/descrição curta", self.artist)
        form.addRow("Categoria", self.category)
        form.addRow("Propriedades/notas", self.description)
        form.addRow("Ícone", icon_row)
        form.addRow("Arquivo original", QLabel(record["path"]))
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_icon(self):
        path, _ = QFileDialog.getOpenFileName(self, "Ícone do material", "", "Imagens (*.png *.ico *.svg *.jpg *.jpeg);;Todos os arquivos (*)")
        if path:
            self.icon.setText(path)


class ConfigManagerDialog(QDialog):
    """Independent catalogue manager for material categories, roots, files and icons."""

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self.library = MediaLibrary(db)
        self.setWindowTitle("Config Manager · Cadastro de materiais")
        self.resize(1000, 650)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self._build_categories_tab()
        self._build_folders_tab()
        self._build_materials_tab()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self.refresh()

    def _make_tab(self, title: str, headers: list[str]):
        page = QWidget()
        layout = QVBoxLayout(page)
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table, 1)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        self.tabs.addTab(page, title)
        return table, buttons

    def _build_categories_tab(self):
        self.categories_table, buttons = self._make_tab("Categorias", ["Categoria", "Tipo", "Ícone", "Estado"])
        for label, callback in (("Nova categoria", self._add_category), ("Editar", self._edit_category), ("Ativar/desativar", self._deactivate_category)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        buttons.addStretch(1)

    def _build_folders_tab(self):
        self.folders_table, buttons = self._make_tab("Pastas e diretórios", ["Nome", "Categoria", "Diretório", "Ícone", "Estado"])
        for label, callback in (("Adicionar pasta", self._add_folder), ("Editar", self._edit_folder), ("Importar/atualizar arquivos", self._scan_folder), ("Ativar/desativar pasta", self._deactivate_folder)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        buttons.addStretch(1)

    def _build_materials_tab(self):
        self.materials_table, buttons = self._make_tab("Arquivos e propriedades", ["Material", "Categoria", "Duração", "Diretório", "Arquivo"])
        edit = QPushButton("Propriedades…")
        edit.clicked.connect(self._edit_material)
        refresh = QPushButton("Atualizar lista")
        refresh.clicked.connect(self.refresh)
        buttons.addWidget(edit)
        buttons.addWidget(refresh)
        buttons.addStretch(1)

    def refresh(self):
        self.categories_table.setRowCount(0)
        for row in self.library.categories(active_only=False):
            index = self.categories_table.rowCount()
            self.categories_table.insertRow(index)
            values = (row["name"], row["material_kind"], row["icon_path"], "Ativa" if row["active"] else "Inativa")
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setData(ROLE, int(row["id"]))
                if column == 0 and row["icon_path"] and Path(row["icon_path"]).is_file():
                    cell.setIcon(QIcon(row["icon_path"]))
                self.categories_table.setItem(index, column, cell)
        self.folders_table.setRowCount(0)
        for row in self.library.folders(active_only=False):
            index = self.folders_table.rowCount()
            self.folders_table.insertRow(index)
            values = (row["name"], row["category"] or "—", row["path"], row["icon_path"], "Ativa" if row["active"] else "Inativa")
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setData(ROLE, int(row["id"]))
                if column == 0 and row["icon_path"] and Path(row["icon_path"]).is_file():
                    cell.setIcon(QIcon(row["icon_path"]))
                self.folders_table.setItem(index, column, cell)
        self.materials_table.setRowCount(0)
        for row in self.db.all("SELECT * FROM media ORDER BY title COLLATE NOCASE"):
            index = self.materials_table.rowCount()
            self.materials_table.insertRow(index)
            values = (row["title"], row["category"], f"{float(row['duration']):.1f}s", str(Path(row["path"]).parent), Path(row["path"]).name)
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setData(ROLE, int(row["id"]))
                if column == 0 and row["icon_path"] and Path(row["icon_path"]).is_file():
                    cell.setIcon(QIcon(row["icon_path"]))
                self.materials_table.setItem(index, column, cell)
        for table in (self.categories_table, self.folders_table, self.materials_table):
            table.resizeColumnsToContents()

    @staticmethod
    def _selected_id(table: QTableWidget):
        row = table.currentRow()
        return int(table.item(row, 0).data(ROLE)) if row >= 0 and table.item(row, 0) else None

    def _add_category(self):
        dialog = _CategoryDialog(self.library.categories(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.library.save_category(dialog.name.text(), str(dialog.kind.currentData()), dialog.icon.text())
                self.refresh()
            except Exception as exc:
                QMessageBox.warning(self, "Categoria", str(exc))

    def _edit_category(self):
        ident = self._selected_id(self.categories_table)
        if not ident:
            return
        record = self.db.one("SELECT * FROM material_categories WHERE id=?", (ident,))
        dialog = _CategoryDialog(self.library.categories(), self, record)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.library.save_category(dialog.name.text(), str(dialog.kind.currentData()), dialog.icon.text(), ident)
                self.refresh()
            except Exception as exc:
                QMessageBox.warning(self, "Categoria", str(exc))

    def _deactivate_category(self):
        ident = self._selected_id(self.categories_table)
        if not ident:
            return
        try:
            record = self.db.one("SELECT active FROM material_categories WHERE id=?", (ident,))
            self.library.set_category_enabled(ident, not bool(record["active"]))
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Categoria", str(exc))

    def _add_folder(self):
        dialog = _FolderDialog(self.library, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                ident = self.library.save_folder(dialog.name.text(), dialog.path.text(), dialog.category.currentData(), dialog.icon.text())
                self._scan_folder_id(ident)
                self.refresh()
            except Exception as exc:
                QMessageBox.warning(self, "Pasta", str(exc))

    def _edit_folder(self):
        ident = self._selected_id(self.folders_table)
        if not ident:
            return
        record = self.db.one("SELECT * FROM media_folders WHERE id=?", (ident,))
        dialog = _FolderDialog(self.library, self, record)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.library.save_folder(dialog.name.text(), dialog.path.text(), dialog.category.currentData(), dialog.icon.text(), ident)
                self._scan_folder_id(ident)
                self.refresh()
            except Exception as exc:
                QMessageBox.warning(self, "Pasta", str(exc))

    def _scan_folder_id(self, folder_id: int):
        folder = self.db.one("SELECT f.*,c.name AS category,c.material_kind FROM media_folders f LEFT JOIN material_categories c ON c.id=f.category_id WHERE f.id=? AND f.active=1", (folder_id,))
        if folder:
            self.library.import_paths([folder["path"]], category=folder["category"], folder_id=folder_id, include_non_audio=folder["material_kind"] in {"text", "file"})

    def _scan_folder(self):
        ident = self._selected_id(self.folders_table)
        if not ident:
            return
        try:
            self._scan_folder_id(ident)
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Importação", str(exc))

    def _deactivate_folder(self):
        ident = self._selected_id(self.folders_table)
        if ident:
            row = self.db.one("SELECT active FROM media_folders WHERE id=?", (ident,))
            self.library.set_folder_enabled(ident, not bool(row["active"]))
            self.refresh()

    def _edit_material(self):
        ident = self._selected_id(self.materials_table)
        if not ident:
            return
        record = self.db.one("SELECT * FROM media WHERE id=?", (ident,))
        if not record:
            return
        dialog = _MaterialDialog(self.library, record, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.library.update_material(ident, title=dialog.title.text(), artist=dialog.artist.text(), category=str(dialog.category.currentData()), description=dialog.description.text(), icon_path=dialog.icon.text())
                self.refresh()
            except Exception as exc:
                QMessageBox.warning(self, "Material", str(exc))
