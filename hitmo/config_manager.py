from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLineEdit, QVBoxLayout, QWidget,
)

from .audio_engine import AudioEngine
from .database import Database


class ConfigManagerDialog(QDialog):
    """Independent settings window for audio routing and station behavior."""

    def __init__(self, db: Database, engine: AudioEngine, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self.engine = engine
        self.setWindowTitle("Config Manager · HitMo Radio Studio")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.output = QComboBox()
        self.output.addItem("Dispositivo padrão do Windows", "")
        for device in AudioEngine.available_outputs():
            ident = bytes(device.id()).decode(errors="ignore")
            self.output.addItem(device.description(), ident)
        saved = db.setting("audio_output_id")
        selected = self.output.findData(saved)
        self.output.setCurrentIndex(max(0, selected))

        self.auto_advance = QCheckBox("Continuar para a próxima faixa ao terminar")
        self.auto_advance.setChecked(db.setting("auto_advance", "1") == "1")
        self.startup_play = QCheckBox("Iniciar reprodução automaticamente ao abrir")
        self.startup_play.setChecked(db.setting("startup_play", "0") == "1")
        self.station_name = QLineEdit(db.setting("station_name", "HitMo Radio"))
        form.addRow("Saída do programa", self.output)
        form.addRow("Emissora", self.station_name)
        form.addRow("", self.auto_advance)
        form.addRow("", self.startup_play)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        output_id = str(self.output.currentData() or "")
        self.db.set_setting("audio_output_id", output_id)
        self.db.set_setting("auto_advance", "1" if self.auto_advance.isChecked() else "0")
        self.db.set_setting("startup_play", "1" if self.startup_play.isChecked() else "0")
        self.db.set_setting("station_name", self.station_name.text().strip() or "HitMo Radio")
        self.engine.set_output(output_id)
        super().accept()

