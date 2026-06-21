# GIS4WRF (https://doi.org/10.5281/zenodo.1288569)
# Copyright (c) 2018 D. Meyer and M. Riechert. Licensed under MIT.

from typing import List, Dict, Optional
from collections import namedtuple
import os

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QDoubleValidator, QIntValidator, QPalette, QBrush, QColor
from PyQt5.QtWidgets import (
    QWidget, QTabWidget, QPushButton, QLayout, QVBoxLayout, QDialog, QGridLayout, QGroupBox, QSpinBox,
    QLabel, QHBoxLayout, QComboBox, QScrollArea, QFileDialog, QRadioButton, QLineEdit, QTableWidget,
    QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QDockWidget, QSlider, QListWidget, QListWidgetItem,
    QAbstractItemView, QHeaderView, QCheckBox
)

import gis4wrf.core
from gis4wrf.core import WRFNetCDFVariable, WRFNetCDFVariableSource
from gis4wrf.plugin import geo as plugin_geo
from gis4wrf.plugin.ui.helpers import add_grid_lineedit, add_grid_combobox, dispose_after_delete
from gis4wrf.plugin.ui.dialog_3d_view import View3DDialog

Dataset = namedtuple('Dataset', [
    'name', # str
    'path', # str
    'variables', # Dict[str,WRFNetCDFVariable]
    'times', # List[str]
    'extra_dims' # Dict[str,WRFNetCDFExtraDim]
])

