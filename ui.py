# coding=utf-8
from PyQt4 import QtCore, QtGui
from aqt import mw
from aqt.qt import *
from aqt.utils import showWarning, tooltip

from exporter import TOMLNoteExporter
import pytoml as toml

class ExportDialog(QDialog):

    def __init__(self, mw):
        QDialog.__init__(self, mw, Qt.Window)
        self.mw = mw
        self.setup_ui()
        self.fill_values()

    def open_profile(self):
        path_name = self.getProfilePathName()
        if path_name:
            self.profile_edit.setText(path_name)

    def open_output(self):
        path_name = self.getOutputPathName()
        if path_name:
            self.output_edit.setText(path_name)

    def on_accept(self):
        ok = self.readValues()
        if ok:
            exporter = TOMLNoteExporter(mw.col, query=mw.ankisport.query, sets=mw.ankisport.sets)
            ok = exporter.doExport(mw.ankisport.output_path, verify=mw.ankisport.verify)
            if ok:
                tooltip("Exported %d notes" % exporter.count, parent=self.mw)
        if ok:
            QDialog.accept(self)

    def on_reject(self):
        self.close()

    def getProfilePathName(self):
        filter = 'TOML Files (*.toml)'
        return unicode(QFileDialog.getOpenFileName(mw, "Exporter Profile",
                                                   mw.ankisport.profile_path, filter))

    def getOutputPathName(self):
        filter = 'TOML Files (*.toml)'
        return unicode(QFileDialog.getSaveFileName(mw, "Export to file",
                                                   mw.ankisport.output_path, filter))

    def fill_values(self):
        self.profile_edit.setText(mw.ankisport.profile_path)
        self.output_edit.setText(mw.ankisport.output_path)
        self.verify_btn.setChecked(mw.ankisport.verify)

    def readValues(self):
        mw.ankisport.profile_path = self.profile_edit.text()
        if mw.ankisport.profile_path == "":
            showWarning("The export profile is not set")
            return False
        mw.ankisport.output_path = self.output_edit.text()
        if mw.ankisport.output_path == "":
            showWarning("The export path is not set")
            return False

        mw.ankisport.verify = self.verify_btn.isChecked()

        with open(mw.ankisport.profile_path, 'r') as f:
            t = toml.load(f)
        mw.ankisport.query = t['query']
        mw.ankisport.sets = t.get('sets', [])
        return True

    def setup_ui(self):
        self.setWindowModality(QtCore.Qt.ApplicationModal)
        self.resize(718, 358)
        self.setSizeGripEnabled(True)
        self.setModal(True)

        l_main = QtGui.QVBoxLayout(self)
        grid = QtGui.QGridLayout()

        profile_label = QLabel('Profile')
        grid.addWidget(profile_label, 0, 0, 1, 1)
        self.profile_edit = QLineEdit()
        grid.addWidget(self.profile_edit, 0, 1, 1, 3)
        profile_btn = QPushButton("Open &Profile", clicked=self.open_profile)
        grid.addWidget(profile_btn, 0, 4, 1, 1)

        output_label = QLabel('Output')
        grid.addWidget(output_label, 1, 0, 1, 1)
        self.output_edit = QLineEdit()
        grid.addWidget(self.output_edit, 1, 1, 1, 3)
        output_btn = QPushButton("Open &Output", clicked=self.open_output)
        grid.addWidget(output_btn, 1, 4, 1, 1)

        self.verify_btn = QCheckBox('&Verify')
        grid.addWidget(self.verify_btn, 2, 0, 1, 2)

        l_main.addLayout(grid)
        button_box = QDialogButtonBox(self)
        button_box.setOrientation(QtCore.Qt.Horizontal)
        button_box.setStandardButtons(QtGui.QDialogButtonBox.Cancel|QtGui.QDialogButtonBox.Ok)
        button_box.accepted.connect(self.on_accept)
        button_box.rejected.connect(self.on_reject)
        l_main.addWidget(button_box)
        self.setLayout(l_main)

class Settings(object):
    def __init__(self):
        dir = QDesktopServices.storageLocation(QDesktopServices.DesktopLocation)
        self.profile_path = os.path.join(dir, "settings.toml")
        self.output_path = os.path.join(dir, "export.toml")
        self.query = ""
        self.sets = []
        self.verify = False

def displayDialog():
    dlg = ExportDialog(mw)
    dlg.exec_()

def load_addon():
    action = QAction('TOML Export...', mw)
    action.triggered.connect(displayDialog)
    mw.form.menuTools.addAction(action)
    mw.ankisport = Settings()
