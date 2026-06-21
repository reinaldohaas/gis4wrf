# GIS4WRF — dialog_3d_view.py
# Interactive 3-D visualisation for WRF output variables.
#
# Modes
# -----
#   Surface    – variable coloured over terrain (Z = terrain height)
#   Level slices – stacked horizontal planes for 3-D variables
#   Cross cuts – EW curtain + NS curtain simultaneously, each with its own
#                position slider.  Z axis is the vertical/eta level.
#   Contour 2D – fast top-down filled-contour with isolines
#
# Optional overlays
# -----------------
#   Wind vectors – quiver arrows from U10/V10 (or U/V at current level)
#   Terrain base – semi-transparent terrain shown below cross cuts
#
# Controls
# --------
#   NS position slider  – latitude of the East-West curtain
#   EW position slider  – longitude of the North-South curtain
#   Level slider        – eta/pressure level (for Surface + Cuts modes)
#   Time slider         – time step
#   Alpha slider        – surface transparency
#   Vert. exag slider   – vertical exaggeration of terrain height
#   Colormap combo
#   Wind checkbox
#   Terrain base checkbox
#   Save PNG button

from __future__ import annotations
import os
from typing import Optional
import numpy as np

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QSlider, QComboBox, QCheckBox,
    QGroupBox, QRadioButton, QButtonGroup, QSizePolicy,
    QFileDialog, QMessageBox, QWidget, QGridLayout,
    QScrollArea, QFrame
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

# ── colour ramps ──────────────────────────────────────────────────────────────
RAMP_CHOICES = [
    'Spectral_r', 'RdYlBu', 'RdBu', 'Blues', 'Viridis',
    'Magma', 'Plasma', 'YlOrRd', 'BuGn', 'PuOr', 'terrain', 'rainbow',
    'coolwarm', 'jet', 'hsv', 'bwr'
]

MODE_SURFACE  = 'surface'
MODE_LEVELS   = 'levels'
MODE_CUTS     = 'cuts'
MODE_CONTOUR  = 'contour2d'


