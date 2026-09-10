# GIS4WRF
# Export a self-contained run bundle (.tar.xz) that can be moved to an HPC server.
#
# The bundle carries the namelists, tables, geogrid output and forcing data, plus
# ready-to-edit driver scripts. It deliberately does NOT carry the Windows WPS/WRF
# binaries: the server is expected to have its own build.

from typing import List, Tuple
import os
import glob
import tarfile
import textwrap

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QCheckBox, QComboBox,
    QPushButton, QFileDialog, QMessageBox, QProgressDialog, QApplication
)

# (label, xz preset, tar mode suffix)
COMPRESSION_CHOICES = [
    ('xz, fast (recommended for large bundles)', 1, 'w:xz'),
    ('xz, maximum (slowest, smallest)', 9, 'w:xz'),
    ('none, plain .tar (fastest)', None, 'w'),
]

# Files in the project root that are always part of the bundle.
CONFIG_FILENAMES = ['project.json', 'namelist.wps', 'namelist.input',
                    'GEOGRID.TBL', 'iofields.txt']


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024 or unit == 'TB':
            return '{:.1f} {}'.format(size, unit)
        size /= 1024
    return '{:.1f} TB'.format(size)


def total_size(paths: List[str]) -> int:
    total = 0
    for path in paths:
        try:
            total += os.path.getsize(path)
        except OSError:
            pass
    return total


