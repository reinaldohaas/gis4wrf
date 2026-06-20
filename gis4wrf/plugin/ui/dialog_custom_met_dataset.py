# GIS4WRF (https://doi.org/10.5281/zenodo.1288569)
# Copyright (c) 2018 D. Meyer and M. Riechert. Licensed under MIT.

from typing import List, Set, Optional
import os
from pathlib import Path
from datetime import datetime, timedelta

from PyQt5.QtCore import Qt, QDate, QTime, QDateTime

from PyQt5.QtGui import (
    QIntValidator, QGuiApplication
)
from PyQt5.QtWidgets import (
    QPushButton, QVBoxLayout, QDialog, QGridLayout,
    QHBoxLayout, QFileDialog, QDialogButtonBox, QListWidget, QListWidgetItem,
    QAbstractItemView, QDateTimeEdit, QLabel, QSpinBox
)

from gis4wrf.core import UserError, UnsupportedError
from gis4wrf.plugin.ui.helpers import add_grid_lineedit, add_grid_combobox, add_grid_labeled_widget, create_file_input

# Map dataset name keywords → recommended VTable filename
VTABLE_MAP = {
    'era5':       'Vtable.ERA-interim.pl',
    'era-interim':'Vtable.ERA-interim.pl',
    'era_interim':'Vtable.ERA-interim.pl',
    'reanalysis': 'Vtable.ERA-interim.pl',
    'gdas':       'Vtable.GFS',
    'gfs':        'Vtable.GFS',
    'ds084':      'Vtable.GFS',
    'fnl':        'Vtable.GFS',
    'ncep':       'Vtable.NCEP',
    'cfsr':       'Vtable.CFSR',
    'narr':       'Vtable.NARR',
    'nam':        'Vtable.NAM',
    'rap':        'Vtable.RAP',
}

def guess_vtable(folder_path: str, vtable_dir: str) -> str:
    """Guess the best VTable based on the folder path, fallback to ERA-interim."""
    folder_lower = folder_path.lower().replace('\\', '/') if folder_path else ''
    for keyword, vtable_name in VTABLE_MAP.items():
        if keyword in folder_lower:
            candidate = os.path.join(vtable_dir, vtable_name)
            if os.path.exists(candidate):
                return vtable_name
    # Default fallback
    for fallback in ['Vtable.ERA-interim.pl', 'Vtable.GFS']:
        if os.path.exists(os.path.join(vtable_dir, fallback)):
            return fallback
    return ''