class View3DDialog(QDialog):
    """Interactive 3-D / 2-D visualisation of a WRF variable."""

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
        self.dataset_path = dataset_path
        self.var_name     = var_name
        self.var_desc     = var_description
        self.var_units    = var_units
        self.time_idx     = time_idx
        self.level_idx    = extra_dim_idx or 0
        self.times        = times
        self.cmap_name    = cmap_name

        # State
        self._mode        = MODE_SURFACE
        self._alpha       = 0.9
        self._vert_exag   = 100     # terrain vertical exaggeration
        self._ns_pos      = 0.5     # fraction 0-1 → latitude of EW curtain
        self._ew_pos      = 0.5     # fraction 0-1 → longitude of NS curtain
        self._show_terrain= True
        self._show_wind   = False
        self._data_cache: dict = {}

        self.setWindowTitle(f'3D View — {var_name}  ({var_description})')
        geom = QGuiApplication.primaryScreen().geometry()
        self.resize(int(geom.width() * 0.70), int(geom.height() * 0.80))

        if not HAS_MPL or not HAS_NC4:
            missing = 'matplotlib' if not HAS_MPL else 'netCDF4'
            lay = QVBoxLayout()
            lay.addWidget(QLabel(f'❌  {missing} not found – cannot open 3D view.'))
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
        with NC4Dataset(self.dataset_path) as ds:
            def _read(name):
                v = ds.variables.get(name)
                return np.squeeze(np.array(v[:])) if v is not None else None

            xlat  = _read('XLAT')
            xlong = _read('XLONG')
            if xlat is not None and xlat.ndim == 3:
                xlat, xlong = xlat[0], xlong[0]

            self.lats    = xlat
            self.lons    = xlong
            self.terrain = None
            hgt = ds.variables.get('HGT')
            if hgt is not None:
                self.terrain = np.squeeze(np.array(hgt[0]))

            v = ds.variables.get(self.var_name)
            if v is None:
                raise KeyError(f'"{self.var_name}" not found')
            raw = np.ma.filled(np.array(v[:]), np.nan)

            self.var_all  = raw
            self.is_3d    = raw.ndim == 4
            self.n_times  = raw.shape[0]
            self.n_levs   = raw.shape[1] if self.is_3d else 1

            # Pre-load wind if available
            self._has_wind = False
            for uname, vname in [('U10','V10'), ('U','V')]:
                uu = ds.variables.get(uname)
                vv = ds.variables.get(vname)
                if uu is not None and vv is not None:
                    self._wind_u = np.array(uu[:])
                    self._wind_v = np.array(vv[:])
                    self._wind_3d = self._wind_u.ndim == 4
                    self._has_wind = True
                    break

    def _get_2d_slice(self) -> np.ndarray:
        key = (self.time_idx, self.level_idx)
        if key not in self._data_cache:
            sl = self.var_all[self.time_idx, self.level_idx] if self.is_3d \
                 else self.var_all[self.time_idx]
            if sl.ndim == 3:
                sl = sl[..., 0]
            self._data_cache[key] = sl
        return self._data_cache[key]

    def _coords(self, ny=None, nx=None):
        sl = self._get_2d_slice()
        ny = ny or sl.shape[0]
        nx = nx or sl.shape[1]
        if self.lons is not None:
            return self.lons[:ny, :nx], self.lats[:ny, :nx]
        X = np.arange(nx)[np.newaxis, :] * np.ones((ny, 1))
        Y = np.arange(ny)[:, np.newaxis] * np.ones((1, nx))
        return X, Y

    def _sub(self, *arrays, n=70):
        """Subsample 2-D arrays to n×n for speed."""
        ny, nx = arrays[0].shape[:2]
        sy, sx = max(1, ny // n), max(1, nx // n)
        return tuple(a[::sy, ::sx] for a in arrays)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(4)

        # ── canvas ───────────────────────────────────────────────────────────
        self.fig    = Figure(facecolor='#1a1a2e', tight_layout=True)
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        nav = NavToolbar(self.canvas, self)
        nav.setStyleSheet('background:#1a1a2e; color:white;')
        root.addWidget(nav)
        root.addWidget(self.canvas)

        # ── control strip (scrollable) ────────────────────────────────────────
        strip = QWidget()
        row   = QHBoxLayout(strip)
        row.setSpacing(8)

        # ── Mode ──────────────────────────────────────────────────────────────
        mode_box = QGroupBox('Mode')
        mode_lay = QVBoxLayout(mode_box)
        self._mode_group = QButtonGroup(self)
        for label, mode in [('🗺 Surface',       MODE_SURFACE),
                             ('📚 Level slices',  MODE_LEVELS),
                             ('✂️ Cross cuts',     MODE_CUTS),
                             ('📐 Contour 2D',    MODE_CONTOUR)]:
            rb = QRadioButton(label)
            rb.setChecked(mode == self._mode)
            if not self.is_3d and mode in (MODE_LEVELS, MODE_CUTS):
                rb.setEnabled(False)
            rb.toggled.connect(lambda on, m=mode: self._on_mode(m) if on else None)
            self._mode_group.addButton(rb)
            mode_lay.addWidget(rb)

        self._chk_terrain = QCheckBox('🏔 Terrain base')
        self._chk_terrain.setChecked(self._show_terrain)
        self._chk_terrain.toggled.connect(self._on_terrain_toggle)
        mode_lay.addWidget(self._chk_terrain)

        self._chk_wind = QCheckBox('💨 Wind vectors')
        self._chk_wind.setChecked(False)
        self._chk_wind.setEnabled(self._has_wind)
        if not self._has_wind:
            self._chk_wind.setToolTip('U10/V10 not found in this file')
        self._chk_wind.toggled.connect(self._on_wind_toggle)
        mode_lay.addWidget(self._chk_wind)
        row.addWidget(mode_box)

        # ── Cross-section positions ───────────────────────────────────────────
        cuts_box = QGroupBox('Cross-section position')
        cuts_lay = QGridLayout(cuts_box)

        cuts_lay.addWidget(QLabel('EW cut\n(N↔S pos.):'), 0, 0)
        self._ns_label  = QLabel(f'{int(self._ns_pos*100)} %')
        self._ns_slider = QSlider(Qt.Horizontal)
        self._ns_slider.setRange(0, 100)
        self._ns_slider.setValue(int(self._ns_pos * 100))
        self._ns_slider.setTickPosition(QSlider.TicksBelow)
        self._ns_slider.setTickInterval(10)
        self._ns_slider.valueChanged.connect(self._on_ns)
        cuts_lay.addWidget(self._ns_slider, 0, 1)
        cuts_lay.addWidget(self._ns_label,  0, 2)

        cuts_lay.addWidget(QLabel('NS cut\n(E↔W pos.):'), 1, 0)
        self._ew_label  = QLabel(f'{int(self._ew_pos*100)} %')
        self._ew_slider = QSlider(Qt.Horizontal)
        self._ew_slider.setRange(0, 100)
        self._ew_slider.setValue(int(self._ew_pos * 100))
        self._ew_slider.setTickPosition(QSlider.TicksBelow)
        self._ew_slider.setTickInterval(10)
        self._ew_slider.valueChanged.connect(self._on_ew)
        cuts_lay.addWidget(self._ew_slider, 1, 1)
        cuts_lay.addWidget(self._ew_label,  1, 2)

        row.addWidget(cuts_box)

        # ── Time + Level ──────────────────────────────────────────────────────
        tl_box = QGroupBox('Time / Level')
        tl_lay = QVBoxLayout(tl_box)

        self._time_label  = QLabel(self.times[self.time_idx] if self.times else '—')
        self._time_label.setStyleSheet('color:#88ccff; font-size:10px;')
        self._time_slider = QSlider(Qt.Horizontal)
        self._time_slider.setRange(0, max(0, len(self.times) - 1))
        self._time_slider.setValue(self.time_idx)
        self._time_slider.setTickPosition(QSlider.TicksBelow)
        self._time_slider.setTickInterval(max(1, len(self.times)//10))
        self._time_slider.valueChanged.connect(self._on_time)
        tl_lay.addWidget(QLabel('Time step:'))
        tl_lay.addWidget(self._time_label)
        tl_lay.addWidget(self._time_slider)

        self._lev_label  = QLabel(f'Level: {self.level_idx}')
        self._lev_slider = QSlider(Qt.Horizontal)
        self._lev_slider.setRange(0, max(0, self.n_levs - 1))
        self._lev_slider.setValue(self.level_idx)
        self._lev_slider.setEnabled(self.is_3d)
        self._lev_slider.setTickPosition(QSlider.TicksBelow)
        self._lev_slider.setTickInterval(max(1, self.n_levs // 10))
        self._lev_slider.valueChanged.connect(self._on_level)
        tl_lay.addWidget(QLabel('Vertical level:'))
        tl_lay.addWidget(self._lev_label)
        tl_lay.addWidget(self._lev_slider)
        row.addWidget(tl_box)

        # ── Appearance ────────────────────────────────────────────────────────
        app_box = QGroupBox('Appearance')
        app_lay = QVBoxLayout(app_box)

        app_lay.addWidget(QLabel('Colormap:'))
        self._cmap_combo = QComboBox()
        for n in RAMP_CHOICES:
            self._cmap_combo.addItem(n)
        cin = self.cmap_name if self.cmap_name in RAMP_CHOICES else 'Spectral_r'
        self._cmap_combo.setCurrentText(cin)
        self._cmap_combo.currentTextChanged.connect(self._on_cmap)
        app_lay.addWidget(self._cmap_combo)

        # Alpha slider
        a_row = QHBoxLayout()
        a_row.addWidget(QLabel('Transparency:'))
        self._alpha_slider = QSlider(Qt.Horizontal)
        self._alpha_slider.setRange(5, 100)
        self._alpha_slider.setValue(int(self._alpha * 100))
        self._alpha_slider.setTickPosition(QSlider.TicksBelow)
        self._alpha_slider.setTickInterval(10)
        self._alpha_label = QLabel(f'{int(self._alpha*100)} %')
        self._alpha_slider.valueChanged.connect(self._on_alpha)
        a_row.addWidget(self._alpha_slider)
        a_row.addWidget(self._alpha_label)
        app_lay.addLayout(a_row)

        # Vert exag slider
        v_row = QHBoxLayout()
        v_row.addWidget(QLabel('Vert. exag ×:'))
        self._vexag_slider = QSlider(Qt.Horizontal)
        self._vexag_slider.setRange(1, 500)
        self._vexag_slider.setValue(self._vert_exag)
        self._vexag_slider.setTickPosition(QSlider.TicksBelow)
        self._vexag_slider.setTickInterval(50)
        self._vexag_label = QLabel(f'× {self._vert_exag}')
        self._vexag_slider.valueChanged.connect(self._on_vexag)
        v_row.addWidget(self._vexag_slider)
        v_row.addWidget(self._vexag_label)
        app_lay.addLayout(v_row)

        save_btn = QPushButton('💾 Save PNG')
        save_btn.clicked.connect(self._export_png)
        app_lay.addWidget(save_btn)
        row.addWidget(app_box)

        row.addStretch()
        root.addWidget(strip)

    # ── plotting dispatcher ───────────────────────────────────────────────────

    def _update_plot(self) -> None:
        self.fig.clear()
        try:
            if   self._mode == MODE_SURFACE:  self._plot_surface()
            elif self._mode == MODE_LEVELS:   self._plot_levels()
            elif self._mode == MODE_CUTS:     self._plot_both_cuts()
            elif self._mode == MODE_CONTOUR:  self._plot_contour2d()
        except Exception as exc:
            ax = self.fig.add_subplot(111)
            ax.set_facecolor('#1a1a2e')
            ax.text(0.5, 0.5, f'Plot error:\n{exc}',
                    transform=ax.transAxes, ha='center', va='center',
                    color='#ff6b6b', fontsize=10)
        self.canvas.draw()

    def _cmap_obj(self):
        return cm.get_cmap(self._cmap_combo.currentText())

    def _norm(self, data):
        vmin = np.nanpercentile(data, 2)
        vmax = np.nanpercentile(data, 98)
        if vmin == vmax:
            vmax = vmin + 1
        return mcolors.Normalize(vmin, vmax), vmin, vmax

    def _make_ax3d(self, title_extra=''):
        ax = self.fig.add_subplot(111, projection='3d')
        ax.set_facecolor('#0d0d1a')
        self.fig.patch.set_facecolor('#1a1a2e')
        for pane in (ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane):
            pane.fill = False
            pane.set_edgecolor('#333')
        ax.tick_params(colors='#aaa', labelsize=7)
        for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
            axis.label.set_color('#ccc')
        t_str = self.times[self.time_idx] if self.times else ''
        ax.set_title(
            f'{self.var_name}  [{self.var_units}]  —  {t_str} {title_extra}',
            color='white', fontsize=10, pad=8
        )
        return ax

    def _make_ax2d(self):
        ax = self.fig.add_subplot(111)
        ax.set_facecolor('#0d0d1a')
        self.fig.patch.set_facecolor('#1a1a2e')
        ax.tick_params(colors='#aaa', labelsize=8)
        ax.xaxis.label.set_color('#ccc')
        ax.yaxis.label.set_color('#ccc')
        ax.spines[:].set_color('#555')
        t_str = self.times[self.time_idx] if self.times else ''
        ax.set_title(f'{self.var_name}  [{self.var_units}]  —  {t_str}',
                     color='white', fontsize=10)
        return ax

    def _add_colorbar(self, ax, cmap, norm, vmin, vmax):
        sm = cm.ScalarMappable(cmap=cmap, norm=norm)
        sm.set_array([])
        cb = self.fig.colorbar(sm, ax=ax, shrink=0.55, pad=0.08,
                                fraction=0.03, orientation='vertical')
        cb.set_label(f'{self.var_name} [{self.var_units}]', color='#ccc', fontsize=8)
        cb.ax.yaxis.set_tick_params(color='#aaa', labelsize=7)
        for lbl in cb.ax.get_yticklabels():
            lbl.set_color('#aaa')

    # ── Surface mode ──────────────────────────────────────────────────────────

    def _plot_surface(self) -> None:
        data = self._get_2d_slice()
        ny, nx = data.shape
        lons, lats = self._coords(ny, nx)
        terrain = (self.terrain[:ny, :nx] if self.terrain is not None
                   else np.zeros_like(data))

        data_s, lons_s, lats_s, terr_s = self._sub(data, lons, lats, terrain)

        cmap = self._cmap_obj()
        norm, vmin, vmax = self._norm(data_s)
        colors = cmap(norm(np.nan_to_num(data_s, nan=vmin)))

        ax = self._make_ax3d(f'  (level {self.level_idx})' if self.is_3d else '')
        ax.plot_surface(lons_s, lats_s, terr_s * self._vert_exag,
                        facecolors=colors, shade=True,
                        alpha=self._alpha, linewidth=0, antialiased=False)

        ax.set_xlabel('Longitude' if self.lons is not None else 'X')
        ax.set_ylabel('Latitude'  if self.lats is not None else 'Y')
        ax.set_zlabel(f'Height × {self._vert_exag}')

        if self._show_wind and self._has_wind:
            self._add_wind_quiver(ax, terr_s * self._vert_exag, lons_s, lats_s)

        self._add_colorbar(ax, cmap, norm, vmin, vmax)

    # ── Level slices mode ─────────────────────────────────────────────────────

    def _plot_levels(self) -> None:
        if not self.is_3d:
            raise ValueError('Level slices requires a 3-D variable')

        t  = self.time_idx
        nl = self.var_all.shape[1]
        ny, nx = self.var_all.shape[2], self.var_all.shape[3]
        lons, lats = self._coords(ny, nx)

        lev_indices = np.linspace(0, nl - 1, min(nl, 7), dtype=int)
        cmap = self._cmap_obj()
        norm, vmin, vmax = self._norm(self.var_all[t])

        ax = self._make_ax3d()
        z_max = 1000.0  # arbitrary height units

        for ilev in lev_indices:
            sl = self.var_all[t, ilev, :ny, :nx]
            sl_s, lons_s, lats_s = self._sub(sl, lons, lats)
            z_val = (ilev / max(nl - 1, 1)) * z_max
            colors = cmap(norm(np.nan_to_num(sl_s, nan=vmin)))
            ax.plot_surface(lons_s, lats_s, np.full_like(lons_s, z_val),
                            facecolors=colors, shade=False,
                            alpha=max(0.05, self._alpha * 0.55),
                            linewidth=0, antialiased=False)

        ax.set_xlabel('Longitude' if self.lons is not None else 'X')
        ax.set_ylabel('Latitude'  if self.lats is not None else 'Y')
        ax.set_zlabel('Eta level (0 = surface, 1000 = top)')
        self._add_colorbar(ax, cmap, norm, vmin, vmax)

    # ── Both cuts mode (correct 3-D orientation) ──────────────────────────────

    def _plot_both_cuts(self) -> None:
        if not self.is_3d:
            raise ValueError('Cross cuts requires a 3-D variable')

        t  = self.time_idx
        nl = self.var_all.shape[1]
        ny, nx = self.var_all.shape[2], self.var_all.shape[3]
        lons, lats = self._coords(ny, nx)

        cmap  = self._cmap_obj()
        norm, vmin, vmax = self._norm(self.var_all[t])
        ax    = self._make_ax3d()

        # Level heights: level 0 at Z=0 (surface), level nl-1 at Z=1
        # multiplied by vert_exag to give sensible visual scale
        lev_z = np.linspace(0, self._vert_exag * 1.0, nl)

        # ── EW curtain (fixed NS position → fixed latitude row iy) ──────────
        iy = max(0, min(ny - 1, int(self._ns_pos * (ny - 1))))
        lon_row  = lons[iy, :]            # (WE,)
        lat_val  = lats[iy, 0] if self.lats is not None else float(iy)
        data_ew  = self.var_all[t, :, iy, :]  # (nl, WE)

        step_x = max(1, nx // 60)
        lon_s   = lon_row[::step_x]       # subsampled lon
        data_ews = data_ew[:, ::step_x]   # (nl, nx_s)

        LON_ew, LEV_ew = np.meshgrid(lon_s, lev_z)
        LAT_ew = np.full_like(LON_ew, lat_val)
        col_ew  = cmap(norm(np.nan_to_num(data_ews, nan=vmin)))
        ax.plot_surface(LON_ew, LAT_ew, LEV_ew,
                        facecolors=col_ew, shade=False,
                        alpha=self._alpha, linewidth=0, antialiased=False)

        # ── NS curtain (fixed EW position → fixed longitude column ix) ───────
        ix = max(0, min(nx - 1, int(self._ew_pos * (nx - 1))))
        lat_col  = lats[:, ix]            # (SN,)
        lon_val  = lons[0, ix] if self.lons is not None else float(ix)
        data_ns  = self.var_all[t, :, :, ix]  # (nl, SN)

        step_y = max(1, ny // 60)
        lat_s   = lat_col[::step_y]
        data_nss = data_ns[:, ::step_y]

        LAT_ns, LEV_ns = np.meshgrid(lat_s, lev_z)
        LON_ns = np.full_like(LAT_ns, lon_val)
        col_ns  = cmap(norm(np.nan_to_num(data_nss, nan=vmin)))
        ax.plot_surface(LON_ns, LAT_ns, LEV_ns,
                        facecolors=col_ns, shade=False,
                        alpha=self._alpha, linewidth=0, antialiased=False)

        # ── Optional terrain base ─────────────────────────────────────────────
        if self._show_terrain and self.terrain is not None:
            terr = self.terrain[:ny, :nx]
            terr_n  = (terr - terr.min()) / max(terr.max() - terr.min(), 1)
            terr_s, lons_s, lats_s = self._sub(terr_n * self._vert_exag * 0.15,
                                                lons, lats)
            terrain_cmap = cm.get_cmap('terrain')
            t_col = terrain_cmap(terr_s / max(terr_s.max(), 1e-6))
            ax.plot_surface(lons_s, lats_s, terr_s,
                            facecolors=t_col, shade=True,
                            alpha=0.35, linewidth=0, antialiased=False)

        # ── Intersection lines ────────────────────────────────────────────────
        ax.plot([lon_row[0],  lon_row[-1]],  [lat_val, lat_val],
                [0, 0], '--', color='#ffff00', lw=0.8, alpha=0.7)
        ax.plot([lon_val,  lon_val],  [lat_col[0], lat_col[-1]],
                [0, 0], '--', color='#00ffff', lw=0.8, alpha=0.7)

        ax.set_xlabel('Longitude' if self.lons is not None else 'X')
        ax.set_ylabel('Latitude'  if self.lats is not None else 'Y')
        ax.set_zlabel('Eta level (0=surface → top)')

        if self._show_wind and self._has_wind:
            # quiver at surface level
            sl = self._get_2d_slice()
            ny2, nx2 = sl.shape
            lo_s, la_s = self._sub(lons, lats, n=15)
            z_s = np.zeros_like(lo_s)
            self._add_wind_quiver(ax, z_s, lo_s, la_s)

        self._add_colorbar(ax, cmap, norm, vmin, vmax)

    # ── Contour 2D mode ───────────────────────────────────────────────────────

    def _plot_contour2d(self) -> None:
        data = self._get_2d_slice()
        ny, nx = data.shape
        lons, lats = self._coords(ny, nx)

        # Use full resolution (matplotlib handles 2D contours efficiently)
        cmap = self._cmap_obj()
        norm, vmin, vmax = self._norm(data)
        data_clean = np.nan_to_num(data, nan=vmin)

        ax = self._make_ax2d()

        # Filled contour
        cf = ax.contourf(lons, lats, data_clean, levels=20,
                         cmap=cmap, norm=norm, alpha=self._alpha,
                         extend='both')
        # Contour lines
        cs = ax.contour(lons, lats, data_clean, levels=10,
                        colors='white', linewidths=0.4, alpha=0.6)
        ax.clabel(cs, inline=True, fontsize=6, fmt='%.1f',
                  colors='white', use_clabeltext=True)

        # Wind vectors on 2D
        if self._show_wind and self._has_wind:
            step = max(1, min(ny, nx) // 15)
            lo_s, la_s = lons[::step, ::step], lats[::step, ::step]
            u_s, v_s = self._get_wind_slice(ny, nx, step)
            if u_s is not None:
                spd = np.hypot(u_s, v_s)
                ax.quiver(lo_s, la_s, u_s, v_s,
                          spd, cmap='Greys_r', alpha=0.85,
                          scale=None, width=0.003)

        ax.set_xlabel('Longitude' if self.lons is not None else 'X')
        ax.set_ylabel('Latitude'  if self.lats is not None else 'Y')

        cb = self.fig.colorbar(cf, ax=ax, pad=0.02, fraction=0.04)
        cb.set_label(f'{self.var_name} [{self.var_units}]', color='#ccc', fontsize=8)
        for lbl in cb.ax.get_yticklabels():
            lbl.set_color('#aaa')

    # ── Wind helpers ──────────────────────────────────────────────────────────

    def _get_wind_slice(self, ny, nx, step=1):
        """Return (u_s, v_s) subsampled wind slices or (None, None)."""
        try:
            if self._wind_3d:
                u = self._wind_u[self.time_idx, self.level_idx]
                v = self._wind_v[self.time_idx, self.level_idx]
            else:
                u = self._wind_u[self.time_idx]
                v = self._wind_v[self.time_idx]
            # Destagger WRF U (WE_stag) and V (SN_stag)
            if u.ndim == 2:
                if u.shape[1] != nx:
                    u = 0.5 * (u[:, :-1] + u[:, 1:])
                if v.shape[0] != ny:
                    v = 0.5 * (v[:-1, :] + v[1:, :])
            u = u[:ny:step, :nx:step]
            v = v[:ny:step, :nx:step]
            return u, v
        except Exception:
            return None, None

    def _add_wind_quiver(self, ax, z_surf, lons_s, lats_s) -> None:
        ny_s, nx_s = lons_s.shape
        u_s, v_s = self._get_wind_slice(
            self.var_all.shape[-2], self.var_all.shape[-1],
            step=max(1, min(self.var_all.shape[-2],
                            self.var_all.shape[-1]) // 15)
        )
        if u_s is None:
            return
        # Clip to lons_s shape
        u_s = u_s[:ny_s, :nx_s]
        v_s = v_s[:ny_s, :nx_s]
        w_s = np.zeros_like(u_s)
        ax.quiver(lons_s, lats_s, z_surf,
                  u_s, v_s, w_s,
                  length=0.4, normalize=False,
                  color='white', alpha=0.85,
                  linewidth=0.6, arrow_length_ratio=0.35)

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_mode(self, mode: str) -> None:
        self._mode = mode
        self._update_plot()

    def _on_terrain_toggle(self, checked: bool) -> None:
        self._show_terrain = checked
        self._update_plot()

    def _on_wind_toggle(self, checked: bool) -> None:
        self._show_wind = checked
        self._update_plot()

    def _on_ns(self, val: int) -> None:
        self._ns_pos = val / 100.0
        self._ns_label.setText(f'{val} %')
        if self._mode == MODE_CUTS:
            self._update_plot()

    def _on_ew(self, val: int) -> None:
        self._ew_pos = val / 100.0
        self._ew_label.setText(f'{val} %')
        if self._mode == MODE_CUTS:
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
        if self._mode in (MODE_SURFACE, MODE_CUTS, MODE_CONTOUR):
            self._update_plot()

    def _on_cmap(self, _name: str) -> None:
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
            self, 'Save plot', f'{self.var_name}_3d.png', 'PNG (*.png)'
        )
        if path:
            self.fig.savefig(path, dpi=200, bbox_inches='tight',
                             facecolor=self.fig.get_facecolor())
            QMessageBox.information(self, 'Saved', f'Image saved:\n{path}')
