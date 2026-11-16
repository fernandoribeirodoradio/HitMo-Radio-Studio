from __future__ import annotations

import sys
import uuid
from pathlib import Path

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSpinBox, QDoubleSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from .api_client import HitMoAPIClient
from .database import default_data_dir
from .shortcuts import reconcile_shortcuts

ROLE = Qt.ItemDataRole.UserRole
APP_SETTINGS = QSettings("HitMo Radio Studio", "ConfigManager")


class ConnectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Conectar ao catálogo central HitMo")
        self.setMinimumWidth(470)
        self.host = QLineEdit(APP_SETTINGS.value("host", "127.0.0.1"))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(APP_SETTINGS.value("port", 8765)))
        self.tls = QCheckBox("Usar HTTPS/TLS")
        self.tls.setChecked(str(APP_SETTINGS.value("tls", "false")).casefold() in {"true", "1", "yes"})
        self.ca_file = QLineEdit(APP_SETTINGS.value("ca_file", ""))
        ca_browse = QPushButton("Certificado CA…")
        ca_browse.clicked.connect(self._browse_ca)
        ca_row = QHBoxLayout()
        ca_row.addWidget(self.ca_file, 1)
        ca_row.addWidget(ca_browse)
        self.username = QLineEdit(APP_SETTINGS.value("username", ""))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form = QFormLayout()
        form.addRow("Servidor / IP", self.host)
        form.addRow("Porta", self.port)
        form.addRow("Transporte", self.tls)
        form.addRow("CA privada (opcional)", ca_row)
        form.addRow("Usuário", self.username)
        form.addRow("Senha", self.password)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Conecte ao serviço central. A senha não é salva nesta estação."))
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._connect)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.client: HitMoAPIClient | None = None
        self.identity: dict | None = None

    def _browse_ca(self):
        path, _ = QFileDialog.getOpenFileName(self, "Certificado da autoridade certificadora", "", "Certificados (*.pem *.crt *.cer);;Todos os arquivos (*)")
        if path:
            self.ca_file.setText(path)

    def _connect(self):
        host = self.host.text().strip()
        if not host or any(char.isspace() for char in host) or "/" in host:
            QMessageBox.warning(self, "Servidor inválido", "Informe um nome de host ou endereço IP sem protocolo ou caminho.")
            return
        if not self.username.text().strip() or not self.password.text():
            QMessageBox.warning(self, "Credenciais", "Informe usuário e senha.")
            return
        scheme = "https" if self.tls.isChecked() else "http"
        url = f"{scheme}://{host}:{self.port.value()}"
        try:
            client = HitMoAPIClient(url, self.username.text().strip(), self.password.text(), timeout=4.0, ca_file=self.ca_file.text().strip() or None)
            identity = client.version()
        except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Falha na conexão", str(exc))
            return
        self.client = client
        self.identity = identity
        APP_SETTINGS.setValue("host", host)
        APP_SETTINGS.setValue("port", self.port.value())
        APP_SETTINGS.setValue("tls", self.tls.isChecked())
        APP_SETTINGS.setValue("ca_file", self.ca_file.text().strip())
        APP_SETTINGS.setValue("username", self.username.text().strip())
        self.accept()