class ExportBundleDialog(QDialog):
    ''' Lets the user pick what goes into the run bundle and writes the archive. '''

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle('Export Run Bundle')
        self.resize(680, 520)

        self.groups = self._collect_groups()
        self._build_ui()
        self._update_estimate()

    # ------------------------------------------------------------------ content

    def _project_root(self) -> str:
        return self.project.path

    def _config_files(self) -> List[str]:
        root = self._project_root()
        files = [os.path.join(root, name) for name in CONFIG_FILENAMES]
        files = [path for path in files if os.path.exists(path)]
        vtable = os.path.join(self.project.run_wps_folder, 'Vtable')
        if os.path.exists(vtable):
            files.append(vtable)
        return files

    def _geo_em_files(self) -> List[str]:
        return sorted(glob.glob(os.path.join(self.project.run_wps_folder, 'geo_em.d0*.nc')))

    def _met_grib_files(self) -> List[str]:
        ''' The raw forcing files, resolved through the project spec when possible. '''
        try:
            paths = self.project.met_dataset_spec['paths'] or []
        except Exception:
            paths = []
        if not paths:
            # Fall back to whatever the last WPS run linked into run_wps.
            paths = [os.path.realpath(path)
                     for path in glob.glob(os.path.join(self.project.run_wps_folder, 'GRIBFILE.*'))]
        return sorted({path for path in paths if os.path.exists(path)})

    def _met_em_files(self) -> List[str]:
        return sorted(glob.glob(os.path.join(self.project.run_wps_folder, 'met_em.d0*.nc')))

    def _ungrib_files(self) -> List[str]:
        return sorted(glob.glob(os.path.join(self.project.run_wps_folder, 'FILE_*')))

    def _gis_layer_files(self) -> List[str]:
        ''' Everything else sitting in the project root (rasters, shapefiles, ...). '''
        root = self._project_root()
        skip = set(CONFIG_FILENAMES)
        files = []
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if not os.path.isfile(path) or name in skip:
                continue
            if name.endswith(('.tar', '.tar.xz', '.zip')):
                continue
            files.append(path)
        return files

    def _collect_groups(self) -> List[dict]:
        return [
            dict(key='config', label='Namelists, GEOGRID.TBL, iofields.txt, Vtable, project.json',
                 files=self._config_files(), dest='config', mandatory=True, default=True),
            dict(key='geo_em', label='Geogrid output (skips geogrid and the geog dataset on the server)',
                 files=self._geo_em_files(), dest='geo_em', mandatory=True, default=True),
            dict(key='met', label='Forcing files (GRIB) — needed to run ungrib on the server',
                 files=self._met_grib_files(), dest='met', mandatory=False, default=True),
            dict(key='met_em', label='Metgrid output — include it to skip ungrib and metgrid entirely',
                 files=self._met_em_files(), dest='met_em', mandatory=False, default=False),
            dict(key='ungrib', label='Ungrib intermediate files (FILE_*) — rarely needed',
                 files=self._ungrib_files(), dest='ungrib', mandatory=False, default=False),
            dict(key='gis', label='GIS layers from the project folder (not used by WRF)',
                 files=self._gis_layer_files(), dest='gis', mandatory=False, default=False),
        ]

    # ----------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            '<b>Builds a self-contained bundle to run this simulation on another machine.</b><br>'
            'WPS/WRF binaries are not included — the server needs its own build (4.2 or newer, dmpar).'))

        group_box = QGroupBox('Contents')
        vbox = QVBoxLayout(group_box)
        self.checkboxes = {}
        for group in self.groups:
            count = len(group['files'])
            size = human_size(total_size(group['files']))
            checkbox = QCheckBox('{}  —  {} file(s), {}'.format(group['label'], count, size))
            checkbox.setChecked(group['default'] and count > 0)
            if group['mandatory'] or count == 0:
                checkbox.setEnabled(False)
                checkbox.setChecked(count > 0)
            checkbox.stateChanged.connect(self._update_estimate)
            vbox.addWidget(checkbox)
            self.checkboxes[group['key']] = checkbox
        layout.addWidget(group_box)

        hbox = QHBoxLayout()
        hbox.addWidget(QLabel('Compression:'))
        self.cmb_compression = QComboBox()
        self.cmb_compression.addItems([label for label, _, _ in COMPRESSION_CHOICES])
        hbox.addWidget(self.cmb_compression)
        hbox.addStretch()
        layout.addLayout(hbox)

        self.lbl_estimate = QLabel()
        layout.addWidget(self.lbl_estimate)

        layout.addWidget(QLabel(
            '<i>The bundle also carries run_wps.sh, run_wrf.sh and README_SERVIDOR.md '
            'with step-by-step instructions.</i>'))
        layout.addStretch()

        buttons = QHBoxLayout()
        buttons.addStretch()
        btn_cancel = QPushButton('Cancel')
        btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(btn_cancel)
        self.btn_export = QPushButton('Export...')
        self.btn_export.setDefault(True)
        self.btn_export.clicked.connect(self._on_export)
        buttons.addWidget(self.btn_export)
        layout.addLayout(buttons)

    def _selected_groups(self) -> List[dict]:
        return [group for group in self.groups
                if self.checkboxes[group['key']].isChecked() and group['files']]

    def _update_estimate(self) -> None:
        selected = self._selected_groups()
        count = sum(len(group['files']) for group in selected)
        raw = total_size([path for group in selected for path in group['files']])
        self.lbl_estimate.setText(
            '<b>Selected: {} file(s), {} before compression.</b>'.format(count, human_size(raw)))

    # -------------------------------------------------------------------- write

    def _on_export(self) -> None:
        selected = self._selected_groups()
        if not selected:
            QMessageBox.warning(self, 'Nothing to export', 'Select at least one group of files.')
            return

        _, preset, mode = COMPRESSION_CHOICES[self.cmb_compression.currentIndex()]
        suffix = '.tar.xz' if preset is not None else '.tar'
        name = os.path.basename(os.path.normpath(self.project.path)) or 'project'
        default_path = os.path.join(self.project.path, name + '_bundle' + suffix)

        path, _ = QFileDialog.getSaveFileName(
            self, 'Export Run Bundle', default_path,
            'Archives (*{})'.format(suffix))
        if not path:
            return

        members = self.build_member_list(selected, name)
        progress = QProgressDialog('Writing bundle...', 'Cancel', 0, len(members) + 1, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        def report(index, file_path):
            if progress.wasCanceled():
                return False
            progress.setValue(index)
            progress.setLabelText('Writing {}\n({} of {})'.format(
                os.path.basename(file_path), index + 1, len(members)))
            QApplication.processEvents()
            return True

        try:
            self.write_bundle(path, selected, name, preset, mode, report)
        except KeyboardInterrupt:
            # raised by write_bundle when the progress dialog was cancelled
            progress.close()
            self.reject()
            return
        except Exception as exc:
            progress.close()
            QMessageBox.critical(self, 'Export Error', 'Failed to create the bundle:\n{}'.format(exc))
            return
        progress.setValue(len(members) + 1)
        progress.close()

        QMessageBox.information(
            self, 'Export complete',
            'Bundle written to:\n{}\n\nSize: {}'.format(path, human_size(os.path.getsize(path))))
        self.accept()

    @staticmethod
    def build_member_list(selected: List[dict], name: str) -> List[Tuple[str, str]]:
        members = []  # type: List[Tuple[str,str]]
        for group in selected:
            for file_path in group['files']:
                members.append((file_path, '{}/{}/{}'.format(name, group['dest'],
                                                             os.path.basename(file_path))))
        return members

    def write_bundle(self, path: str, selected: List[dict], name: str,
                     preset, mode: str, report=None) -> None:
        ''' Writes the archive. `report(index, file_path)` may return False to abort. '''
        members = self.build_member_list(selected, name)
        kwargs = {'preset': preset} if preset is not None else {}
        try:
            with tarfile.open(path, mode, **kwargs) as tar:
                for index, (file_path, arcname) in enumerate(members):
                    if report is not None and not report(index, file_path):
                        raise KeyboardInterrupt
                    tar.add(file_path, arcname=arcname)
                for filename, text in self._generate_helper_files(selected).items():
                    self._add_text_member(tar, '{}/{}'.format(name, filename), text)
        except KeyboardInterrupt:
            if os.path.exists(path):
                os.remove(path)
            raise

    @staticmethod
    def _add_text_member(tar: tarfile.TarFile, arcname: str, text: str) -> None:
        import io
        data = text.encode('utf-8')
        info = tarfile.TarInfo(arcname)
        info.size = len(data)
        info.mode = 0o755 if arcname.endswith('.sh') else 0o644
        tar.addfile(info, io.BytesIO(data))

    # ------------------------------------------------------------------ scripts

    def _generate_helper_files(self, selected: List[dict]) -> dict:
        keys = {group['key'] for group in selected}
        has_met_em = 'met_em' in keys
        max_dom, e_we, e_sn = self._read_grid_shape()

        return {
            'run_wps.sh': self._script_wps(),
            'run_wrf.sh': self._script_wrf(has_met_em),
            'README_SERVIDOR.md': self._readme(keys, max_dom, e_we, e_sn),
        }

    def _read_grid_shape(self):
        ''' Reads max_dom/e_we/e_sn from the WRF namelist so the README can size the run. '''
        try:
            import f90nml
            domains = f90nml.read(self.project.wrf_namelist_path)['domains']
            max_dom = domains['max_dom']
            e_we = domains['e_we']
            e_sn = domains['e_sn']
            if not isinstance(e_we, list):
                e_we = [e_we]
            if not isinstance(e_sn, list):
                e_sn = [e_sn]
            return max_dom, [v for v in e_we if v], [v for v in e_sn if v]
        except Exception:
            return None, [], []

    @staticmethod
    def _script_wps() -> str:
        return textwrap.dedent('''\
            #!/usr/bin/env bash
            # Runs ungrib and metgrid. Geogrid is not needed: geo_em files ship with the bundle.
            set -euo pipefail

            HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
            : "${WPS_DIR:?set WPS_DIR=/path/to/WPS}"
            NP="${NP:-8}"

            RUN="$HERE/run_wps"
            mkdir -p "$RUN/geogrid" "$RUN/metgrid"

            cp -f "$HERE/config/namelist.wps" "$RUN/namelist.wps"
            cp -f "$HERE/config/Vtable"       "$RUN/Vtable"
            cp -f "$HERE/config/GEOGRID.TBL"  "$RUN/geogrid/GEOGRID.TBL"
            cp -f "$WPS_DIR/metgrid/METGRID.TBL.ARW" "$RUN/metgrid/METGRID.TBL"
            ln -sf "$HERE"/geo_em/geo_em.d0*.nc "$RUN"/

            # link_grib without csh: GRIBFILE.AAA, AAB, ...
            cd "$RUN"
            rm -f GRIBFILE.*
            letters=({A..Z})
            i=0
            for f in "$HERE"/met/*; do
              a=$(( i / 676 )); b=$(( (i / 26) % 26 )); c=$(( i % 26 ))
              ln -sf "$f" "GRIBFILE.${letters[$a]}${letters[$b]}${letters[$c]}"
              i=$(( i + 1 ))
            done

            echo "== ungrib (serial) =="
            "$WPS_DIR/ungrib.exe"

            echo "== metgrid (np=$NP) =="
            mpirun -np "$NP" "$WPS_DIR/metgrid.exe"

            echo "met_em files written to $RUN"
            ''')

    @staticmethod
    def _script_wrf(has_met_em: bool) -> str:
        met_em_source = ('"$HERE"/met_em/met_em.d0*.nc' if has_met_em
                         else '"$HERE"/run_wps/met_em.d0*.nc')
        return textwrap.dedent('''\
            #!/usr/bin/env bash
            # Runs real.exe and then wrf.exe.
            set -euo pipefail

            HERE="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
            : "${{WRF_DIR:?set WRF_DIR=/path/to/WRF}}"
            NP="${{NP:-64}}"

            RUN="$HERE/run_wrf"
            mkdir -p "$RUN"

            # Static data and tables from the WRF build (run/ on a source build,
            # test/em_real/ on some distributions).
            STATIC="$WRF_DIR/run"
            [ -d "$STATIC" ] || STATIC="$WRF_DIR/test/em_real"
            for f in "$STATIC"/*; do
              case "$(basename "$f")" in
                namelist.input*|*.exe|README*) continue ;;
              esac
              ln -sf "$f" "$RUN"/
            done

            cp -f "$HERE/config/namelist.input" "$RUN/namelist.input"
            if [ -f "$HERE/config/iofields.txt" ]; then
              cp -f "$HERE/config/iofields.txt" "$RUN"/
            fi

            # An unmatched glob would create dangling links, so check first.
            met_em=( {met_em_source} )
            if [ ! -e "${{met_em[0]}}" ]; then
              echo "met_em files not found. Run run_wps.sh first." >&2
              exit 1
            fi
            ln -sf "${{met_em[@]}}" "$RUN"/

            cd "$RUN"
            echo "== real.exe (np=$NP) =="
            mpirun -np "$NP" "$WRF_DIR/main/real.exe"
            grep -q "SUCCESS COMPLETE REAL_EM INIT" rsl.error.0000 rsl.out.0000 2>/dev/null \\
              || {{ echo "real.exe failed, check rsl.error.*" >&2; exit 1; }}

            echo "== wrf.exe (np=$NP) =="
            mpirun -np "$NP" "$WRF_DIR/main/wrf.exe"
            ''').format(met_em_source=met_em_source)

    def _readme(self, keys, max_dom, e_we, e_sn) -> str:
        smallest = min(list(e_we) + list(e_sn)) if e_we and e_sn else None
        if smallest:
            max_ranks_per_dim = max(1, smallest // 10)
            max_ranks = max_ranks_per_dim * max_ranks_per_dim
            sizing = textwrap.dedent('''\
                O domínio mais restritivo tem {smallest} pontos no menor lado. O WRF precisa de
                aproximadamente 10 pontos por patch em cada direção (a halo tem 5), então o teto
                prático é em torno de **{max_ranks_per_dim} x {max_ranks_per_dim} = {max_ranks} processos** —
                acima disso o real.exe aborta com erro de domínio pequeno demais, e bem antes disso
                a comunicação de halo passa a dominar o tempo.

                Numa máquina de 200 CPUs, comece com `NP=64`, meça, e só então tente `NP=100`.
                Se a escalabilidade travar, o ganho vem de aumentar os domínios ou de rodar vários
                membros/cenários em paralelo, não de empilhar mais ranks no mesmo domínio.
                ''').format(smallest=smallest, max_ranks_per_dim=max_ranks_per_dim, max_ranks=max_ranks)
        else:
            sizing = ('Verifique o tamanho dos domínios antes de escolher NP: o WRF exige cerca de\n'
                      '10 pontos de grade por processo em cada direção.\n')

        if 'met_em' in keys:
            wps_step = ('Os `met_em` já vêm no pacote, então **pule o run_wps.sh** e vá direto para\n'
                        '`run_wrf.sh`. Só rode o WPS de novo se quiser mudar a janela temporal.')
        else:
            wps_step = ('Rode `run_wps.sh` primeiro (ungrib + metgrid). O geogrid **não** é necessário:\n'
                        'os `geo_em` dos domínios já vêm no pacote, e por isso o dataset geográfico\n'
                        '(dezenas de GB) não precisa existir no servidor.')

        return textwrap.dedent('''\
            # Pacote de execução WRF — {name}

            Gerado pelo GIS4WRF. Contém configuração, dados de contorno e os `geo_em` já prontos.
            **Não contém binários**: o servidor precisa da sua própria compilação de WPS/WRF 4.2+
            com `dmpar`.

            ## Conteúdo

            ```
            config/    namelists, GEOGRID.TBL, iofields.txt, Vtable, project.json
            geo_em/    saída do geogrid ({max_dom} domínio(s))
            met/       forçantes brutas (GRIB)
            met_em/    saída do metgrid (se incluída)
            gis/       camadas GIS do projeto (se incluídas, não usadas pelo WRF)
            run_wps.sh, run_wrf.sh
            ```

            ## Como rodar

            ```bash
            tar xJf {name}_bundle.tar.xz
            cd {name}
            export WPS_DIR=/caminho/para/WPS
            export WRF_DIR=/caminho/para/WRF
            export NP=64
            ./run_wps.sh    # ungrib + metgrid
            ./run_wrf.sh    # real + wrf
            ```

            {wps_step}

            ## Quantos processos usar

            {sizing}

            ## Antes de disparar, confira

            - `config/namelist.input` — a janela temporal (`start_*`/`end_*`) e o `history_interval`.
            - Se você compilou o WRF com opções diferentes, revise `&physics`: este namelist foi
              montado para um build 4.2 padrão.
            - O `nocolons = .true.` no namelist faz os nomes de arquivo usarem `_` no lugar de `:`.
              Mantenha coerente entre WPS e WRF.
            ''').format(name=os.path.basename(os.path.normpath(self.project.path)) or 'project',
                        max_dom=max_dom if max_dom else '?',
                        wps_step=wps_step, sizing=sizing)
