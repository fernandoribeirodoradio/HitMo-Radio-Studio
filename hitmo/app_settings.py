from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLineEdit, QMessageBox, QVBoxLayout, QWidget,
)

from .audio_engine import AudioEngine
from .database import Database


class AppSettingsDialog(QDialog):
    """Application preferences; intentionally separate from material management."""

    def __init__(self, db: Database, engine: AudioEngine, parent: QWidget | None = None):
        super().__init__(parent)
        self.db = db
        self.engine = engine
        self.setWindowTitle("Preferências do aplicativo")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        stored_output_id = db.setting("audio_output_id")
        stored_cue_id = db.setting("cue_output_id")
        self.output = QComboBox()
        self.output.addItem("Dispositivo padrão do Windows", "")
        for device in AudioEngine.available_outputs():
            ident = bytes(device.id()).decode(errors="ignore")
            self.output.addItem(device.description(), ident)
        selected = self.output.findData(stored_output_id)
        if stored_output_id and selected < 0:
            self.output.addItem(f"Indisponível · manter seleção salva ({stored_output_id})", stored_output_id)
            selected = self.output.count() - 1
        self.output.setCurrentIndex(max(0, selected))
        self.cue_output = QComboBox()
        self.cue_output.addItem("CUE desativado", "")
        for device in AudioEngine.available_outputs():
            ident = bytes(device.id()).decode(errors="ignore")
            self.cue_output.addItem(device.description(), ident)
        cue_selected = self.cue_output.findData(stored_cue_id)
        if stored_cue_id and cue_selected < 0:
            self.cue_output.addItem(f"Indisponível · manter seleção salva ({stored_cue_id})", stored_cue_id)
            cue_selected = self.cue_output.count() - 1
        self.cue_output.setCurrentIndex(max(0, cue_selected))
        self.auto_advance = QCheckBox("Avançar automaticamente após concluir o item")
        self.auto_advance.setChecked(db.setting("auto_advance", "1") == "1")
        self.startup_play = QCheckBox("Iniciar reprodução ao abrir")
        self.startup_play.setChecked(db.setting("startup_play", "0") == "1")
        self.station_name = QLineEdit(db.setting("station_name", "HitMo Radio"))
        form.addRow("Saída de áudio", self.output)
        form.addRow("Saída CUE independente", self.cue_output)
        form.addRow("Nome da emissora", self.station_name)
        form.addRow("", self.auto_advance)
        form.addRow("", self.startup_play)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        output_id = str(self.output.currentData() or "")
        cue_id = str(self.cue_output.currentData() or "")
        old_output_id = self.db.setting("audio_output_id")
        old_cue_id = self.db.setting("cue_output_id")
        if (output_id, cue_id) != (old_output_id, old_cue_id):
            selected_output = AudioEngine._device_id_for_selection(output_id)
            selected_cue = AudioEngine._device_id_for_selection(cue_id) if cue_id else ""
            if selected_cue and selected_cue == selected_output:
                QMessageBox.warning(self, "Saídas incompatíveis", "PGM e CUE precisam usar dispositivos de saída distintos.")
                return
            try:
                self.engine.configure_outputs(output_id, cue_id)
            except ValueError as exc:
                QMessageBox.warning(self, "Saídas incompatíveis", str(exc))
                return
            except RuntimeError as exc:
                QMessageBox.warning(self, "Saída indisponível", str(exc))
                return
            self.db.set_setting("audio_output_id", output_id)
            self.db.set_setting("cue_output_id", cue_id)
        self.db.set_setting("auto_advance", "1" if self.auto_advance.isChecked() else "0")
        self.db.set_setting("startup_play", "1" if self.startup_play.isChecked() else "0")
        self.db.set_setting("station_name", self.station_name.text().strip() or "HitMo Radio")
        super().accept()