class MaterialDialog(QDialog):
    def __init__(self, categories: list[dict], folders: list[dict], record: dict | None = None, parent=None):
        super().__init__(parent)
        self.record = record
        self.categories = categories
        self.setWindowTitle("Editar material" if record else "Cadastrar material")
        self.setMinimumWidth(600)
        self.title = QLineEdit(str(record.get("title", "")) if record else "")
        self.artist = QLineEdit(str(record.get("artist", "")) if record else "")
        self.category = QComboBox()
        for category in categories:
            icon_path = str(category.get("icon_path", ""))
            icon = QIcon(icon_path) if icon_path and Path(icon_path).is_file() else QIcon()
            self.category.addItem(icon, category["name"], category)
        current_category = str(record.get("category", "")) if record else ""
        index = self.category.findText(current_category)
        if index >= 0:
            self.category.setCurrentIndex(index)
        self.folder = QComboBox()
        self.folder.addItem("Sem pasta", None)
        for folder in folders:
            label = folder["name"] + (" · inativa" if not folder["active"] else "")
            self.folder.addItem(label, int(folder["id"]))
        folder_index = self.folder.findData(record.get("folder_id") if record else None)
        if folder_index >= 0:
            self.folder.setCurrentIndex(folder_index)
        self.kind = QLabel()
        self.path = QLineEdit(str(record.get("path", "")) if record else "")
        browse = QPushButton("Selecionar arquivo…")
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path, 1)
        path_row.addWidget(browse)
        self.duration = QDoubleSpinBox()
        self.duration.setRange(0, 86400)
        self.duration.setDecimals(2)
        self.duration.setSuffix(" s")
        if record:
            self.duration.setValue(float(record.get("duration", 0)))
        self.description = QLineEdit(str(record.get("description", "")) if record else "")
        self.text_content = QTextEdit(str(record.get("text_content", "")) if record else "")
        self.text_content.setMaximumHeight(130)
        self.icon = QLineEdit(str(record.get("icon_path", "")) if record else "")
        icon_button = QPushButton("Ícone…")
        icon_button.clicked.connect(self._browse_icon)
        icon_row = QHBoxLayout()
        icon_row.addWidget(self.icon, 1)
        icon_row.addWidget(icon_button)
        form = QFormLayout()
        form.addRow("Nome", self.title)
        form.addRow("Artista / responsável", self.artist)
        form.addRow("Categoria", self.category)
        form.addRow("Pasta do catálogo", self.folder)
        form.addRow("Tipo definido no catálogo", self.kind)
        form.addRow("Arquivo ou caminho compartilhado", path_row)
        form.addRow("Duração", self.duration)
        form.addRow("Descrição", self.description)
        form.addRow("Texto associado", self.text_content)
        form.addRow("Ícone próprio", icon_row)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        self.category.currentIndexChanged.connect(self._update_kind)
        self._update_kind()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _category_data(self) -> dict:
        return self.category.currentData() or {"material_kind": "audio"}

    def _update_kind(self):
        kind = str(self._category_data().get("material_kind", "audio"))
        self.kind.setText({"audio": "Áudio executável", "text": "Texto / documento", "file": "Arquivo"}.get(kind, kind))
        self.text_content.setEnabled(kind == "text")
        self.duration.setEnabled(kind == "audio")

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Selecionar material", self.path.text(), "Todos os arquivos (*.*)")
        if path:
            self.path.setText(path)

    def _browse_icon(self):
        path, _ = QFileDialog.getOpenFileName(self, "Selecionar ícone", self.icon.text(), "Imagens (*.png *.ico *.svg *.jpg *.jpeg);;Todos os arquivos (*)")
        if path:
            self.icon.setText(path)

    def _validate(self):
        if not self.title.text().strip():
            QMessageBox.warning(self, "Nome obrigatório", "Informe o nome do material.")
            return
        if self._category_data().get("material_kind") == "audio" and not self.path.text().strip():
            QMessageBox.warning(self, "Caminho obrigatório", "Materiais sonoros precisam de um caminho de arquivo.")
            return
        self.accept()

    def values(self) -> dict:
        kind = str(self._category_data().get("material_kind", "audio"))
        path = self.path.text().strip()
        if not path and kind != "audio":
            path = f"hitmo://{'text' if kind == 'text' else 'material'}/{uuid.uuid4().hex}"
        return {
            "path": path,
            "title": self.title.text().strip(),
            "artist": self.artist.text().strip(),
            "category": self.category.currentText(),
            "folder_id": self.folder.currentData(),
            "duration": self.duration.value() if kind == "audio" else 0,
            "description": self.description.text().strip(),
            "text_content": self.text_content.toPlainText() if kind == "text" else "",
            "icon_path": self.icon.text().strip(),
        }


