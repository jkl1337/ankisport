# coding=utf-8
import json
import subprocess
import textwrap
from collections import defaultdict
from datetime import datetime
from itertools import islice, izip

import codecs
import re
from PyQt4 import QtCore, QtGui
from anki.exporting import Exporter
from anki.lang import _
from anki.utils import splitFields, ids2str
from aqt import mw
from aqt.qt import *
from aqt.utils import showWarning, tooltip

import pytoml as toml

class TOMLGenerator(object):
    g_newline = '\n'

    DATETIME_ISO8601_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
    escape_re = re.compile(r'([\x00-\x1f"\\])')
    escape_re_sub_tab = {'\t': 't', '\n': 'n', '\"': '"', '\r': 'r', '\\': '\\', '\f': 'f', '\b': 'b', '"""': r'"""'}
    ml_escape_re = re.compile(r'([\x00-\x09\x0b-\x1f\\]|""")')
    ws_match_re = re.compile(r'^[\t ]+')

    def __init__(self, output):
        self.output = output
        self.text_wrapper = textwrap.TextWrapper(width=120, expand_tabs=False, replace_whitespace=False, drop_whitespace=False)

    @classmethod
    def escape_string(cls, s):
        escape_re_sub_tab = cls.escape_re_sub_tab
        return cls.escape_re.sub(lambda c: '\\' + (escape_re_sub_tab.get(c.group(1), None)
                                                   or ('u%.4x' % ord(c.group(1)))), s)

    def g_write_escaped(self, s):
        self.output.write(self.escape_string(s))

    def g_write_ml_escaped(self, s):
        escape_re_sub_tab = self.escape_re_sub_tab
        self.output.write(
            self.ml_escape_re.sub(lambda c: '\\' + (escape_re_sub_tab.get(c.group(1), None)
                                                    or ('u%.4x' % ord(c.group(1)))), s))

    def wrap_text(self, s, offset):
        tw = self.text_wrapper
        tw.initial_indent = ' ' * offset

        lines = []
        for para in s.splitlines(True):
            sl = tw.wrap(para)
            if tw.initial_indent and sl:
                sl[0] = sl[0][offset:]
            lines.extend(sl)
            tw.initial_indent = ''
        return lines

    def g_string(self, line_offset, v):
        output = self.output
        ws_match = self.ws_match_re

        lines = self.wrap_text(v, 0)

        if len(lines) == 0:
            output.write('""\n')
            return
        elif len(lines) == 1:
            multiline = False
            singlequote = False if re.search(r"[\x00-\x1f']", lines[0]) else True
            if not singlequote:
                unescaped_style = lines[0].find("'''") == -1
        else:
            multiline = True

        if multiline:
            output.write('"""\n')
            self.g_write_ml_escaped(lines[0])
            trailing_nl = lines[0][-1] == u'\n'
            trailing_quote = False
            # TODO: Handle massive whitespace
            for line in islice(lines, 1, None):
                # fix up previous line, appending any leading whitespace on this line
                lws = ws_match.match(line)
                if lws:
                    ws = lws.group()
                    trailing_nl = ws[-1] == u'\n'
                    self.g_write_ml_escaped(ws)
                    line = line[lws.end():]
                if not trailing_nl:
                    output.write('\\\n')
                self.g_write_ml_escaped(line)
                trailing_nl = line[-1] == u'\n'
                trailing_quote = line[-1] == u'"'
            output.write('"""\n' if not trailing_quote else '\\\n"""\n')
        else:
            if singlequote:
                output.write("'%s'\n" % lines[0])
            else:
                if unescaped_style:
                    output.write("'''%s'''\n" % lines[0])
                else:
                    output.write('"')
                    self.g_write_escaped(lines[0])
                    output.write('"\n')


    def g_bool(self, line_offset, v):
        self.output.write('true' if v else 'false')
        self.output.write('\n')

    def g_integer(self, line_offset, v):
        self.output.write(str(v))
        self.output.write('\n')

    g_float = g_integer

    def g_datetime(self, line_offset, v):
        self.output.write(v.strftime(self.DATETIME_ISO8601_FORMAT))
        self.output.write('\n')

    VALUE_MAP = {unicode: g_string, bool: g_bool, int: g_integer, long: g_integer, float: g_float, datetime: g_datetime}

    def gen_value(self, line_offset, v):
        for t, c in self.VALUE_MAP.iteritems():
            if isinstance(v, t):
                return c(self, line_offset, v)

    def gen_key_value(self, k, v):
        if re.search(r"[^A-Za-z0-9_-]", k):
            k = '"' + self.escape_string(k) + '" = '
        else:
            k += ' = '
        self.output.write(k)
        self.gen_value(len(k), v)


