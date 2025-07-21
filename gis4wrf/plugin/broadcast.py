# GIS4WRF (https://doi.org/10.5281/zenodo.1288569)
# Copyright (c) 2018 D. Meyer and M. Riechert. Licensed under MIT.

from PyQt5.QtCore import QObject, pyqtSignal

class BroadcastSignals(QObject):
    geo_datasets_updated = pyqtSignal()
    met_datasets_updated = pyqtSignal()
    options_updated = pyqtSignal()
    project_updated = pyqtSignal()
    open_project_from_object = pyqtSignal(object)  # Corrigido para evitar import circular

Broadcast = BroadcastSignals()

class MetToolsDownloadManager:
    def __init__(self, iface):
        self.options = get_options()
        self.rda_token_input, gbox = self.create_rda_auth_input()
        self.rda_token_input.setText(self.options.rda_token or 'f477fca663fb4aa112d7c7feaa3f')

    def create_rda_auth_input(self):
        from PyQt5.QtWidgets import QLineEdit, QGroupBox, QVBoxLayout, QLabel
        gbox = QGroupBox('Autenticação RDA')
        layout = QVBoxLayout()
        gbox.setLayout(layout)

        token_label = QLabel('Token RDA:')
        token_input = QLineEdit(self.options.rda_token or '')
        layout.addWidget(token_label)
        layout.addWidget(token_input)

        return token_input, gbox
