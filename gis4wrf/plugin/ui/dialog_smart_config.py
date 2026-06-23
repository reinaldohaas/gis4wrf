from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QCheckBox,
    QComboBox, QPushButton, QTextEdit, QMessageBox
)
from PyQt5.QtCore import Qt
import os
from gis4wrf.core.readers.namelist import read_namelist
from gis4wrf.core.writers.namelist import patch_namelist

class SmartConfigDialog(QDialog):
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Intelligent WRF Configuration Wizard")
        self.resize(650, 550)
        
        self.dx_km = self._get_dx_km()
        self.suggested_dt = round(6 * self.dx_km) if self.dx_km else 60
        
        self._build_ui()
        self._generate_report()
        
    def _get_dx_km(self):
        self.max_dom = 1
        try:
            wrf_nml = read_namelist(self.project.wrf_namelist_path, 'wrf')
            self.max_dom = wrf_nml.get('domains', {}).get('max_dom', 1)
        except:
            pass

        try:
            nml = read_namelist(self.project.wps_namelist_path, 'wps')
            dx = nml['geogrid']['dx']
            if isinstance(dx, list): dx = dx[0]
            if self.max_dom == 1:
                self.max_dom = nml.get('share', {}).get('max_dom', 1)
            return dx / 1000.0
        except:
            return 10.0 # Default fallback
            
    def _build_ui(self):
        layout = QVBoxLayout(self)
        
        lbl_info = QLabel(
            f"<b>Domain Resolution (dx):</b> {self.dx_km:.2f} km<br>"
            f"<b>Suggested Initial Time Step:</b> {self.suggested_dt} seconds"
        )
        layout.addWidget(lbl_info)
        
        group_options = QGroupBox("Configuration Options")
        vbox_opts = QVBoxLayout(group_options)
        
        self.chk_adaptive = QCheckBox("Enable Adaptive Time Step (Recommended for stability and speed)")
        self.chk_adaptive.setChecked(True)
        self.chk_adaptive.stateChanged.connect(self._generate_report)
        vbox_opts.addWidget(self.chk_adaptive)
        
        hbox_season = QHBoxLayout()
        hbox_season.addWidget(QLabel("Primary Season / Weather Focus:"))
        self.cmb_season = QComboBox()
        self.cmb_season.addItems(["Generic / Spring / Fall", "Summer / Severe Storms", "Winter / Snow / Ice"])
        self.cmb_season.currentIndexChanged.connect(self._generate_report)
        hbox_season.addWidget(self.cmb_season)
        vbox_opts.addLayout(hbox_season)
        
        layout.addWidget(group_options)
        
        group_report = QGroupBox("Configuration Report")
        vbox_report = QVBoxLayout(group_report)
        self.txt_report = QTextEdit()
        self.txt_report.setReadOnly(True)
        self.txt_report.setStyleSheet("background-color: #f9f9f9; color: #333;")
        vbox_report.addWidget(self.txt_report)
        
        self.chk_agree = QCheckBox("Eu concordo com essa configuração inteligente (I agree with this intelligent configuration)")
        self.chk_agree.stateChanged.connect(self._on_agree_changed)
        vbox_report.addWidget(self.chk_agree)
        
        layout.addWidget(group_report)
        
        self.btn_apply = QPushButton("Apply to namelist.input")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._on_apply)
        layout.addWidget(self.btn_apply)
        
    def _generate_report(self):
        season_idx = self.cmb_season.currentIndex()
        adaptive = self.chk_adaptive.isChecked()
        
        report = []
        report.append("<b>Intelligent Configuration Report:</b><br>")
        
        # Physics
        self.patch = {'physics': {}, 'domains': {}}
        
        def arr(val):
            return [val] * self.max_dom
        
        # Cumulus
        if self.dx_km < 3.0:
            report.append("- <b>Cumulus Physics:</b> Disabled (cu_physics=0).<br>  <i>Reason: Grid resolution is less than 3km (Convection Permitting).</i>")
            self.patch['physics']['cu_physics'] = arr(0)
        else:
            report.append("- <b>Cumulus Physics:</b> Kain-Fritsch (cu_physics=1).<br>  <i>Reason: Grid resolution is >= 3km.</i>")
            self.patch['physics']['cu_physics'] = arr(1)
            
        # Microphysics
        if season_idx == 1: # Summer
            report.append("- <b>Microphysics:</b> WSM6 (mp_physics=6).<br>  <i>Reason: Selected Summer/Storms, excellent for graupel and deep convection.</i>")
            self.patch['physics']['mp_physics'] = arr(6)
        elif season_idx == 2: # Winter
            report.append("- <b>Microphysics:</b> Thompson (mp_physics=8).<br>  <i>Reason: Selected Winter/Snow, superior for mixed-phase ice and snow representation.</i>")
            self.patch['physics']['mp_physics'] = arr(8)
        else:
            report.append("- <b>Microphysics:</b> WSM6 (mp_physics=6).<br>  <i>Reason: Good general-purpose microphysics scheme.</i>")
            self.patch['physics']['mp_physics'] = arr(6)
            
        # Radiation
        report.append("- <b>Radiation:</b> RRTMG for Shortwave and Longwave (ra_sw_physics=4, ra_lw_physics=4).<br>  <i>Reason: Modern WRF standard for accuracy.</i>")
        self.patch['physics']['ra_sw_physics'] = arr(4)
        self.patch['physics']['ra_lw_physics'] = arr(4)
        
        # PBL
        report.append("- <b>PBL:</b> YSU Scheme (bl_pbl_physics=1, sf_sfclay_physics=1).<br>  <i>Reason: Robust boundary layer parametrization for most cases.</i>")
        self.patch['physics']['bl_pbl_physics'] = arr(1)
        self.patch['physics']['sf_sfclay_physics'] = arr(1)
        
        # Time Step
        report.append(f"<br>- <b>Initial Time Step:</b> {self.suggested_dt}s.<br>  <i>Reason: Computed as 6 * dx ({self.dx_km:.2f}km).</i>")
        self.patch['domains']['time_step'] = self.suggested_dt
        
        if adaptive:
            report.append("- <b>Adaptive Time Step:</b> Enabled (use_adaptive_time_step=True). Target CFL=1.2.<br>  <i>Reason: Safely maximizes time step dynamically to speed up simulation without CFL crashing.</i>")
            self.patch['domains']['use_adaptive_time_step'] = True
            self.patch['domains']['step_to_output_time'] = True
            self.patch['domains']['target_cfl'] = 1.2
            self.patch['domains']['max_step_increase_pct'] = 5
            self.patch['domains']['starting_time_step'] = -1
            self.patch['domains']['max_time_step'] = -1
            self.patch['domains']['min_time_step'] = -1
        else:
            report.append("- <b>Adaptive Time Step:</b> Disabled.<br>  <i>Reason: User opted out. Fixed time step will be used.</i>")
            self.patch['domains']['use_adaptive_time_step'] = False
            
        self.txt_report.setHtml("<br>".join(report))
        
    def _on_agree_changed(self, state):
        self.btn_apply.setEnabled(state == Qt.Checked)
        
    def _on_apply(self):
        nml_path = self.project.wrf_namelist_path
        if not os.path.exists(nml_path):
            QMessageBox.warning(self, "Error", "namelist.input not found. Please create or run prepare first.")
            return
            
        patch_namelist(nml_path, self.patch)
        QMessageBox.information(self, "Success", "namelist.input updated successfully with intelligent configuration!")
        self.accept()