class CustomMetDatasetDialog(QDialog):
    def __init__(self, vtable_dir: str, spec: Optional[dict]=None) -> None:
        super().__init__()

        self.vtable_dir = vtable_dir
        self.paths = set() # type: Set[Path]

        geom = QGuiApplication.primaryScreen().geometry()
        w, h = geom.width(), geom.height()
        self.setWindowTitle("Custom Meteorological Dataset")
        self.setMinimumSize(int(w * 0.25), int(h * 0.35))

        layout = QVBoxLayout()

        # button to open folder/files dialog
        hbox = QHBoxLayout()
        layout.addLayout(hbox)
        add_folder_btn = QPushButton('Add folder')
        add_files_btn = QPushButton('Add files')
        remove_selected_btn = QPushButton('Remove selected')
        hbox.addWidget(add_folder_btn)
        hbox.addWidget(add_files_btn)
        hbox.addWidget(remove_selected_btn)
        add_folder_btn.clicked.connect(self.on_add_folder_btn_clicked)
        add_files_btn.clicked.connect(self.on_add_files_btn_clicked)
        remove_selected_btn.clicked.connect(self.on_remove_selected_btn_clicked)

        # show added files in a list
        self.paths_list = QListWidget()
        self.paths_list.setSelectionMode(QAbstractItemView.ContiguousSelection)
        layout.addWidget(self.paths_list)

        grid = QGridLayout()
        layout.addLayout(grid)

        # date/time start only
        self.start_date_input = QDateTimeEdit()
        self.start_date_input.setCalendarPopup(True)
        self.start_date_input.setDisplayFormat('dd/MM/yyyy HH:mm')
        add_grid_labeled_widget(grid, 0, 'Start Date/Time', self.start_date_input)

        # duration in hours (replaces End Date/Time)
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 8760)   # 1 h → 1 year
        self.duration_spin.setValue(23)
        self.duration_spin.setSuffix(' h')
        add_grid_labeled_widget(grid, 1, 'Duration (hours)', self.duration_spin)

        # interval in seconds — default 3600
        interval_validator = QIntValidator()
        interval_validator.setBottom(1)
        self.interval_input = add_grid_lineedit(grid, 2, 'Interval in seconds', interval_validator, required=True)
        self.interval_input.set_value(3600)  # default 1 hour

        # vtable file input
        self.vtable_input, vtable_hbox = create_file_input(dialog_caption='Select VTable file',
            is_folder=False, start_folder=vtable_dir)
        add_grid_labeled_widget(grid, 3, 'VTable', vtable_hbox)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.on_ok_clicked)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)
        
        self.setLayout(layout)

        if spec:
            self._populate_from_spec(spec)

    def _populate_from_spec(self, spec: dict) -> None:
        # Files
        paths = spec.get('paths', [])
        if paths:
            self.paths = set(map(Path, paths))
            self.base_folder = spec.get('base_folder', os.path.dirname(str(list(self.paths)[0])))
            self.update_file_list()

        # Start date
        start_date = None
        time_range = spec.get('time_range')
        if time_range and len(time_range) >= 1:
            start_date = time_range[0]
            if isinstance(start_date, str):
                start_date = datetime.fromisoformat(start_date)
            self.start_date_input.setDateTime(
                QDateTime(QDate(start_date.year, start_date.month, start_date.day),
                          QTime(start_date.hour, start_date.minute))
            )

        # Duration: compute from time_range if available
        if time_range and len(time_range) >= 2:
            end_date = time_range[1]
            if isinstance(end_date, str):
                end_date = datetime.fromisoformat(end_date)
            if start_date:
                hours = max(1, int((end_date - start_date).total_seconds() / 3600))
                self.duration_spin.setValue(hours)

        # Interval
        interval = spec.get('interval_seconds')
        if interval:
            self.interval_input.set_value(interval)

        # VTable — use explicit value or guess from folder path
        vtable = spec.get('vtable', '')
        if not vtable:
            folder_path = spec.get('base_folder', '') or (str(list(self.paths)[0]) if self.paths else '')
            vtable = guess_vtable(folder_path, self.vtable_dir)
        if vtable:
            # Store absolute path if it's already absolute, otherwise join with vtable_dir
            full_path = vtable if os.path.isabs(vtable) else os.path.join(self.vtable_dir, vtable)
            self.vtable_input.setText(full_path)

    @property
    def start_date(self) -> datetime:
        return self.start_date_input.dateTime().toPyDateTime()

    @property
    def end_date(self) -> datetime:
        return self.start_date + timedelta(hours=self.duration_spin.value())

    @property
    def interval_seconds(self) -> int:
        return self.interval_input.value()

    @property
    def vtable_path(self) -> str:
        return self.vtable_input.text()

    def on_ok_clicked(self) -> None:
        if not self.paths:
            raise UserError('No GRIB files were added')
        if not self.interval_input.is_valid():
            raise UserError('Interval must be an integer above 0')
        if self.duration_spin.value() < 1:
            raise UserError('Duration must be at least 1 hour')
        if not self.vtable_path:
            raise UserError('No VTable file selected')
        if not os.path.exists(os.path.join(self.vtable_dir, self.vtable_path)) and \
           not os.path.exists(self.vtable_path):
            raise UserError('VTable file does not exist')
        self.accept()

    def on_add_folder_btn_clicked(self) -> None:
        folder = QFileDialog.getExistingDirectory(caption='Select folder')
        if not folder:
            return
        paths = [] # type: List[Path]
        for root, _, filenames in os.walk(folder):
            paths.extend(Path(root) / filename for filename in filenames)
        self.update_paths(self.paths.union(paths))
        self.update_file_list()
    
    def on_add_files_btn_clicked(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(caption='Select files')
        if not paths:
            return

        self.update_paths(self.paths.union(map(Path, paths)))
        self.update_file_list()

    def on_remove_selected_btn_clicked(self) -> None:
        paths = [item.data(Qt.UserRole) for item in self.paths_list.selectedItems()]
        self.update_paths(self.paths.difference(paths))
        self.update_file_list()

    def update_paths(self, paths: Set[Path]) -> None:
        if len(paths) == 1:
            # special case as os.path.commonpath() would return '.'
            base_folder = os.path.dirname(list(paths)[0])
        elif paths:
            try:
                base_folder = os.path.commonpath(paths)
            except ValueError:
                raise UnsupportedError('Only datasets with files located on the same drive are supported')
        else:
            base_folder = None

        self.base_folder = base_folder
        self.paths = paths

    def update_file_list(self) -> None:
        self.paths_list.clear()
        for path in sorted(self.paths):
            item = QListWidgetItem(str(path))
            item.setData(Qt.UserRole, path)
            self.paths_list.addItem(item)