class keydefaultdict(defaultdict):
    def __missing__(self, key):
        if self.default_factory is None:
            raise KeyError(key)
        else:
            ret = self[key] = self.default_factory(key)
            return ret


class OutputModel(object):
    def __init__(self, models, mid):
        model = models.get(mid)
        fieldNames = models.fieldNames(model)
        fieldSet = set(fieldNames)

        def transformName(name):
            n = name.lower().replace(' ', '-')
            return n if n not in fieldSet else name

        self.fieldNames = [transformName(fn) for fn in fieldNames]
        self.name = model['name']
        self.model = model


class TOMLNoteExporter(Exporter):
    key = _("Notes in TOML format")
    ext = ".toml"

    def __init__(self, col, query=None, sets=None):
        Exporter.__init__(self, col)
        self.includeMedia = True
        self.query = query
        self.sets = sets

    def exportInto(self, path):
        file = codecs.open(path, "w", encoding='utf-8')
        self.doExport(file)
        file.close()

    def doExport(self, path, verify=False):
        models = self.col.models
        outputModels = keydefaultdict(lambda mid: OutputModel(models, mid))

        count = 0

        notes = []
        query = self.query
        if query is not None:
            if self.sets:
                sets = self.sets
                for s in sets:
                    notes.append(self.col.findNotes('(%s) (tag:%s or tag:%s::*)' % (query, s, s)))
            else:
                sets = ['']
                notes.append(self.col.findNotes('%s' % query))
        else:
            cardIds = self.cardIds()
            cursor = self.col.db.execute(r"""
SELECT guid, flds, mid, tags
FROM notes
WHERE id IN
  (SELECT nid
   FROM cards
   WHERE cards.id IN %s)
ORDER BY sfld""" % ids2str(cardIds))

        paths = []
        for note_ids, group_name in izip(notes, sets):
            if group_name:
                cur_path, ext = os.path.splitext(path)
                cur_path = '%s-%s%s' % (cur_path, group_name, ext)
            else:
                cur_path = path
            with codecs.open(cur_path, 'w', encoding='utf-8') as output:
                paths.append(cur_path)
                generator = TOMLGenerator(output)

                for id, flds, mid, tags in self.col.db.execute(r"""
SELECT guid, flds, mid, tags FROM notes
WHERE id IN %s
ORDER BY sfld""" % ids2str(note_ids)):
                    fieldData = splitFields(flds)
                    om = outputModels[mid]
                    output.write('[[notes]]\n')
                    output.write("model = '%s'\n" % om.name)
                    output.write("guid = '%s'\n" % id)
                    for i, name in enumerate(om.fieldNames):
                        f = fieldData[i]
                        if name == u'note-id':
                            f = int(f)
                        generator.gen_key_value(name, f)
                    tags = self._fixup_tags(tags)
                    generator.gen_key_value(u'tags', tags)
                    output.write('\n')
                    count += 1

        mode = 'a' if len(sets) == 1 else 'w'
        filteredModels = []
        for v in outputModels.itervalues():
            n = v.model.copy()
            n['tags'] = []
            filteredModels.append(n)

        with codecs.open(path, mode, encoding='utf-8') as output:
            data = {'models': filteredModels}
            toml.dump(output, data)

        if verify:
            self.verify(paths)
        self.count = count
        return True

    re_tag_fixup = re.compile(r'(?:marked)(\s+|\Z)')

    @classmethod
    def _fixup_tags(cls, tags):
        tags = cls.re_tag_fixup.sub('', tags)
        return tags.strip()

    def verify(self, paths):
        p1 = subprocess.Popen(['cat'] + paths, stdout=subprocess.PIPE)
        p2 = subprocess.Popen(['tomljson'], stdin=p1.stdout, stdout=subprocess.PIPE)
        p1.stdout.close()
        exp_data = json.loads(p2.communicate()[0])
        notes = exp_data['notes']
        note_tbl = {}
        for n in notes:
            note_tbl[n['note-id']] = n

        for flds, in self.col.db.execute(r"""
SELECT flds FROM notes
WHERE id IN %s""" % ids2str(note_tbl.keys())):
            flds = splitFields(flds)
            nid = flds[0]
            want = flds[1]
            n = note_tbl[int(nid)]
            if want != n['text']:
                showWarning('Mismatch text %s\n\nWant %s\n\nGot %s' % (nid, repr(want), repr(n['text'])))
            want = flds[2]
            if want != n['extra']:
                showWarning('Mismatch extra %s\n\nWant %s\n\nGot %s' % (nid, repr(want), repr(n['extra'])))