class CategoryDialog(QDialog):
    def __init__(self, record: dict | None = None, parent=None):
        super().__init__(parent)
        self.record = record or {}
        self.setWindowTitle("Editar categoria" if record else "Nova categoria")
        self.name = QLineEdit(str(self.record.get("name", "")))
        self.kind = QComboBox()
        for key, label in (("audio", "Áudio"), ("text", "Texto"), ("file", "Arquivo")):
            self.kind.addItem(label, key)
        index = self.kind.findData(self.record.get("material_kind", "audio"))
        self.kind.setCurrentIndex(max(0, index))
        self.icon = QLineEdit(str(self.record.get("icon_path", "")))
        browse = QPushButton("Ícone compartilhado…")
        browse.clicked.connect(self._browse_icon)
        icon_row = QHBoxLayout()
        icon_row.addWidget(self.icon, 1)
        icon_row.addWidget(browse)
        form = QFormLayout(self)
        form.addRow("Nome", self.name)
        form.addRow("Tipo de material", self.kind)
        form.addRow("Caminho do ícone", icon_row)
        self.kind.setToolTip("Mudar audio/text/file para uma categoria já usada é recusado para preservar o comportamento dos materiais.")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse_icon(self):
        path, _ = QFileDialog.getOpenFileName(self, "Selecionar ícone compartilhado", self.icon.text(), "Imagens (*.png *.ico *.svg *.jpg *.jpeg);;Todos os arquivos (*)")
        if path:
            self.icon.setText(path)

    def _accept_if_valid(self):
        if not self.name.text().strip():
            QMessageBox.warning(self, "Nome obrigatório", "Informe o nome da categoria.")
            return
        self.accept()

    def values(self) -> dict:
        return {"name": self.name.text().strip(), "material_kind": self.kind.currentData(), "icon_path": self.icon.text().strip()}


