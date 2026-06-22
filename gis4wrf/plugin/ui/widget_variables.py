from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, 
    QCheckBox, QLabel, QLineEdit, QPushButton, QMessageBox, QScrollArea
)
from PyQt5.QtCore import Qt
import os

class VariablesWidget(QWidget):
    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.project = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        lbl_info = QLabel(
            "<b>WRF Output Variables (IOFields)</b><br>"
            "WRF outputs hundreds of variables by default. To reduce file size, you can explicitly "
            "remove groups of heavy variables or add specific ones."
        )
        lbl_info.setWordWrap(True)
        layout.addWidget(lbl_info)

        # Remove Blocks
        group_remove = QGroupBox("Remove Variable Blocks (-)")
        vbox_remove = QVBoxLayout(group_remove)

        self.chk_micro = QCheckBox("Microphysics (QCLOUD, QRAIN, QICE, QSNOW, QGRAUPEL, QVAPOR)")
        self.chk_soil = QCheckBox("Soil & Surface (SMOIS, SH2O, TSLB, TSK, HFX, LH)")
        self.chk_rad = QCheckBox("Radiation & Clouds (RTHRATEN, RTHRATLW, RTHRATSW, GLW, SWDOWN)")
        self.chk_pbl = QCheckBox("PBL (PBLH, UST, AKHS, AKMS)")

        vbox_remove.addWidget(self.chk_micro)
        vbox_remove.addWidget(self.chk_soil)
        vbox_remove.addWidget(self.chk_rad)
        vbox_remove.addWidget(self.chk_pbl)
        layout.addWidget(group_remove)

        # Custom Individual Remove
        group_custom_rm = QGroupBox("Custom Variables to Remove (-)")
        vbox_custom_rm = QVBoxLayout(group_custom_rm)
        self.txt_custom_rm = QLineEdit()
        self.txt_custom_rm.setPlaceholderText("e.g. ZNU, ZNW, ZS, DZS")
        vbox_custom_rm.addWidget(self.txt_custom_rm)
        layout.addWidget(group_custom_rm)

        # Custom Individual Add
        group_custom_add = QGroupBox("Custom Variables to Add (+)")
        vbox_custom_add = QVBoxLayout(group_custom_add)
        self.txt_custom_add = QLineEdit()
        self.txt_custom_add.setPlaceholderText("e.g. WSPD, WDIR")
        vbox_custom_add.addWidget(self.txt_custom_add)
        layout.addWidget(group_custom_add)

        # Save Button
        btn_save = QPushButton("Save Variables Configuration")
        btn_save.clicked.connect(self._on_save)
        layout.addWidget(btn_save)

        layout.addStretch()

    def set_project(self, project):
        self.project = project
        self._load_config()

    def _load_config(self):
        if not self.project or not self.project.path:
            return
            
        iofields_path = os.path.join(self.project.path, 'iofields.txt')
        
        # Reset UI
        self.chk_micro.setChecked(False)
        self.chk_soil.setChecked(False)
        self.chk_rad.setChecked(False)
        self.chk_pbl.setChecked(False)
        self.txt_custom_rm.setText("")
        self.txt_custom_add.setText("")

        if not os.path.exists(iofields_path):
            return

        with open(iofields_path, 'r') as f:
            content = f.read().splitlines()

        added = []
        removed = []
        for line in content:
            line = line.strip()
            if line.startswith('+:'):
                vars_str = line.split(':')[-1]
                added.extend([v.strip() for v in vars_str.split(',') if v.strip()])
            elif line.startswith('-:'):
                vars_str = line.split(':')[-1]
                removed.extend([v.strip() for v in vars_str.split(',') if v.strip()])

        # Detect blocks
        micro_vars = {"QCLOUD", "QRAIN", "QICE", "QSNOW", "QGRAUPEL", "QVAPOR"}
        soil_vars = {"SMOIS", "SH2O", "TSLB", "TSK", "HFX", "LH"}
        rad_vars = {"RTHRATEN", "RTHRATLW", "RTHRATSW", "GLW", "SWDOWN"}
        pbl_vars = {"PBLH", "UST", "AKHS", "AKMS"}

        rem_set = set(removed)
        if micro_vars.issubset(rem_set):
            self.chk_micro.setChecked(True)
            rem_set -= micro_vars
        if soil_vars.issubset(rem_set):
            self.chk_soil.setChecked(True)
            rem_set -= soil_vars
        if rad_vars.issubset(rem_set):
            self.chk_rad.setChecked(True)
            rem_set -= rad_vars
        if pbl_vars.issubset(rem_set):
            self.chk_pbl.setChecked(True)
            rem_set -= pbl_vars

        self.txt_custom_rm.setText(", ".join(sorted(list(rem_set))))
        self.txt_custom_add.setText(", ".join(sorted(list(set(added)))))

    def _on_save(self):
        if not self.project or not self.project.path:
            QMessageBox.warning(self, "Error", "No active project. Please create or open a project first.")
            return

        removed = []
        if self.chk_micro.isChecked():
            removed.extend(["QCLOUD", "QRAIN", "QICE", "QSNOW", "QGRAUPEL", "QVAPOR"])
        if self.chk_soil.isChecked():
            removed.extend(["SMOIS", "SH2O", "TSLB", "TSK", "HFX", "LH"])
        if self.chk_rad.isChecked():
            removed.extend(["RTHRATEN", "RTHRATLW", "RTHRATSW", "GLW", "SWDOWN"])
        if self.chk_pbl.isChecked():
            removed.extend(["PBLH", "UST", "AKHS", "AKMS"])

        custom_rm = [v.strip() for v in self.txt_custom_rm.text().split(',') if v.strip()]
        removed.extend(custom_rm)
        
        custom_add = [v.strip() for v in self.txt_custom_add.text().split(',') if v.strip()]

        lines = []
        # Chunk variables into lines of ~10 to avoid ultra-long namelist lines just in case
        def chunker(seq, size):
            return (seq[pos:pos + size] for pos in range(0, len(seq), size))

        if removed:
            # Remove duplicates while preserving order
            seen = set()
            removed = [x for x in removed if not (x in seen or seen.add(x))]
            for chunk in chunker(removed, 15):
                lines.append("-:h:0:" + ",".join(chunk))

        if custom_add:
            seen = set()
            custom_add = [x for x in custom_add if not (x in seen or seen.add(x))]
            for chunk in chunker(custom_add, 15):
                lines.append("+:h:0:" + ",".join(chunk))

        iofields_path = os.path.join(self.project.path, 'iofields.txt')
        
        if not lines:
            if os.path.exists(iofields_path):
                os.remove(iofields_path)
            QMessageBox.information(self, "Variables", "Configuration cleared. Default WRF output will be used.")
        else:
            with open(iofields_path, 'w') as f:
                f.write("\\n".join(lines) + "\\n")
            QMessageBox.information(self, "Variables", "Variables configuration saved successfully!\\niofields.txt updated.")
