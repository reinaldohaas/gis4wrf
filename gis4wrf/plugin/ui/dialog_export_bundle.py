# GIS4WRF
# Export a self-contained run bundle (.tar.xz) that can be moved to an HPC server.
#
# The archive mirrors the GIS4WRF layout, so the extracted tree is a working
# GIS4WRF installation directory: the project keeps its own folder with its
# run_wps/run_wrf subfolders, and the meteorological data keeps the relative
# path recorded in project.json. That way the project can be reopened in
# GIS4WRF on the other machine and the driver scripts find everything where
# the plugin itself would put it.
#
# WPS/WRF binaries are deliberately not included: the server is expected to
# have its own build.

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

# (label, xz preset, tar mode)
COMPRESSION_CHOICES = [
    ('xz, fast (recommended)', 1, 'w:xz'),
    ('xz, maximum (slowest, smallest)', 9, 'w:xz'),
    ('none, plain .tar (fastest — best when met_em is included)', None, 'w'),
]

# Files in the project root that are always part of the bundle.
CONFIG_FILENAMES = ['project.json', 'namelist.wps', 'namelist.input',
                    'GEOGRID.TBL', 'iofields.txt']

# Top-level folder inside the archive, mirroring the GIS4WRF working directory.
ROOT_DIRNAME = 'gis4wrf'


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
        self.project_name = os.path.basename(os.path.normpath(project.path)) or 'project'
        self.setWindowTitle('Export Run Bundle')
        self.resize(720, 560)

        self.groups = self._collect_groups()
        self._build_ui()
        self._update_estimate()

    # ------------------------------------------------------------------ content
    #
    # Every group maps each source file to its path inside the archive, relative
    # to ROOT_DIRNAME. `projects/<name>/...` and `datasets/met/...` reproduce the
    # layout GIS4WRF uses on disk.

    def _project_rel(self, *parts) -> str:
        return '/'.join(['projects', self.project_name] + list(parts))

    def _config_files(self) -> List[Tuple[str, str]]:
        root = self.project.path
        members = []
        for name in CONFIG_FILENAMES:
            path = os.path.join(root, name)
            if os.path.exists(path):
                members.append((path, self._project_rel(name)))
        # The Vtable lives in run_wps, where ungrib expects it.
        vtable = os.path.join(self.project.run_wps_folder, 'Vtable')
        if os.path.exists(vtable):
            members.append((vtable, self._project_rel('run_wps', 'Vtable')))
        return members

    def _run_wps_files(self, pattern: str) -> List[Tuple[str, str]]:
        return [(path, self._project_rel('run_wps', os.path.basename(path)))
                for path in sorted(glob.glob(os.path.join(self.project.run_wps_folder, pattern)))]

    def _met_files(self) -> List[Tuple[str, str]]:
        ''' Raw forcing, kept under the relative path recorded in project.json. '''
        members = []
        try:
            spec = self.project.data['met_dataset_spec']
            rel_paths = spec['rel_paths']
            abs_paths = self.project.met_dataset_spec['paths'] or []
        except Exception:
            rel_paths, abs_paths = [], []

        if abs_paths and len(abs_paths) == len(rel_paths):
            for abs_path, rel_path in zip(abs_paths, rel_paths):
                if os.path.exists(abs_path):
                    arc = 'datasets/met/' + rel_path.replace('\\', '/')
                    members.append((abs_path, arc))
        else:
            # Fall back to whatever the last WPS run linked into run_wps.
            for link in sorted(glob.glob(os.path.join(self.project.run_wps_folder, 'GRIBFILE.*'))):
                target = os.path.realpath(link)
                if os.path.exists(target):
                    members.append((target, 'datasets/met/' + os.path.basename(target)))
        return members

    def _gis_layer_files(self) -> List[Tuple[str, str]]:
        ''' Everything else sitting in the project root (rasters, shapefiles, ...). '''
        root = self.project.path
        skip = set(CONFIG_FILENAMES)
        members = []
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if not os.path.isfile(path) or name in skip:
                continue
            if name.endswith(('.tar', '.tar.xz', '.zip')):
                continue
            members.append((path, self._project_rel(name)))
        return members

    def _collect_groups(self) -> List[dict]:
        return [
            dict(key='config', default=True, mandatory=True,
                 label='Project configuration — namelists, GEOGRID.TBL, iofields.txt, Vtable, project.json',
                 members=self._config_files()),
            dict(key='geo_em', default=True, mandatory=True,
                 label='run_wps/geo_em — skips geogrid and the geog dataset on the server',
                 members=self._run_wps_files('geo_em.d0*.nc')),
            dict(key='met', default=True, mandatory=True,
                 label='datasets/met — raw forcing (GRIB), keeps project.json rel_paths valid',
                 members=self._met_files()),
            dict(key='met_em', default=False, mandatory=False,
                 label='run_wps/met_em — include it to skip ungrib and metgrid entirely',
                 members=self._run_wps_files('met_em.d0*.nc')),
            dict(key='ungrib', default=False, mandatory=False,
                 label='run_wps/FILE_* — ungrib intermediates, rarely needed',
                 members=self._run_wps_files('FILE_*')),
            dict(key='gis', default=False, mandatory=False,
                 label='GIS layers from the project folder (not used by WRF)',
                 members=self._gis_layer_files()),
        ]

    # ----------------------------------------------------------------------- ui

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            '<b>Builds a self-contained bundle to run this simulation on another machine.</b><br>'
            'The archive mirrors the GIS4WRF layout ('
            '<code>{root}/projects/{name}/</code> and <code>{root}/datasets/met/</code>), so the project '
            'can be reopened in GIS4WRF there.<br>'
            'WPS/WRF binaries are not included — the server needs its own build (4.2 or newer, dmpar).'
            .format(root=ROOT_DIRNAME, name=self.project_name)))

        group_box = QGroupBox('Contents')
        vbox = QVBoxLayout(group_box)
        self.checkboxes = {}
        for group in self.groups:
            count = len(group['members'])
            size = human_size(total_size([src for src, _ in group['members']]))
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
            '<i>run_wps.sh, run_wrf.sh and README_SERVIDOR.md are generated into the archive root.</i>'))
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
                if self.checkboxes[group['key']].isChecked() and group['members']]

    def _update_estimate(self) -> None:
        selected = self._selected_groups()
        count = sum(len(group['members']) for group in selected)
        raw = total_size([src for group in selected for src, _ in group['members']])
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
        default_path = os.path.join(self.project.path, self.project_name + '_bundle' + suffix)

        path, _ = QFileDialog.getSaveFileName(
            self, 'Export Run Bundle', default_path, 'Archives (*{})'.format(suffix))
        if not path:
            return

        members = self.build_member_list(selected)
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
            self.write_bundle(path, selected, preset, mode, report)
        except KeyboardInterrupt:
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
    def build_member_list(selected: List[dict]) -> List[Tuple[str, str]]:
        ''' (source path, path inside the archive) for everything selected. '''
        members = []
        for group in selected:
            for src, rel in group['members']:
                members.append((src, '{}/{}'.format(ROOT_DIRNAME, rel)))
        return members

    def write_bundle(self, path: str, selected: List[dict], preset, mode: str, report=None) -> None:
        ''' Writes the archive. `report(index, src)` may return False to abort. '''
        members = self.build_member_list(selected)
        kwargs = {'preset': preset} if preset is not None else {}
        try:
            with tarfile.open(path, mode, **kwargs) as tar:
                for index, (src, arcname) in enumerate(members):
                    if report is not None and not report(index, src):
                        raise KeyboardInterrupt
                    tar.add(src, arcname=arcname)
                for filename, text in self._generate_helper_files(selected).items():
                    self._add_text_member(tar, '{}/{}'.format(ROOT_DIRNAME, filename), text)
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
        max_dom, e_we, e_sn = self._read_grid_shape()
        return {
            'run_wps.sh': self._script_wps(),
            'run_wrf.sh': self._script_wrf(),
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

    def _script_wps(self) -> str:
        ''' Mirrors Project.prepare_wps_run, then runs ungrib and metgrid. '''
        return textwrap.dedent('''\
            #!/usr/bin/env bash
            # Prepares run_wps the same way GIS4WRF does, then runs ungrib and metgrid.
            # Geogrid is not needed: the geo_em files ship with the bundle.
            set -euo pipefail

            HERE="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
            PROJ="$HERE/projects/{name}"
            RUN="$PROJ/run_wps"
            : "${{WPS_DIR:?set WPS_DIR=/path/to/WPS}}"
            NP="${{NP:-8}}"

            mkdir -p "$RUN/geogrid" "$RUN/metgrid"
            cp -f "$PROJ/namelist.wps" "$RUN/namelist.wps"
            cp -f "$PROJ/GEOGRID.TBL"  "$RUN/geogrid/GEOGRID.TBL"
            cp -f "$WPS_DIR/metgrid/METGRID.TBL.ARW" "$RUN/metgrid/METGRID.TBL"

            # link_grib without csh: GRIBFILE.AAA, AAB, ...
            cd "$RUN"
            rm -f GRIBFILE.*
            mapfile -t gribs < <(find "$HERE/datasets/met" -type f | sort)
            if [ "${{#gribs[@]}}" -eq 0 ]; then
              echo "No forcing files under datasets/met." >&2
              exit 1
            fi
            letters=({{A..Z}})
            i=0
            for f in "${{gribs[@]}}"; do
              a=$(( i / 676 )); b=$(( (i / 26) % 26 )); c=$(( i % 26 ))
              ln -sf "$f" "GRIBFILE.${{letters[$a]}}${{letters[$b]}}${{letters[$c]}}"
              i=$(( i + 1 ))
            done

            echo "== ungrib (serial) =="
            "$WPS_DIR/ungrib.exe"

            echo "== metgrid (np=$NP) =="
            mpirun -np "$NP" "$WPS_DIR/metgrid.exe"

            echo "met_em files written to $RUN"
            ''').format(name=self.project_name)

    def _script_wrf(self) -> str:
        ''' Mirrors Project.prepare_wrf_run, then runs real.exe and wrf.exe. '''
        return textwrap.dedent('''\
            #!/usr/bin/env bash
            # Prepares run_wrf the same way GIS4WRF does, then runs real.exe and wrf.exe.
            set -euo pipefail

            HERE="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
            PROJ="$HERE/projects/{name}"
            RUN="$PROJ/run_wrf"
            : "${{WRF_DIR:?set WRF_DIR=/path/to/WRF}}"
            NP="${{NP:-64}}"

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

            cp -f "$PROJ/namelist.input" "$RUN/namelist.input"
            if [ -f "$PROJ/iofields.txt" ]; then
              cp -f "$PROJ/iofields.txt" "$RUN"/
            fi

            # An unmatched glob would create dangling links, so check first.
            met_em=( "$PROJ"/run_wps/met_em.d0*.nc )
            if [ ! -e "${{met_em[0]}}" ]; then
              echo "No met_em in $PROJ/run_wps. Run run_wps.sh first." >&2
              exit 1
            fi
            ln -sf "${{met_em[@]}}" "$RUN"/

            cd "$RUN"
            echo "== real.exe (np=$NP) =="
            mpirun -np "$NP" "$WRF_DIR/main/real.exe"
            grep -q "SUCCESS COMPLETE REAL_EM INIT" rsl.error.0000 rsl.out.0000 2>/dev/null \\
              || {{ echo "real.exe failed, check $RUN/rsl.error.*" >&2; exit 1; }}

            echo "== wrf.exe (np=$NP) =="
            mpirun -np "$NP" "$WRF_DIR/main/wrf.exe"
            echo "wrfout files in $RUN"
            ''').format(name=self.project_name)

    def _readme(self, keys, max_dom, e_we, e_sn) -> str:
        smallest = min(list(e_we) + list(e_sn)) if e_we and e_sn else None
        if smallest:
            per_dim = max(1, smallest // 10)
            sizing = textwrap.dedent('''\
                O domínio mais restritivo tem {smallest} pontos no menor lado. O WRF precisa de
                cerca de 10 pontos por patch em cada direção (a halo tem 5), então o teto prático
                fica em torno de **{per_dim} x {per_dim} = {total} processos**. Acima disso o real.exe
                aborta com erro de domínio pequeno demais, e bem antes disso a troca de halo passa
                a dominar o tempo.

                Comece com `NP=64`, meça, e só então tente `NP=100`. Se a escalabilidade travar,
                o ganho vem de aumentar os domínios ou de rodar vários cenários em paralelo —
                não de empilhar mais ranks nesta grade.
                ''').format(smallest=smallest, per_dim=per_dim, total=per_dim * per_dim)
        else:
            sizing = ('Verifique o tamanho dos domínios antes de escolher NP: o WRF exige cerca de\n'
                      '10 pontos de grade por processo em cada direção.\n')

        if 'met_em' in keys:
            wps_step = ('Os `met_em` já vêm em `run_wps/`, então **pule o `run_wps.sh`** e vá direto\n'
                        'para o `run_wrf.sh`. Só rode o WPS de novo se mudar a janela temporal.')
        else:
            wps_step = ('Rode o `run_wps.sh` primeiro (ungrib + metgrid). O geogrid **não** é\n'
                        'necessário: os `geo_em` já vêm em `run_wps/`, e por isso o dataset\n'
                        'geográfico (dezenas de GB) não precisa existir no servidor.')

        return textwrap.dedent('''\
            # Pacote de execução WRF — {name}

            Gerado pelo GIS4WRF. A árvore aqui dentro é a mesma que o GIS4WRF usa em disco,
            então dá tanto para rodar pelos scripts quanto para reabrir o projeto no plugin.
            **Não contém binários**: o servidor precisa da própria compilação de WPS/WRF 4.2+
            com `dmpar`.

            ## Estrutura

            ```
            {root}/
              projects/{name}/
                project.json, namelist.wps, namelist.input, GEOGRID.TBL, iofields.txt
                run_wps/          Vtable, geo_em.d0*.nc (e met_em, se incluídos)
                run_wrf/          criado pelo run_wrf.sh
              datasets/
                met/              forçantes no mesmo caminho relativo do project.json
              run_wps.sh, run_wrf.sh, README_SERVIDOR.md
            ```

            Os caminhos das forçantes em `project.json` são relativos ao diretório de dados
            meteorológicos, então eles continuam válidos apontando o GIS4WRF para
            `{root}/datasets/met`.

            ## Como rodar

            ```bash
            tar xJf {name}_bundle.tar.xz
            cd {root}
            export WPS_DIR=/caminho/para/WPS
            export WRF_DIR=/caminho/para/WRF
            export NP=64
            ./run_wps.sh    # ungrib + metgrid
            ./run_wrf.sh    # real + wrf
            ```

            {wps_step}

            Os dois scripts preparam `run_wps/` e `run_wrf/` exatamente como o plugin faz
            (`prepare_wps_run` e `prepare_wrf_run`): copiam namelists e tabelas, linkam os
            GRIB como `GRIBFILE.AAA...`, linkam os dados estáticos do WRF e os `met_em`.

            ## Quantos processos usar

            {sizing}

            ## Antes de disparar, confira

            - `projects/{name}/namelist.input` — janela temporal (`start_*`/`end_*`) e `history_interval`.
            - Se o WRF do servidor foi compilado com outras opções, revise `&physics`.
            - `nocolons = .true.` faz os nomes de arquivo usarem `_` no lugar de `:`.
              Mantenha coerente entre WPS e WRF.
            ''').format(name=self.project_name, root=ROOT_DIRNAME,
                        wps_step=wps_step, sizing=sizing)