class FolderDialog(QDialog):
    def __init__(self, categories: list[dict], record: dict | None = None, parent=None):
        super().__init__(parent)
        self.record = record or {}
        self.setWindowTitle("Editar pasta" if record else "Nova pasta")
        self.name = QLineEdit(str(self.record.get("name", "")))
        self.path = QLineEdit(str(self.record.get("path", "")))
        self.path.setPlaceholderText(r"Caminho local do servidor ou UNC: \\servidor\acervo\musicas")
        self.category = QComboBox()
        self.category.addItem("Sem categoria", None)
        for category in categories:
            label = category["name"] + (" · inativa" if not category.get("active", 1) else "")
            self.category.addItem(label, int(category["id"]))
        index = self.category.findData(self.record.get("category_id"))
        self.category.setCurrentIndex(max(0, index))
        self.icon = QLineEdit(str(self.record.get("icon_path", "")))
        browse = QPushButton("Ícone compartilhado…")
        browse.clicked.connect(self._browse_icon)
        icon_row = QHBoxLayout()
        icon_row.addWidget(self.icon, 1)
        icon_row.addWidget(browse)
        form = QFormLayout(self)
        form.addRow("Nome da pasta", self.name)
        form.addRow("Diretório visível ao servidor", self.path)
        form.addRow("Categoria", self.category)
        form.addRow("Caminho do ícone", icon_row)
        form.addRow(QLabel("O servidor verifica se o diretório existe. Use UNC para um compartilhamento de rede; o cadastro preserva o caminho se estiver indisponível."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse_icon(self):
        path, _ = QFileDialog.getOpenFileName(self, "Selecionar ícone compartilhado", self.icon.text(), "Imagens (*.png *.ico *.svg *.jpg *.jpeg);;Todos os arquivos (*)")
        if path:
            self.icon.setText(path)

    def _accept_if_valid(self):
        if not self.name.text().strip() or not self.path.text().strip():
            QMessageBox.warning(self, "Campos obrigatórios", "Informe o nome e o diretório da pasta.")
            return
        self.accept()

    def values(self) -> dict:
        return {"name": self.name.text().strip(), "path": self.path.text().strip(), "category_id": self.category.currentData(), "icon_path": self.icon.text().strip()}


class ConfigManagerWindow(QMainWindow):
    def __init__(self, api: HitMoAPIClient, identity: dict, shortcuts_dir: str | Path | None = None, parent=None):
        super().__init__(parent)
        self.api = api
        self.identity = identity
        self.shortcuts_dir = Path(shortcuts_dir) if shortcuts_dir else default_data_dir() / "Atalhos"
        self.categories: list[dict] = []
        self.folders: list[dict] = []
        self.materials: list[dict] = []
        self.references: list[dict] = []
        self.revision = int(identity.get("catalog_revision", 0))
        self.can_edit = identity.get("role", "reader") in {"admin", "editor"}
        self.setWindowTitle("HitMo ConfigManager — Catálogo central")
        self.resize(1280, 760)
        self._build_ui()
        self._apply_theme()
        self.refresh()

    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        header = QHBoxLayout()
        self.identity_label = QLabel(f"Conectado: {self.identity.get('authenticated_as', 'usuário')} · {self.api.base_url}")
        self.revision_label = QLabel(f"Revisão {self.revision}")
        header.addWidget(self.identity_label, 1)
        header.addWidget(self.revision_label)
        self.refresh_button = QPushButton("Atualizar catálogo")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Pesquisar por nome, artista ou caminho…")
        self.search.textChanged.connect(self._render_materials)
        self.category_filter = QComboBox()
        self.category_filter.currentIndexChanged.connect(self._render_materials)
        self.folder_filter = QComboBox()
        self.folder_filter.currentIndexChanged.connect(self._render_materials)
        self.state_filter = QComboBox()
        self.state_filter.addItem("Ativos", "active")
        self.state_filter.addItem("Inativos", "inactive")
        self.state_filter.addItem("Todos", "all")
        self.state_filter.currentIndexChanged.connect(self._render_materials)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.category_filter)
        filters.addWidget(self.folder_filter)
        filters.addWidget(self.state_filter)
        layout.addLayout(filters)

        self.tabs = QTabWidget()
        self.material_table = self._table(["ID", "Nome", "Artista", "Tipo", "Categoria", "Caminho físico", "Disponibilidade no servidor", "Estado"])
        self.material_table.cellDoubleClicked.connect(lambda *_: self.edit_material())
        self.material_table.itemSelectionChanged.connect(self._selection_changed)
        material_tab = QWidget()
        material_layout = QVBoxLayout(material_tab)
        material_layout.addWidget(self.material_table)
        actions = QHBoxLayout()
        self.add_button = QPushButton("Cadastrar material")
        self.add_button.clicked.connect(self.add_material)
        self.edit_button = QPushButton("Editar selecionado")
        self.edit_button.clicked.connect(self.edit_material)
        self.deactivate_button = QPushButton("Desativar cadastro")
        self.deactivate_button.clicked.connect(self.deactivate_material)
        self.restore_button = QPushButton("Restaurar cadastro")
        self.restore_button.clicked.connect(self.restore_material)
        for button in (self.add_button, self.edit_button, self.deactivate_button, self.restore_button):
            button.setEnabled(self.can_edit)
            actions.addWidget(button)
        self.edit_button.setEnabled(False)
        self.deactivate_button.setEnabled(False)
        self.restore_button.setEnabled(False)
        actions.addStretch(1)
        material_layout.addLayout(actions)
        self.tabs.addTab(material_tab, "Materiais")

        self.shortcut_table = self._table(["ID", "Atalho", "Categoria", "Tipo", "Estado", "Representação HitMo"])
        self.shortcut_table.cellDoubleClicked.connect(self._edit_shortcut_material)
        self.tabs.addTab(self.shortcut_table, "Atalhos HitMo")

        self.category_table = self._table(["ID", "Categoria", "Tipo", "Materiais", "Ícone", "Estado"])
        self.category_table.itemSelectionChanged.connect(self._category_selection_changed)
        self.category_table.cellDoubleClicked.connect(lambda *_: self.edit_category())
        category_tab = QWidget()
        category_layout = QVBoxLayout(category_tab)
        category_layout.addWidget(self.category_table)
        category_actions = QHBoxLayout()
        self.category_add_button = QPushButton("Nova categoria")
        self.category_add_button.clicked.connect(self.add_category)
        self.category_edit_button = QPushButton("Editar")
        self.category_edit_button.clicked.connect(self.edit_category)
        self.category_deactivate_button = QPushButton("Desativar")
        self.category_deactivate_button.clicked.connect(lambda: self._set_entity_active("category", False))
        self.category_restore_button = QPushButton("Restaurar")
        self.category_restore_button.clicked.connect(lambda: self._set_entity_active("category", True))
        for button in (self.category_add_button, self.category_edit_button, self.category_deactivate_button, self.category_restore_button):
            button.setEnabled(self.can_edit)
            category_actions.addWidget(button)
        self.category_edit_button.setEnabled(False)
        self.category_deactivate_button.setEnabled(False)
        self.category_restore_button.setEnabled(False)
        category_actions.addStretch(1)
        category_layout.addLayout(category_actions)
        self.tabs.addTab(category_tab, "Categorias")

        self.folder_table = self._table(["ID", "Pasta", "Categoria", "Diretório", "Disponibilidade", "Ícone", "Materiais", "Estado"])
        self.folder_table.itemSelectionChanged.connect(self._folder_selection_changed)
        self.folder_table.cellDoubleClicked.connect(lambda *_: self.edit_folder())
        folder_tab = QWidget()
        folder_layout = QVBoxLayout(folder_tab)
        folder_layout.addWidget(self.folder_table)
        folder_actions = QHBoxLayout()
        self.folder_add_button = QPushButton("Nova pasta")
        self.folder_add_button.clicked.connect(self.add_folder)
        self.folder_edit_button = QPushButton("Editar")
        self.folder_edit_button.clicked.connect(self.edit_folder)
        self.folder_deactivate_button = QPushButton("Desativar")
        self.folder_deactivate_button.clicked.connect(lambda: self._set_entity_active("folder", False))
        self.folder_restore_button = QPushButton("Restaurar")
        self.folder_restore_button.clicked.connect(lambda: self._set_entity_active("folder", True))
        for button in (self.folder_add_button, self.folder_edit_button, self.folder_deactivate_button, self.folder_restore_button):
            button.setEnabled(self.can_edit)
            folder_actions.addWidget(button)
        self.folder_edit_button.setEnabled(False)
        self.folder_deactivate_button.setEnabled(False)
        self.folder_restore_button.setEnabled(False)
        folder_actions.addStretch(1)
        folder_layout.addLayout(folder_actions)
        self.tabs.addTab(folder_tab, "Pastas do catálogo")
        self.reference_table = self._table(["ID Playlist", "Nome", "Código original", "Destino", "Ícone original", "Argumentos", "Atalho", "Índice ícone", "Saída", "Arquivos", "DBF ID", "Alvo existia no import"])
        self.tabs.addTab(self.reference_table, "Referências Folders.xml (somente leitura)")
        layout.addWidget(self.tabs, 1)
        self.status = QLabel("Pronto")
        layout.addWidget(self.status)
        self.setCentralWidget(root)

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
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit { background:#191d24; border:1px solid #303642; border-radius:5px; padding:6px; }
            QTableWidget { background:#15181e; alternate-background-color:#191d24; gridline-color:#292e38; }
            QHeaderView::section { background:#1b1f27; color:#cbd0d9; padding:7px; border:0; }
            QPushButton { background:#252a34; border:1px solid #373e4b; border-radius:5px; padding:7px 12px; }
            QPushButton:hover { background:#303746; }
            QPushButton:disabled { color:#757b86; }
            QTabBar::tab { background:#1b1f27; padding:8px 14px; }
            QTabBar::tab:selected { background:#302c53; }
        """)

    def refresh(self):
        try:
            identity = self.api.version()
            self.categories = self.api.categories(include_inactive=True)["categories"]
            self.folders = self.api.folders(include_inactive=True)["folders"]
            self.materials = self.api.materials(include_inactive=True)["materials"]
            references = self.api.playlist_references()
            self.references = references["entries"]
            self.revision = int(identity["catalog_revision"])
            self.revision_label.setText(f"Revisão {self.revision}")
            self.identity_label.setText(f"Conectado: {identity.get('authenticated_as', 'usuário')} · {self.api.base_url}")
            self._render_folders()
            self._render_categories()
            self._render_materials()
            self._render_shortcuts()
            self._render_references(references.get("catalog"))
            shortcut_result = reconcile_shortcuts(self.materials, self.shortcuts_dir, self.categories)
            self.status.setText(
                f"Catálogo sincronizado · atalhos: {shortcut_result['created']} criados, "
                f"{shortcut_result['updated']} atualizados, {shortcut_result['preserved']} protegidos, "
                f"{shortcut_result['stale']} antigos mantidos, {shortcut_result['errors']} erros"
            )
        except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
            self.status.setText("Desconectado · use Atualizar catálogo para tentar novamente")
            QMessageBox.warning(self, "Catálogo indisponível", str(exc))

    def _render_categories(self):
        selected = self.category_filter.currentData()
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItem("Todas as categorias", None)
        for category in self.categories:
            label = category["name"] + (" · inativa" if not category["active"] else "")
            icon_path = str(category.get("icon_path", ""))
            icon = QIcon(icon_path) if icon_path and Path(icon_path).is_file() else QIcon()
            self.category_filter.addItem(icon, label, category["name"])
        index = self.category_filter.findData(selected)
        self.category_filter.setCurrentIndex(max(0, index))
        self.category_filter.blockSignals(False)
        self.category_table.setRowCount(0)
        kind_labels = {"audio": "Áudio", "text": "Texto", "file": "Arquivo"}
        for category in self.categories:
            row = self.category_table.rowCount()
            self.category_table.insertRow(row)
            values = (category["id"], category["name"], kind_labels.get(category["material_kind"], category["material_kind"]), category.get("material_count", 0), category.get("icon_path", ""), "Ativa" if category["active"] else "Inativa")
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setData(ROLE, category)
                if column == 1 and category.get("icon_path") and Path(category["icon_path"]).is_file():
                    cell.setIcon(QIcon(category["icon_path"]))
                self.category_table.setItem(row, column, cell)
        self.category_table.resizeColumnsToContents()

    def _render_materials(self, *_):
        text = self.search.text().strip().casefold()
        category = self.category_filter.currentData()
        folder_id = self.folder_filter.currentData()
        state = self.state_filter.currentData()
        filtered = []
        for material in self.materials:
            active = bool(material.get("active", 1))
            if state == "active" and not active or state == "inactive" and active:
                continue
            if category and material.get("category") != category:
                continue
            if folder_id is not None and material.get("folder_id") != folder_id:
                continue
            searchable = " ".join(str(material.get(key, "")) for key in ("title", "artist", "path", "category")).casefold()
            if text and text not in searchable:
                continue
            filtered.append(material)
        self.material_table.setRowCount(0)
        for material in filtered:
            row = self.material_table.rowCount()
            self.material_table.insertRow(row)
            kind = str(material.get("material_kind", "audio"))
            path_state = material.get("path_exists_on_server")
            available = "Disponível" if path_state is True else ("Referência lógica" if str(material.get("path", "")).startswith("hitmo://") else ("UNC · teste físico pendente" if path_state is None else "Ausente no servidor"))
            values = (
                material["id"], material["title"], material.get("artist", ""),
                {"audio": "Áudio", "text": "Texto/documento", "file": "Arquivo"}.get(kind, kind),
                material["category"], material.get("path", ""), available,
                "Ativo" if material.get("active", 1) else "Inativo",
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setData(ROLE, material)
                if column == 1 and material.get("icon_path") and Path(material["icon_path"]).is_file():
                    cell.setIcon(QIcon(material["icon_path"]))
                self.material_table.setItem(row, column, cell)
        self.material_table.resizeColumnsToContents()

    def _render_folders(self):
        selected = self.folder_filter.currentData()
        self.folder_filter.blockSignals(True)
        self.folder_filter.clear()
        self.folder_filter.addItem("Todas as pastas", None)
        for folder in self.folders:
            label = folder["name"] + (" · inativa" if not folder["active"] else "")
            self.folder_filter.addItem(label, int(folder["id"]))
        index = self.folder_filter.findData(selected)
        self.folder_filter.setCurrentIndex(max(0, index))
        self.folder_filter.blockSignals(False)
        self.folder_table.setRowCount(0)
        for item in self.folders:
            row = self.folder_table.rowCount()
            self.folder_table.insertRow(row)
            path_state = item.get("path_exists_on_server")
            available = "Disponível" if path_state is True else ("UNC · teste físico pendente" if path_state is None else "Indisponível no servidor")
            values = (item["id"], item["name"], item.get("category") or "—", item["path"], available, item.get("icon_path", ""), item.get("material_count", 0), "Ativa" if item.get("active") else "Inativa")
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setData(ROLE, item)
                if column == 1 and item.get("icon_path") and Path(item["icon_path"]).is_file():
                    cell.setIcon(QIcon(item["icon_path"]))
                self.folder_table.setItem(row, column, cell)
        self.folder_table.resizeColumnsToContents()

    def _render_shortcuts(self):
        self.shortcut_table.setRowCount(0)
        categories_by_name = {category["name"]: category for category in self.categories}
        for material in self.materials:
            row = self.shortcut_table.rowCount()
            self.shortcut_table.insertRow(row)
            kind = str(material.get("material_kind", "audio"))
            values = (
                material["id"], material["title"], material["category"],
                {"audio": "Áudio", "text": "Texto/documento", "file": "Arquivo"}.get(kind, kind),
                "Ativo" if material.get("active", 1) else "Inativo",
                str(self.shortcuts_dir / f"material-{material['id']}.hitmo.json"),
            )
            icon_path = str(material.get("icon_path") or categories_by_name.get(material["category"], {}).get("icon_path", ""))
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                if column == 1 and icon_path and Path(icon_path).is_file():
                    cell.setIcon(QIcon(icon_path))
                self.shortcut_table.setItem(row, column, cell)
        self.shortcut_table.resizeColumnsToContents()

    def _edit_shortcut_material(self, row: int, _column: int):
        if row < 0 or not self.shortcut_table.item(row, 0):
            return
        media_id = int(self.shortcut_table.item(row, 0).text())
        self.state_filter.setCurrentIndex(self.state_filter.findData("all"))
        self.category_filter.setCurrentIndex(0)
        self.folder_filter.setCurrentIndex(0)
        self.search.clear()
        for material_row in range(self.material_table.rowCount()):
            item = self.material_table.item(material_row, 0)
            if item and int(item.text()) == media_id:
                self.material_table.selectRow(material_row)
                self.tabs.setCurrentIndex(0)
                self.edit_material()
                return
        self.status.setText("Material não está disponível na lista atual; atualize o catálogo.")

    def _render_references(self, catalog):
        self.reference_table.setRowCount(0)
        self.reference_table.setToolTip("Snapshot preservado; códigos não são associados automaticamente a IDs do catálogo HitMo.")
        for item in self.references:
            row = self.reference_table.rowCount()
            self.reference_table.insertRow(row)
            values = (item["playlist_id"], item["title"], item["type_code"], item["target"], item["icon_location"], item["shortcut_arguments"], item["shortcut_path_name"], item["icon_index"], item["output"], item["total_files"], item["dbf_id"], "Sim" if item["target_exists"] else "Não")
            for column, value in enumerate(values):
                self.reference_table.setItem(row, column, QTableWidgetItem("" if value is None else str(value)))
        self.reference_table.setToolTip(
            "Snapshot de " + str(catalog.get("source_path")) + ". Os códigos e destinos são somente leitura; associação automática com media.id não é presumida."
            if catalog else "Nenhum snapshot Folders.xml importado no servidor."
        )
        self.reference_table.resizeColumnsToContents()

    def _selected_material(self) -> dict | None:
        row = self.material_table.currentRow()
        if row < 0 or not self.material_table.item(row, 0):
            return None
        return self.material_table.item(row, 0).data(ROLE)

    def _selection_changed(self):
        record = self._selected_material()
        active = bool(record and record.get("active", 1))
        self.edit_button.setEnabled(self.can_edit and record is not None)
        self.deactivate_button.setEnabled(self.can_edit and record is not None and active)
        self.restore_button.setEnabled(self.can_edit and record is not None and not active)

    def _selected_entity(self, table: QTableWidget) -> dict | None:
        row = table.currentRow()
        item = table.item(row, 0) if row >= 0 else None
        return item.data(ROLE) if item else None

    def _category_selection_changed(self):
        record = self._selected_entity(self.category_table)
        active = bool(record and record.get("active", 1))
        self.category_edit_button.setEnabled(self.can_edit and record is not None)
        self.category_deactivate_button.setEnabled(self.can_edit and record is not None and active)
        self.category_restore_button.setEnabled(self.can_edit and record is not None and not active)

    def _folder_selection_changed(self):
        record = self._selected_entity(self.folder_table)
        active = bool(record and record.get("active", 1))
        self.folder_edit_button.setEnabled(self.can_edit and record is not None)
        self.folder_deactivate_button.setEnabled(self.can_edit and record is not None and active)
        self.folder_restore_button.setEnabled(self.can_edit and record is not None and not active)

    def add_category(self):
        if not self.can_edit:
            return
        dialog = CategoryDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save_entity("category", None, dialog.values())

    def edit_category(self):
        record = self._selected_entity(self.category_table)
        if not self.can_edit or not record:
            return
        dialog = CategoryDialog(record, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save_entity("category", int(record["id"]), dialog.values())

    def add_folder(self):
        if not self.can_edit:
            return
        dialog = FolderDialog(self.categories, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save_entity("folder", None, dialog.values())

    def edit_folder(self):
        record = self._selected_entity(self.folder_table)
        if not self.can_edit or not record:
            return
        dialog = FolderDialog(self.categories, record, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save_entity("folder", int(record["id"]), dialog.values())

    def _save_entity(self, resource: str, entity_id: int | None, values: dict):
        try:
            revision = self.api.version()["catalog_revision"]
            if resource == "category":
                if entity_id is None:
                    self.api.add_category(values, revision)
                else:
                    self.api.update_category(entity_id, values, revision)
            elif entity_id is None:
                self.api.add_folder(values, revision)
            else:
                self.api.update_folder(entity_id, values, revision)
        except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Alteração não salva", f"{exc}\n\nAtualize o catálogo e reaplique alterações em conflito.")
            return
        self.refresh()

    def _set_entity_active(self, resource: str, active: bool):
        table = self.category_table if resource == "category" else self.folder_table
        record = self._selected_entity(table)
        if not self.can_edit or not record:
            return
        verb = "restaurar" if active else "desativar"
        retained = "Os materiais, referências, programação e histórico serão preservados."
        if QMessageBox.question(self, f"Confirmar {verb}", f"Deseja {verb} este cadastro? {retained}") != QMessageBox.StandardButton.Yes:
            return
        try:
            revision = self.api.version()["catalog_revision"]
            if resource == "category":
                self.api.set_category_active(int(record["id"]), active, revision)
            else:
                self.api.set_folder_active(int(record["id"]), active, revision)
        except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Operação não concluída", str(exc))
            return
        self.refresh()

    def add_material(self):
        if not self.can_edit:
            return
        dialog = MaterialDialog(self.categories, self.folders, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.values()
        try:
            self.api.add_material(payload, self.api.version()["catalog_revision"])
        except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Cadastro não salvo", str(exc))
            return
        self.refresh()

    def edit_material(self):
        if not self.can_edit:
            return
        record = self._selected_material()
        if not record:
            return
        dialog = MaterialDialog(self.categories, self.folders, record, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.api.update_material(int(record["id"]), dialog.values(), self.api.version()["catalog_revision"])
        except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Edição não salva", f"{exc}\n\nEm conflito 409, atualize o catálogo e reaplique a alteração.")
            return
        self.refresh()

    def _set_active(self, active: bool):
        record = self._selected_material()
        if not self.can_edit or not record:
            return
        action = self.api.restore_material if active else self.api.deactivate_material
        prompt = "Restaurar este cadastro?" if active else "Desativar o cadastro? O arquivo, a programação e o histórico serão preservados."
        if QMessageBox.question(self, "Confirmar operação", prompt) != QMessageBox.StandardButton.Yes:
            return
        try:
            action(int(record["id"]), self.api.version()["catalog_revision"])
        except (ConnectionError, RuntimeError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "Operação não concluída", str(exc))
            return
        self.refresh()

    def deactivate_material(self):
        self._set_active(False)

    def restore_material(self):
        self._set_active(True)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("HitMo ConfigManager")
    dialog = ConnectionDialog()
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.client is None or dialog.identity is None:
        return 0
    window = ConfigManagerWindow(dialog.client, dialog.identity)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
