from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from .database import Database


class OperatorDialog(QDialog):
    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Operadores")
        self.setMinimumSize(420, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Perfis locais do estúdio"))
        self.list = QListWidget()
        layout.addWidget(self.list)
        row = QHBoxLayout()
        add = QPushButton("Adicionar")
        remove = QPushButton("Remover")
        row.addWidget(add)
        row.addWidget(remove)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        add.clicked.connect(self._add)
        remove.clicked.connect(self._remove)
        self._refresh()

    def _refresh(self):
        self.list.clear()
        for row in self.db.all("SELECT id,name,role FROM operators WHERE active=1 ORDER BY name COLLATE NOCASE"):
            item = QListWidgetItem(f"{row['name']}    ·    {row['role']}")
            item.setData(Qt.ItemDataRole.UserRole, int(row["id"]))
            self.list.addItem(item)

    def _add(self):
        name, ok = QInputDialog.getText(self, "Novo operador", "Nome")
        if not ok or not name.strip():
            return
        role, ok = QInputDialog.getItem(self, "Permissão", "Perfil", ["Operador", "Administrador"], 0, False)
        if not ok:
            return
        try:
            self.db.execute("INSERT INTO operators(name,role) VALUES(?,?)", (name.strip(), role))
        except Exception as exc:
            QMessageBox.warning(self, "Operador", f"Não foi possível adicionar: {exc}")
        self._refresh()

    def _remove(self):
        item = self.list.currentItem()
        if not item:
            return
        count = int(self.db.one("SELECT COUNT(*) AS n FROM operators WHERE active=1")["n"])
        if count <= 1:
            QMessageBox.information(self, "Operadores", "Mantenha pelo menos um operador cadastrado.")
            return
        self.db.execute("UPDATE operators SET active=0 WHERE id=?", (int(item.data(Qt.ItemDataRole.UserRole)),))
        self._refresh()