class ViewWidget(QWidget):
    tab_active = pyqtSignal()

    def __init__(self, iface, dock_widget: QDockWidget) -> None:
        super().__init__()
        self.iface = iface
        self.dock_widget = dock_widget
        
        self.vbox = QVBoxLayout()
        self.create_variable_selector()
        self.create_time_selector()
        self.create_extra_dim_selector()
        self.create_interp_input()
        self.create_colormap_panel()
        self.create_dataset_selector()
        self.setLayout(self.vbox)

        self.datasets = {} # type: Dict[str, Dataset]
        self.selected_dataset = None # type: Optional[str]
        self.selected_variable = {} # type: Dict[str,str]
        self.selected_time = {} # type: Dict[str,int]
        self.selected_extra_dim = {} # type: Dict[Tuple[str,str],int]

        self.pause_replace_layer = False

    def create_variable_selector(self) -> None:
        self.variable_selector = QTreeWidget()
        self.variable_selector.setHeaderLabels(['Name', 'Units', 'Description'])
        self.variable_selector.setRootIsDecorated(False)
        self.variable_selector.setSortingEnabled(True)
        self.variable_selector.sortByColumn(0, Qt.AscendingOrder)
        self.variable_selector.header().setSectionsMovable(False)
        self.variable_selector.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.variable_selector.currentItemChanged.connect(self.on_variable_selected)
        hbox = QHBoxLayout()
        hbox.addWidget(self.variable_selector)
        self.vbox.addLayout(hbox)

    def create_time_selector(self) -> None:
        self.time_label = QLabel('Time: N/A')
        self.time_selector = QSlider(Qt.Horizontal)
        self.time_selector.setSingleStep(1)
        self.time_selector.setPageStep(1)
        self.time_selector.setMinimum(0)
        self.time_selector.setMaximum(0)
        self.time_selector.valueChanged.connect(self.on_time_selected)
        self.vbox.addWidget(self.time_label)
        self.vbox.addWidget(self.time_selector)

    def create_extra_dim_selector(self) -> None:
        self.extra_dim_label = QLabel('N/A:')
        self.extra_dim_selector = QSlider(Qt.Horizontal)
        self.extra_dim_selector.setSingleStep(1)
        self.extra_dim_selector.setPageStep(1)
        self.extra_dim_selector.setMinimum(0)
        self.extra_dim_selector.setMaximum(0)
        self.extra_dim_selector.valueChanged.connect(self.on_extra_dim_selected)
        
        vbox = QVBoxLayout()
        vbox.addWidget(self.extra_dim_label)
        vbox.addWidget(self.extra_dim_selector)
        vbox.setContentsMargins(0, 0, 0, 0)
        
        self.extra_dim_container = QWidget()
        self.extra_dim_container.setLayout(vbox)
        self.extra_dim_container.setHidden(True)
        self.vbox.addWidget(self.extra_dim_container)

    def create_interp_input(self) -> None:
        grid = QGridLayout()

        self.interp_vert_selector = add_grid_combobox(grid, 0, 'Vertical Variable')
        self.interp_input = add_grid_lineedit(grid, 1, 'Desired Level', QDoubleValidator(0.0, 10000.0, 50), required=True)       
        self.interp_input.returnPressed.connect(self.on_interp_btn_clicked)

        btn = QPushButton('Interpolate')
        btn.clicked.connect(self.on_interp_btn_clicked)
        grid.addWidget(btn, 2, 1)

        self.interp_container = QGroupBox('Interpolate Vertical Level')
        self.interp_container.setCheckable(True)
        self.interp_container.setChecked(False)
        self.interp_container.toggled.connect(self.on_interp_toggled)
        self.interp_container.setLayout(grid)
        self.interp_container.setHidden(True)
        self.vbox.addWidget(self.interp_container)

    def create_colormap_panel(self) -> None:
        """Create the colormap / layer controls panel."""
        gbox = QGroupBox('Colormap & Layer Controls')
        gbox.setCheckable(False)
        grid = QGridLayout()
        gbox.setLayout(grid)

        # Ramp selector
        self.cmap_combo = QComboBox()
        self._ramp_options = [
            'Spectral', 'RdYlBu', 'RdBu', 'Blues', 'BuGn', 'Greens',
            'YlOrRd', 'PuOr', 'Viridis', 'Magma', 'Plasma', 'Inferno',
            'RdPu', 'BrBG', 'terrain', 'rainbow', 'coolwarm', 'jet'
        ]
        for name in self._ramp_options:
            self.cmap_combo.addItem(name)
        self.cmap_combo.setCurrentText('Spectral')
        grid.addWidget(QLabel('Ramp:'), 0, 0)
        grid.addWidget(self.cmap_combo, 0, 1, 1, 2)

        # Invert checkbox
        self.cmap_invert = QCheckBox('Invert')
        grid.addWidget(self.cmap_invert, 0, 3)

        # Auto min/max
        self.cmap_auto = QCheckBox('Auto min/max')
        self.cmap_auto.setChecked(True)
        self.cmap_auto.toggled.connect(self._on_cmap_auto_toggled)
        grid.addWidget(self.cmap_auto, 1, 0, 1, 2)

        # Manual min/max
        float_val = QDoubleValidator()
        self.cmap_min = QLineEdit()
        self.cmap_min.setPlaceholderText('Min')
        self.cmap_min.setEnabled(False)
        self.cmap_min.setValidator(float_val)
        self.cmap_max = QLineEdit()
        self.cmap_max.setPlaceholderText('Max')
        self.cmap_max.setEnabled(False)
        self.cmap_max.setValidator(float_val)
        grid.addWidget(self.cmap_min, 1, 2)
        grid.addWidget(self.cmap_max, 1, 3)

        # Apply colormap button
        apply_btn = QPushButton('Apply colormap')
        apply_btn.clicked.connect(self.on_apply_colormap)
        grid.addWidget(apply_btn, 2, 0, 1, 2)

        # Bilinear smoothing checkbox
        self.chk_bilinear = QCheckBox('Smooth (bilinear)')
        self.chk_bilinear.setToolTip(
            'Apply bilinear resampling to reduce pixelation in map view')
        self.chk_bilinear.toggled.connect(self._on_bilinear_toggled)
        grid.addWidget(self.chk_bilinear, 2, 2, 1, 2)

        # ── Opacity (layer transparency in QGIS map) ──────────────────────────
        grid.addWidget(QLabel('Opacity:'), 3, 0)
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.setTickPosition(QSlider.TicksBelow)
        self.opacity_slider.setTickInterval(10)
        self.opacity_label  = QLabel('100 %')
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        grid.addWidget(self.opacity_slider, 3, 1, 1, 2)
        grid.addWidget(self.opacity_label,  3, 3)

        # ── Extra tools row ───────────────────────────────────────────────────
        contour_btn = QPushButton('📐 Add Contours')
        contour_btn.setToolTip('Generate contour vector layer in QGIS from current raster band')
        contour_btn.clicked.connect(self.on_add_contours)
        grid.addWidget(contour_btn, 4, 0, 1, 2)

        wind_btn = QPushButton('💨 Wind overlay')
        wind_btn.setToolTip('Show wind barbs / quivers in a matplotlib popup')
        wind_btn.clicked.connect(self.on_wind_overlay)
        grid.addWidget(wind_btn, 4, 2, 1, 2)

        self.vbox.addWidget(gbox)


    def _on_cmap_auto_toggled(self, checked: bool) -> None:
        self.cmap_min.setEnabled(not checked)
        self.cmap_max.setEnabled(not checked)

    def on_apply_colormap(self) -> None:
        """Reapply colormap with current panel settings to all layers in the current group."""
        try:
            dataset = self.get_dataset()
        except Exception:
            return

        ramp_name = self.cmap_combo.currentText()
        invert = self.cmap_invert.isChecked()
        auto = self.cmap_auto.isChecked()
        vmin = None if auto else (float(self.cmap_min.text()) if self.cmap_min.text() else None)
        vmax = None if auto else (float(self.cmap_max.text()) if self.cmap_max.text() else None)

        layers = plugin_geo.get_raster_layers_in_group(dataset.name)
        for layer in layers:
            var_name = layer.shortName() or ''
            plugin_geo.apply_smart_style(layer, var_name,
                                         vmin=vmin, vmax=vmax,
                                         ramp_name=ramp_name, invert=invert)

    def _on_opacity_changed(self, val: int) -> None:
        """Set opacity of all layers in the current dataset group."""
        self.opacity_label.setText(f'{val} %')
        try:
            dataset = self.get_dataset()
        except Exception:
            return
        layers = plugin_geo.get_raster_layers_in_group(dataset.name)
        for layer in layers:
            layer.setOpacity(val / 100.0)
            layer.triggerRepaint()

    def _on_bilinear_toggled(self, checked: bool) -> None:
        """Toggle bilinear resampling to smooth pixelated rasters."""
        try:
            dataset = self.get_dataset()
        except Exception:
            return
        layers = plugin_geo.get_raster_layers_in_group(dataset.name)
        from qgis.core import QgsBilinearRasterResampler
        for layer in layers:
            resampler_filter = layer.resampleFilter()
            if resampler_filter:
                if checked:
                    resampler_filter.setZoomedInResampler(QgsBilinearRasterResampler())
                    resampler_filter.setZoomedOutResampler(QgsBilinearRasterResampler())
                else:
                    resampler_filter.setZoomedInResampler(None)
                    resampler_filter.setZoomedOutResampler(None)
            layer.triggerRepaint()

    def on_add_contours(self) -> None:
        """Generate a contour vector layer from the current raster and add to QGIS."""
        try:
            dataset = self.get_dataset()
        except Exception:
            return
        layers = plugin_geo.get_raster_layers_in_group(dataset.name)
        if not layers:
            return
        layer = layers[0]
        try:
            from osgeo import gdal, ogr
            import tempfile
            src = gdal.Open(layer.source())
            if src is None:
                raise RuntimeError('Cannot open raster source for contouring')
            band_idx = self.get_time_index() + 1
            band = src.GetRasterBand(band_idx)
            stats = band.GetStatistics(True, True)
            vmin, vmax = stats[0], stats[1]
            interval = (vmax - vmin) / 10.0
            if interval <= 0:
                interval = 1.0

            tmp = tempfile.mktemp(suffix='.gpkg')
            driver = ogr.GetDriverByName('GPKG')
            out_ds = driver.CreateDataSource(tmp)
            srs_wkt = src.GetProjection()
            from osgeo import osr
            srs = osr.SpatialReference()
            srs.ImportFromWkt(srs_wkt)
            out_layer = out_ds.CreateLayer('contours', srs=srs,
                                           geom_type=ogr.wkbMultiLineString)
            field = ogr.FieldDefn('LEVEL', ogr.OFTReal)
            out_layer.CreateField(field)
            gdal.ContourGenerate(band, interval, 0, [], 0, 0, out_layer, -1, 0)
            out_ds = None

            from qgis.core import QgsVectorLayer, QgsProject
            var_name = layer.shortName() or 'var'
            vlayer = QgsVectorLayer(tmp, f'{var_name} contours', 'ogr')
            if vlayer.isValid():
                QgsProject.instance().addMapLayer(vlayer)
            else:
                raise RuntimeError('Contour layer is not valid')
        except Exception as exc:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, 'Contours', f'Error generating contours:\n{exc}')

    def on_wind_overlay(self) -> None:
        """Open a matplotlib popup showing wind barbs / quivers overlaid on the variable."""
        try:
            dataset  = self.get_dataset()
            variable = self.get_variable()
        except Exception:
            return
        try:
            import matplotlib
            matplotlib.use('Qt5Agg')
            from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
            from netCDF4 import Dataset as NC4Dataset
            import numpy as np

            with NC4Dataset(dataset.path) as ds:
                # Find U/V wind
                for uname, vname in [('U10', 'V10'), ('U', 'V')]:
                    u_var = ds.variables.get(uname)
                    v_var = ds.variables.get(vname)
                    if u_var is not None and v_var is not None:
                        break
                else:
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.information(self, 'Wind overlay',
                        'U10/V10 or U/V not found in this file.')
                    return

                t = self.get_time_index()
                u = np.array(u_var[t]); v = np.array(v_var[t])
                if u.ndim == 3:
                    lev = self.get_extra_dim_index() or 0
                    u = u[lev]; v = v[lev]

                xlat  = ds.variables.get('XLAT')
                xlong = ds.variables.get('XLONG')
                lats  = np.squeeze(xlat[0])  if xlat  is not None else None
                lons  = np.squeeze(xlong[0]) if xlong is not None else None

            ny, nx = u.shape[0], u.shape[1]
            step = max(1, min(ny, nx) // 18)
            u_s  = u[::step, :nx:step];  v_s = v[:ny:step, ::step]
            if lons is not None:
                lo_s = lons[::step, ::step]; la_s = lats[::step, ::step]
            else:
                lo_s, la_s = (np.arange(nx)[::step][np.newaxis, :] * np.ones((len(range(0, ny, step)), 1)),
                              np.arange(ny)[::step][:, np.newaxis] * np.ones((1, len(range(0, nx, step)))))
            # Clip to same shape
            n = min(lo_s.shape[0], u_s.shape[0], v_s.shape[0])
            m = min(lo_s.shape[1], u_s.shape[1], v_s.shape[1])
            lo_s, la_s, u_s, v_s = lo_s[:n, :m], la_s[:n, :m], u_s[:n, :m], v_s[:n, :m]
            spd = np.hypot(u_s, v_s)

            from PyQt5.QtWidgets import QDialog, QVBoxLayout
            dlg = QDialog(self)
            dlg.setWindowTitle(f'Wind overlay — {dataset.name}')
            dlg.resize(800, 600)
            fig = Figure(facecolor='#1a1a2e', tight_layout=True)
            canvas = FigureCanvasQTAgg(fig)
            ax = fig.add_subplot(111)
            ax.set_facecolor('#0d0d1a')
            ax.tick_params(colors='#aaa')
            t_str = dataset.times[t] if dataset.times else ''
            ax.set_title(f'Wind — {uname}/{vname}  {t_str}', color='white')
            q = ax.quiver(lo_s, la_s, u_s, v_s, spd,
                          cmap='plasma', scale=None, width=0.003)
            cb = fig.colorbar(q, ax=ax, pad=0.02, fraction=0.04)
            cb.set_label('Wind speed (m/s)', color='#ccc')
            for lbl in cb.ax.get_yticklabels():
                lbl.set_color('#aaa')
            ax.set_xlabel('Longitude' if lons is not None else 'X', color='#ccc')
            ax.set_ylabel('Latitude'  if lons is not None else 'Y', color='#ccc')
            lay = QVBoxLayout(dlg)
            lay.addWidget(canvas)
            dlg.show()
        except Exception as exc:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, 'Wind overlay', f'Error:\n{exc}')


    def create_dataset_selector(self) -> None:
        dataset_label = QLabel('Dataset:')
        self.dataset_selector = QComboBox()
        self.dataset_selector.currentIndexChanged.connect(self.on_dataset_selected)
        hbox = QHBoxLayout()
        hbox.addWidget(dataset_label)
        hbox.addWidget(self.dataset_selector)
        self.vbox.addLayout(hbox)

        btn_3d = QPushButton('🌐  Open 3D View')
        btn_3d.setToolTip('Open interactive 3D visualisation for the selected variable')
        btn_3d.clicked.connect(self.on_open_3d_view)
        self.vbox.addWidget(btn_3d)


    def add_dataset(self, path: str) -> None:
        variables = gis4wrf.core.get_supported_wrf_nc_variables(path)
        times = gis4wrf.core.get_wrf_nc_time_steps(path)
        extra_dims = gis4wrf.core.get_wrf_nc_extra_dims(path)
        dataset_name = os.path.basename(path)
        is_new_dataset = dataset_name not in self.datasets
        self.datasets[dataset_name] = Dataset(dataset_name, path, variables, times, extra_dims)
        if is_new_dataset:
            self.dataset_selector.addItem(dataset_name, dataset_name)
        self.select_dataset(dataset_name, is_new=is_new_dataset)

    def select_dataset(self, dataset_name: str, is_new: bool) -> None:
        index = self.dataset_selector.findData(dataset_name)
        current_index = self.dataset_selector.currentIndex()
        if index == current_index and not is_new:
            # otherwise the event handler wouldn't be triggered
            self.dataset_selector.setCurrentIndex(-1)
        self.dataset_selector.setCurrentIndex(index)     

    def init_variable_selector(self) -> None:
        dataset = self.get_dataset()
        selected = self.selected_variable.get(dataset.name)
        self.variable_selector.clear()
        derived_bg = QBrush(QColor('#E8FFE9'))
        for var_name, variable in sorted(dataset.variables.items(), key=lambda v: v[1].name):
            derived = variable.source != WRFNetCDFVariableSource.FILE
            item = QTreeWidgetItem(self.variable_selector)
            item.setData(0, Qt.UserRole, var_name)
            var_name_text = var_name.upper()
            item.setText(0, var_name_text)
            if derived:
                item.setToolTip(0, f'Derived by {variable.source.value}')
                for i in range(3):
                    item.setBackground(i, derived_bg)
            item.setText(1, variable.units)
            item.setText(2, variable.description)
            item.setToolTip(2, variable.description)
            if var_name == selected:
                self.variable_selector.setCurrentItem(item)

        # Resize Units column to fit contents, and use as basis for Units and Name columns.
        # Resizing Name to fit contents would make the column too wide as some
        # derived variables have longer names.
        self.variable_selector.resizeColumnToContents(1)
        header = self.variable_selector.header()
        units_size = header.sectionSize(1)
        header.setDefaultSectionSize(int(units_size * 1.2))
        
        if selected is None:
            self.extra_dim_container.hide()

    def init_time_selector(self) -> None:
        dataset = self.get_dataset()
        self.time_selector.setMaximum(len(dataset.times) - 1)
        selected_time = self.selected_time.get(dataset.name, 0)
        self.select_time(selected_time)
        # force label update in case the index didn't change during dataset change
        self.on_time_selected(selected_time)

    def select_time(self, index: int) -> None:
        self.time_selector.setValue(index)

    def init_extra_dim_selector(self) -> None:
        dataset = self.get_dataset()
        variable = self.get_variable()
        extra_dim_name = variable.extra_dim_name
        if extra_dim_name is None:
            self.extra_dim_container.hide()
            return
        # prevent double layer replace, already happens in on_variable_selected()
        self.pause_replace_layer = True
        extra_dim = dataset.extra_dims[extra_dim_name]
        selected_extra_dim = self.selected_extra_dim.get((dataset.name, extra_dim_name), 0)
        self.extra_dim_label.setText(extra_dim.label + f': {extra_dim.steps[selected_extra_dim]}')
        self.extra_dim_selector.setMinimum(0)
        self.extra_dim_selector.setMaximum(len(extra_dim.steps) - 1)
        self.extra_dim_selector.setValue(selected_extra_dim)
        self.extra_dim_container.show()
        self.pause_replace_layer = False

    def init_interp_input(self, dataset_init: bool) -> None:
        if dataset_init:
            self.interp_vert_selector.clear()
            has_vert = False
            sorted_variables = sorted(self.get_dataset().variables.values(), key=lambda v: v.name)
            for variable in sorted_variables:
                if variable.extra_dim_name != 'bottom_top':
                    continue
                has_vert = True
                variable_label = self.get_variable_label(variable)
                if len(variable_label) > 30:
                    interp_vert_selector_label = variable_label[:27] + '...'
                else:
                    interp_vert_selector_label = variable_label
                self.interp_vert_selector.addItem(interp_vert_selector_label, variable.name)
            if not has_vert:
                self.extra_dim_container.setEnabled(True)
                self.interp_container.hide()
        else:
            variable = self.get_variable()
            extra_dim_name = variable.extra_dim_name
            if extra_dim_name != 'bottom_top':
                self.interp_container.hide()
                return
            self.interp_container.show()
        
    def on_dataset_selected(self, index: int) -> None:
        if index == -1:
            return
        
        self.init_variable_selector()
        self.init_time_selector()
        self.init_interp_input(True)
        
        previous_dataset = self.selected_dataset
        self.selected_dataset = self.get_dataset_name()

        if previous_dataset is not None:
            plugin_geo.remove_group(previous_dataset)

        if previous_dataset == self.selected_dataset:
            # User re-opened same file, e.g. to see new time steps while running simulation.
            # Try to load the same variable and time step.
            self.replace_variable_layer()
            self.select_time_band_in_variable_layers() 

    def on_variable_selected(self, current: Optional[QTreeWidgetItem], previous: Optional[QTreeWidgetItem]) -> None:
        if current is None:
            return
        var_name = current.data(0, Qt.UserRole)
        dataset = self.get_dataset()
        assert var_name == self.get_var_name()
        self.selected_variable[dataset.name] = var_name
        self.init_extra_dim_selector()
        self.init_interp_input(False)
        self._sync_colormap_combo(var_name)
        self.replace_variable_layer()
        self.select_time_band_in_variable_layers()

    def _sync_colormap_combo(self, var_name: str) -> None:
        """Update the colormap combo to reflect the auto-detected ramp for this variable."""
        ramp_name, invert = plugin_geo._get_var_colormap(var_name)
        idx = self.cmap_combo.findText(ramp_name)
        if idx >= 0:
            self.cmap_combo.setCurrentIndex(idx)
        self.cmap_invert.setChecked(invert)

        
    def on_time_selected(self, index: int) -> None:
        dataset = self.get_dataset()
        self.selected_time[dataset.name] = index
        self.time_label.setText('Time: ' + dataset.times[index])
        self.select_time_band_in_variable_layers()

    def on_extra_dim_selected(self, index: int) -> None:
        dataset = self.get_dataset()
        variable = self.get_variable()
        extra_dim_name = variable.extra_dim_name
        self.selected_extra_dim[(dataset.name, extra_dim_name)] = index
        
        extra_dim = dataset.extra_dims[extra_dim_name]
        self.extra_dim_label.setText(extra_dim.label + f': {extra_dim.steps[index]}')

        self.replace_variable_layer()
        self.select_time_band_in_variable_layers()

    def on_interp_toggled(self, enabled: True) -> None:
        self.extra_dim_container.setEnabled(not enabled)
        self.replace_variable_layer()

    def on_interp_btn_clicked(self) -> None:
        self.replace_variable_layer()
        self.select_time_band_in_variable_layers()

    def replace_variable_layer(self) -> None:
        if self.pause_replace_layer:
            return
        if self.is_interp_enabled() and self.get_interp_level() is None:
            return
        
        dataset = self.get_dataset()
        variable = self.get_variable()
        extra_dim_index = self.get_extra_dim_index()
        interp_level = self.get_interp_level()
        interp_vert_name = self.get_interp_vert_name()
        if interp_level is not None:
            extra_dim_index = None
        label = self.get_variable_label(variable)
        uri, dispose = gis4wrf.core.convert_wrf_nc_var_to_gdal_dataset(
            dataset.path, variable.name, extra_dim_index, interp_level, interp_vert_name)
        layer = plugin_geo.load_layers([(uri, label, variable.name)],
            group_name=dataset.name, visible=True)[0]
        dispose_after_delete(layer, dispose)

    def select_time_band_in_variable_layers(self) -> None:
        dataset = self.get_dataset()
        time_idx = self.get_time_index()
        layers = plugin_geo.get_raster_layers_in_group(dataset.name)
        for layer in layers:
            var_name = layer.shortName()
            if var_name in dataset.variables:
                plugin_geo.switch_band(layer, time_idx)
    
    def get_variable_label(self, variable: WRFNetCDFVariable) -> str:
        label = variable.name.upper()
        if variable.units:
            label += ' in ' + variable.units
        if variable.description:
            label += ' (' + variable.description + ')'
        return label
    
    def get_dataset_name(self) -> str:
        return self.dataset_selector.currentData()

    def get_var_name(self) -> str:
        return self.variable_selector.currentItem().data(0, Qt.UserRole)   

    def get_time_index(self) -> int:
        return self.time_selector.value()

    def get_extra_dim_index(self) -> Optional[int]:
        if self.extra_dim_container.isHidden():
            return None
        return self.extra_dim_selector.value()

    def is_interp_enabled(self):
        return self.interp_container.isVisible() and self.interp_container.isChecked()

    def get_interp_vert_name(self):
        if not self.is_interp_enabled():
            return None
        return self.interp_vert_selector.currentData()

    def get_interp_level(self) -> Optional[float]:
        if not self.is_interp_enabled():
            return None
        if not self.interp_input.is_valid():
            return None
        return self.interp_input.value()

    def get_dataset(self) -> Dataset:
        return self.datasets[self.get_dataset_name()]

    def get_variable(self) -> WRFNetCDFVariable:
        return self.get_dataset().variables[self.get_var_name()]

    def on_open_3d_view(self) -> None:
        """Open the interactive 3D visualisation dialog."""
        try:
            dataset  = self.get_dataset()
            variable = self.get_variable()
        except Exception:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, '3D View',
                'Please select a dataset and a variable first.')
            return

        dlg = View3DDialog(
            dataset_path    = dataset.path,
            var_name        = variable.name,
            var_description = variable.description or '',
            var_units       = variable.units or '',
            time_idx        = self.get_time_index(),
            extra_dim_idx   = self.get_extra_dim_index(),
            times           = dataset.times,
            cmap_name       = self.cmap_combo.currentText(),
            parent          = self
        )
        dlg.show()   # non-modal so the user can still interact with QGIS
