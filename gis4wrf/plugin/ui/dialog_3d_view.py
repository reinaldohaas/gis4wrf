# GIS4WRF (https://doi.org/10.5281/zenodo.1288569)
# Copyright (c) 2018 D. Meyer and M. Riechert. Licensed under MIT.

"""3-D visualisation dialog for WRF output variables.

Modes
-----
* Surface   – 2-D variable (e.g. T2, RAIN) draped over terrain (HGT)
* Levels    – 3-D variable: stacked semi-transparent horizontal contour planes
* X-Section – 3-D variable: vertical longitude or latitude cross-section

Controls
--------
* Mode radio buttons
* Time-step slider
* Pressure/eta level selector (for 3-D variables)
* Vertical exaggeration spin-box
* Colormap combo + Invert checkbox
* Export PNG button
"""

from __future__ import annotations

import os
from typing import Optional

import numpy as np

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QSlider, QComboBox, QCheckBox, QSpinBox,
    QGroupBox, QRadioButton, QButtonGroup, QSizePolicy,
    QFileDialog, QMessageBox, QWidget
)

try:
    import matplotlib
    matplotlib.use('Qt5Agg')
    from matplotlib.backends.backend_qt5agg import (
        FigureCanvasQTAgg as FigureCanvas,
        NavigationToolbar2QT as NavToolbar
    )
    from matplotlib.figure import Figure
    import matplotlib.cm as cm
    import matplotlib.colors as mcolors
    HAS_MPL = True
except Exception:
    HAS_MPL = False

try:
    from netCDF4 import Dataset as NC4Dataset
    HAS_NC4 = True
except Exception:
    HAS_NC4 = False

# ─── colour ramps offered in the dialog ───────────────────────────────────────
RAMP_CHOICES = [
    'Spectral_r', 'RdYlBu', 'RdBu', 'Blues', 'Viridis',
    'Magma', 'Plasma', 'YlOrRd', 'BuGn', 'PuOr', 'terrain', 'rainbow'
]

MODE_SURFACE  = 'surface'
MODE_LEVELS   = 'levels'
MODE_XSECTION = 'xsection'