# def update_exporters_list(exps):
#     def _id(obj):
#         return "%s (*%s)" % (obj.key, obj.ext), obj
#
#     exps.append(_id(TOMLNoteExporter))
#
#
# addHook("exportersList", update_exporters_list)

class ExportDialog(QDialog):

    def __init__(self, mw):
        QDialog.__init__(self, mw, Qt.Window)
        self.mw = mw
        self.setupUi()
        self.fillValues()

    def openProfile(self):
        path_name = self.getProfilePathName()
        if path_name:
            self.profile_edit.setText(path_name)

    def openOutput(self):
        path_name = self.getOutputPathName()
        if path_name:
            self.output_edit.setText(path_name)

    def onAccept(self):
        ok = self.readValues()
        if ok:
            exporter = TOMLNoteExporter(mw.col, query=mw.ankisport.query, sets=mw.ankisport.sets)
            ok = exporter.doExport(mw.ankisport.output_path, verify=mw.ankisport.verify)
            if ok:
                tooltip("Exported %d notes" % exporter.count, parent=self.mw)
        if ok:
            QDialog.accept(self)

    def onReject(self):
        self.close()

    def getProfilePathName(self):
        filter = 'TOML Files (*.toml)'
        return unicode(QFileDialog.getOpenFileName(mw, "Exporter Profile",
                                                   mw.ankisport.profile_path, filter))

    def getOutputPathName(self):
        filter = 'TOML Files (*.toml)'
        return unicode(QFileDialog.getSaveFileName(mw, "Export to file",
                                                   mw.ankisport.output_path, filter))

    def fillValues(self):
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

    def setupUi(self):
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
        profile_btn = QPushButton("Open &Profile", clicked=self.openProfile)
        grid.addWidget(profile_btn, 0, 4, 1, 1)

        output_label = QLabel('Output')
        grid.addWidget(output_label, 1, 0, 1, 1)
        self.output_edit = QLineEdit()
        grid.addWidget(self.output_edit, 1, 1, 1, 3)
        output_btn = QPushButton("Open &Output", clicked=self.openOutput)
        grid.addWidget(output_btn, 1, 4, 1, 1)

        self.verify_btn = QCheckBox('&Verify')
        grid.addWidget(self.verify_btn, 2, 0, 1, 2)

        l_main.addLayout(grid)
        button_box = QDialogButtonBox(self)
        button_box.setOrientation(QtCore.Qt.Horizontal)
        button_box.setStandardButtons(QtGui.QDialogButtonBox.Cancel|QtGui.QDialogButtonBox.Ok)
        button_box.accepted.connect(self.onAccept)
        button_box.rejected.connect(self.onReject)
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

action = QAction('TOML Export...', mw)
action.triggered.connect(displayDialog)
mw.form.menuTools.addAction(action)
mw.ankisport = Settings()