class View3DDialog(QDialog):
    """Interactive 3-D visualisation of a WRF variable."""

    def __init__(
        self,
        dataset_path: str,
        var_name: str,
        var_description: str,
        var_units: str,
        time_idx: int,
        extra_dim_idx: Optional[int],
        times: list,
        cmap_name: str = 'Spectral_r',
        parent=None
    ):
        super().__init__(parent)
        self.dataset_path  = dataset_path
        self.var_name      = var_name
        self.var_desc      = var_description
        self.var_units     = var_units
        self.time_idx      = time_idx
        self.level_idx     = extra_dim_idx or 0
        self.times         = times
        self.cmap_name     = cmap_name
        self._alpha = 0.9        # surface transparency (0=invisible, 1=opaque)
        self._vert_exag    = 80      # vertical exaggeration factor
        self._mode         = MODE_SURFACE
        self._xsec_axis    = 'lat'   # 'lat' | 'lon'
        self._xsec_frac    = 0.5     # 0-1 position along the domain
        self._data_cache: dict = {}

        self.setWindowTitle(f'3D View — {var_name}  ({var_description})')
        geom = QGuiApplication.primaryScreen().geometry()
        self.resize(int(geom.width() * 0.65), int(geom.height() * 0.75))

        if not HAS_MPL or not HAS_NC4:
            missing = 'matplotlib' if not HAS_MPL else 'netCDF4'
            lay = QVBoxLayout()
            lay.addWidget(QLabel(f'❌  {missing} library not found – cannot open 3D view.'))
            self.setLayout(lay)
            return

        try:
            self._load_nc()
        except Exception as exc:
            lay = QVBoxLayout()
            lay.addWidget(QLabel(f'❌  Error loading data:\n{exc}'))
            self.setLayout(lay)
            return

        self._build_ui()
        self._update_plot()

    # ── data loading ──────────────────────────────────────────────────────────

    def _load_nc(self) -> None:
        """Load coordinates, terrain and the variable array from the NetCDF."""
        with NC4Dataset(self.dataset_path) as ds:
            # Lat / lon 2-D grids
            def _read(name):
                v = ds.variables.get(name)
                return np.squeeze(v[:]) if v is not None else None

            xlat  = _read('XLAT')   # (Time, south_north, west_east) or (SN, WE)
            xlong = _read('XLONG')

            if xlat is not None and xlat.ndim == 3:
                xlat  = xlat[0]
                xlong = xlong[0]

            self.lats = xlat   # (SN, WE)  or None
            self.lons = xlong

            # Terrain height
            hgt = ds.variables.get('HGT')
            self.terrain = np.squeeze(hgt[0]) if hgt is not None else None

            # Target variable
            v = ds.variables.get(self.var_name)
            if v is None:
                raise KeyError(f'Variable "{self.var_name}" not found in NetCDF')

            raw = v[:]   # (Time, [level], SN, WE)
            if hasattr(raw, 'data'):
                raw = np.ma.filled(raw, np.nan)

            self.var_all = raw          # full array
            self.is_3d   = raw.ndim == 4
            self.n_times = raw.shape[0]
            self.n_levs  = raw.shape[1] if self.is_3d else 1

    def _get_slice(self) -> np.ndarray:
        """Return a 2-D (SN, WE) slice for the current time and level."""
        key = (self.time_idx, self.level_idx)
        if key not in self._data_cache:
            if self.is_3d:
                sl = self.var_all[self.time_idx, self.level_idx]
            else:
                sl = self.var_all[self.time_idx]
            if sl.ndim == 3:          # still 3-D (SN, WE, ?) → take first
                sl = sl[..., 0]
            self._data_cache[key] = sl
        return self._data_cache[key]

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(4)

        # ── matplotlib canvas ────────────────────────────────────────────────
        self.fig    = Figure(facecolor='#1a1a2e', tight_layout=True)
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        nav = NavToolbar(self.canvas, self)
        nav.setStyleSheet('background:#1a1a2e; color:white;')
        root.addWidget(nav)
        root.addWidget(self.canvas)

        # ── control strip ────────────────────────────────────────────────────
        ctrl = QWidget()
        ctrl_row = QHBoxLayout(ctrl)
        ctrl_row.setSpacing(12)

        # Mode
        mode_box = QGroupBox('Mode')
        mode_lay = QVBoxLayout(mode_box)
        self._mode_group = QButtonGroup(self)
        for label, mode in [('Surface (2D)', MODE_SURFACE),
                             ('Level slices (3D)', MODE_LEVELS),
                             ('Cross-section (3D)', MODE_XSECTION)]:
            rb = QRadioButton(label)
            rb.setChecked(mode == self._mode)
            if not self.is_3d and mode != MODE_SURFACE:
                rb.setEnabled(False)
            rb.toggled.connect(lambda on, m=mode: self._on_mode(m) if on else None)
            self._mode_group.addButton(rb)
            mode_lay.addWidget(rb)
        ctrl_row.addWidget(mode_box)

        # Cross-section sub-controls
        xsec_box = QGroupBox('Cross-section axis')
        xsec_lay = QVBoxLayout(xsec_box)
        self._xsec_lat = QRadioButton('Latitude (EW cut)')
        self._xsec_lon = QRadioButton('Longitude (NS cut)')
        self._xsec_lat.setChecked(True)
        self._xsec_lat.toggled.connect(lambda on: self._on_xsec_axis('lat') if on else None)
        self._xsec_lon.toggled.connect(lambda on: self._on_xsec_axis('lon') if on else None)
        xsec_lay.addWidget(self._xsec_lat)
        xsec_lay.addWidget(self._xsec_lon)
        self._xsec_slider = QSlider(Qt.Horizontal)
        self._xsec_slider.setRange(0, 100)
        self._xsec_slider.setValue(50)
        self._xsec_slider.valueChanged.connect(self._on_xsec_pos)
        self._xsec_label = QLabel('Position: 50 %')
        xsec_lay.addWidget(self._xsec_label)
        xsec_lay.addWidget(self._xsec_slider)
        ctrl_row.addWidget(xsec_box)

        # Time
        time_box = QGroupBox('Time step')
        time_lay = QVBoxLayout(time_box)
        self._time_label = QLabel(self.times[self.time_idx] if self.times else '—')
        self._time_slider = QSlider(Qt.Horizontal)
        self._time_slider.setRange(0, max(0, len(self.times) - 1))
        self._time_slider.setValue(self.time_idx)
        self._time_slider.valueChanged.connect(self._on_time)
        time_lay.addWidget(self._time_label)
        time_lay.addWidget(self._time_slider)
        ctrl_row.addWidget(time_box)

        # Level (3-D only) — now a slider for live scrubbing
        lev_box = QGroupBox('Vertical level')
        lev_lay = QVBoxLayout(lev_box)
        self._lev_label = QLabel(f'Level: {self.level_idx}')
        self._lev_slider = QSlider(Qt.Horizontal)
        self._lev_slider.setRange(0, max(0, self.n_levs - 1))
        self._lev_slider.setValue(self.level_idx)
        self._lev_slider.setEnabled(self.is_3d)
        self._lev_slider.setTickPosition(QSlider.TicksBelow)
        self._lev_slider.setTickInterval(max(1, self.n_levs // 10))
        self._lev_slider.valueChanged.connect(self._on_level)
        lev_lay.addWidget(self._lev_label)
        lev_lay.addWidget(self._lev_slider)
        ctrl_row.addWidget(lev_box)

        # Appearance
        app_box = QGroupBox('Appearance')
        app_lay = QVBoxLayout(app_box)

        cmap_row = QHBoxLayout()
        cmap_row.addWidget(QLabel('Colormap:'))
        self._cmap_combo = QComboBox()
        for n in RAMP_CHOICES:
            self._cmap_combo.addItem(n)
        cmap_in = self.cmap_name if self.cmap_name in RAMP_CHOICES else 'Spectral_r'
        self._cmap_combo.setCurrentText(cmap_in)
        self._cmap_combo.currentTextChanged.connect(self._on_cmap)
        cmap_row.addWidget(self._cmap_combo)
        app_lay.addLayout(cmap_row)

        # Transparency slider
        alpha_row = QHBoxLayout()
        alpha_row.addWidget(QLabel('Transparency:'))
        self._alpha_slider = QSlider(Qt.Horizontal)
        self._alpha_slider.setRange(5, 100)   # 5 % … 100 %
        self._alpha_slider.setValue(int(self._alpha * 100))
        self._alpha_slider.setTickPosition(QSlider.TicksBelow)
        self._alpha_slider.setTickInterval(10)
        self._alpha_label = QLabel(f'{int(self._alpha*100)} %')
        self._alpha_slider.valueChanged.connect(self._on_alpha)
        alpha_row.addWidget(self._alpha_slider)
        alpha_row.addWidget(self._alpha_label)
        app_lay.addLayout(alpha_row)

        # Vertical exaggeration slider
        vexag_row = QHBoxLayout()
        vexag_row.addWidget(QLabel('Vert. exag ×:'))
        self._vexag_slider = QSlider(Qt.Horizontal)
        self._vexag_slider.setRange(1, 500)
        self._vexag_slider.setValue(self._vert_exag)
        self._vexag_slider.setTickPosition(QSlider.TicksBelow)
        self._vexag_slider.setTickInterval(50)
        self._vexag_label = QLabel(f'× {self._vert_exag}')
        self._vexag_slider.valueChanged.connect(self._on_vexag)
        vexag_row.addWidget(self._vexag_slider)
        vexag_row.addWidget(self._vexag_label)
        app_lay.addLayout(vexag_row)

        ctrl_row.addWidget(app_box)

        # Export
        exp_box = QGroupBox('Export')
        exp_lay = QVBoxLayout(exp_box)
        export_btn = QPushButton('💾 Save PNG')
        export_btn.clicked.connect(self._export_png)
        exp_lay.addWidget(export_btn)
        ctrl_row.addWidget(exp_box)

        ctrl_row.addStretch()
        root.addWidget(ctrl)

    # ── plot ──────────────────────────────────────────────────────────────────

    def _update_plot(self) -> None:
        self.fig.clear()
        try:
            if self._mode == MODE_SURFACE:
                self._plot_surface()
            elif self._mode == MODE_LEVELS:
                self._plot_levels()
            elif self._mode == MODE_XSECTION:
                self._plot_xsection()
        except Exception as exc:
            ax = self.fig.add_subplot(111)
            ax.set_facecolor('#1a1a2e')
            ax.text(0.5, 0.5, f'Plot error:\n{exc}',
                    transform=ax.transAxes, ha='center', va='center',
                    color='#ff6b6b', fontsize=11)
        self.canvas.draw()

    def _cmap(self):
        return cm.get_cmap(self._cmap_combo.currentText())

    def _make_ax3d(self):
        ax = self.fig.add_subplot(111, projection='3d')
        ax.set_facecolor('#1a1a2e')
        self.fig.patch.set_facecolor('#1a1a2e')
        for pane in (ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane):
            pane.fill = False
        for spine in ax.spines.values():
            spine.set_edgecolor('#555')
        ax.tick_params(colors='#aaa', labelsize=7)
        ax.xaxis.label.set_color('#ccc')
        ax.yaxis.label.set_color('#ccc')
        ax.zaxis.label.set_color('#ccc')
        ax.set_title(
            f'{self.var_name}  –  {self.times[self.time_idx] if self.times else ""}',
            color='white', fontsize=11, pad=10
        )
        return ax

    def _coords(self):
        """Return (lons, lats) 2-D arrays or simple index grids."""
        sl = self._get_slice()
        ny, nx = sl.shape
        if self.lons is not None:
            return self.lons[:ny, :nx], self.lats[:ny, :nx]
        return np.arange(nx)[np.newaxis, :] * np.ones((ny, 1)), \
               np.arange(ny)[:, np.newaxis] * np.ones((1, nx))

    def _subsample(self, *arrays, max_pts=80):
        """Subsample 2-D arrays to at most max_pts×max_pts for speed."""
        ny, nx = arrays[0].shape
        sy = max(1, ny // max_pts)
        sx = max(1, nx // max_pts)
        return tuple(a[::sy, ::sx] for a in arrays)

    # ── Surface mode ──────────────────────────────────────────────────────────

    def _plot_surface(self) -> None:
        data  = self._get_slice()
        lons, lats = self._coords()
        terrain = self.terrain if self.terrain is not None else np.zeros_like(data)

        # Crop terrain to data shape
        ny, nx = data.shape
        terrain = terrain[:ny, :nx]

        # Subsample
        data, lons, lats, terrain = self._subsample(data, lons, lats, terrain)

        # Normalise
        vmin = np.nanpercentile(data, 2)
        vmax = np.nanpercentile(data, 98)
        if vmin == vmax:
            vmax = vmin + 1

        norm   = mcolors.Normalize(vmin, vmax)
        colors = self._cmap()(norm(np.nan_to_num(data, nan=vmin)))

        ax = self._make_ax3d()
        ax.plot_surface(
            lons, lats, terrain * self._vert_exag,
            facecolors=colors, shade=True, alpha=self._alpha, linewidth=0,
            antialiased=False
        )

        ax.set_xlabel('Longitude' if self.lons is not None else 'X', color='#ccc')
        ax.set_ylabel('Latitude'  if self.lats is not None else 'Y', color='#ccc')
        ax.set_zlabel(f'Height × {self._vert_exag}', color='#ccc')

        # Colour-bar (inset axes trick for 3-D)
        sm = cm.ScalarMappable(cmap=self._cmap(), norm=norm)
        sm.set_array([])
        cb = self.fig.colorbar(sm, ax=ax, shrink=0.5, pad=0.1,
                                orientation='vertical', fraction=0.03)
        cb.set_label(f'{self.var_name} [{self.var_units}]', color='#ccc')
        cb.ax.yaxis.set_tick_params(color='#aaa')
        for label in cb.ax.get_yticklabels():
            label.set_color('#aaa')

    # ── Level slices mode ─────────────────────────────────────────────────────

    def _plot_levels(self) -> None:
        if not self.is_3d:
            raise ValueError('Level-slices mode requires a 3-D variable.')

        lons, lats = self._coords()
        t = self.time_idx
        n = self.var_all.shape[1]

        # Pick up to 6 evenly spaced levels
        lev_indices = np.linspace(0, n - 1, min(n, 6), dtype=int)

        ax = self._make_ax3d()

        vmin = np.nanpercentile(self.var_all[t], 2)
        vmax = np.nanpercentile(self.var_all[t], 98)
        if vmin == vmax:
            vmax = vmin + 1

        ny, nx = lons.shape
        lons_s, lats_s = self._subsample(lons, lats)

        for i, ilev in enumerate(lev_indices):
            sl = self.var_all[t, ilev, :ny, :nx]
            sl_s, = self._subsample(sl)
            z_val = float(ilev) / max(n - 1, 1) * self._vert_exag * 500

            norm = mcolors.Normalize(vmin, vmax)
            colors = self._cmap()(norm(np.nan_to_num(sl_s, nan=vmin)))

            ax.plot_surface(
                lons_s, lats_s,
                np.full_like(lons_s, z_val),
                facecolors=colors, shade=False,
                alpha=max(0.05, self._alpha * 0.5), linewidth=0, antialiased=False
            )

        ax.set_xlabel('Longitude' if self.lons is not None else 'X', color='#ccc')
        ax.set_ylabel('Latitude'  if self.lats is not None else 'Y', color='#ccc')
        ax.set_zlabel('Eta level (scaled)', color='#ccc')

        sm = cm.ScalarMappable(cmap=self._cmap(), norm=mcolors.Normalize(vmin, vmax))
        sm.set_array([])
        cb = self.fig.colorbar(sm, ax=ax, shrink=0.5, pad=0.1,
                                orientation='vertical', fraction=0.03)
        cb.set_label(f'{self.var_name} [{self.var_units}]', color='#ccc')
        for label in cb.ax.get_yticklabels():
            label.set_color('#aaa')

    # ── Cross-section mode ────────────────────────────────────────────────────

    def _plot_xsection(self) -> None:
        if not self.is_3d:
            raise ValueError('Cross-section mode requires a 3-D variable.')

        t  = self.time_idx
        nl = self.var_all.shape[1]
        ny, nx = self.var_all.shape[2], self.var_all.shape[3]

        lons, lats = self._coords()

        frac = self._xsec_frac
        if self._xsec_axis == 'lat':
            # Fixed latitude index → east-west cut
            iy   = int(frac * (ny - 1))
            horiz = lons[iy, :]          # longitude axis
            data  = self.var_all[t, :, iy, :]   # (level, WE)
            xlabel = 'Longitude'
        else:
            # Fixed longitude index → north-south cut
            ix   = int(frac * (nx - 1))
            horiz = lats[:, ix]          # latitude axis
            data  = self.var_all[t, :, :, ix]   # (level, SN)
            xlabel = 'Latitude'

        levels = np.arange(nl)
        H, X   = np.meshgrid(levels, horiz, indexing='ij')  # (level, horiz)

        ax = self._make_ax3d()

        vmin = np.nanpercentile(data, 2)
        vmax = np.nanpercentile(data, 98)
        if vmin == vmax:
            vmax = vmin + 1

        norm   = mcolors.Normalize(vmin, vmax)
        colors = self._cmap()(norm(np.nan_to_num(data, nan=vmin)))

        ax.plot_surface(
            X, H, np.zeros_like(X),
            facecolors=colors, shade=False, alpha=self._alpha, linewidth=0
        )
        ax.set_xlabel(xlabel, color='#ccc')
        ax.set_ylabel('Eta level', color='#ccc')
        ax.set_zlabel('', color='#ccc')

        sm = cm.ScalarMappable(cmap=self._cmap(), norm=mcolors.Normalize(vmin, vmax))
        sm.set_array([])
        cb = self.fig.colorbar(sm, ax=ax, shrink=0.5, pad=0.1,
                                orientation='vertical', fraction=0.03)
        cb.set_label(f'{self.var_name} [{self.var_units}]', color='#ccc')
        for label in cb.ax.get_yticklabels():
            label.set_color('#aaa')

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_mode(self, mode: str) -> None:
        self._mode = mode
        self._update_plot()

    def _on_xsec_axis(self, axis: str) -> None:
        self._xsec_axis = axis
        self._update_plot()

    def _on_xsec_pos(self, val: int) -> None:
        self._xsec_frac = val / 100.0
        self._xsec_label.setText(f'Position: {val} %')
        if self._mode == MODE_XSECTION:
            self._update_plot()

    def _on_time(self, idx: int) -> None:
        self.time_idx = idx
        self._data_cache.clear()
        if self.times:
            self._time_label.setText(self.times[idx])
        self._update_plot()

    def _on_level(self, idx: int) -> None:
        self.level_idx = idx
        self._lev_label.setText(f'Level: {idx}')
        self._data_cache.clear()
        self._update_plot()

    def _on_cmap(self, name: str) -> None:
        self.cmap_name = name
        self._update_plot()

    def _on_alpha(self, val: int) -> None:
        self._alpha = val / 100.0
        self._alpha_label.setText(f'{val} %')
        self._update_plot()

    def _on_vexag(self, val: int) -> None:
        self._vert_exag = val
        self._vexag_label.setText(f'× {val}')
        self._update_plot()

    def _export_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, 'Save 3D plot', f'{self.var_name}_3d.png', 'PNG (*.png)'
        )
        if path:
            self.fig.savefig(path, dpi=200, bbox_inches='tight',
                             facecolor=self.fig.get_facecolor())
            QMessageBox.information(self, 'Saved', f'Image saved to:\n{path}')
