# coding=utf-8
import json
import subprocess
import textwrap
from collections import defaultdict
from datetime import datetime
from itertools import islice, izip

import codecs
import os
import re
from anki.exporting import Exporter
from anki.utils import splitFields, ids2str
from aqt.utils import showWarning

import pytoml as toml


class keydefaultdict(defaultdict):
    """
    A defaultdict with a custom default_factory that passes the key as an argument
    """
    def __missing__(self, key):
        if self.default_factory is None:
            raise KeyError(key)
        else:
            ret = self[key] = self.default_factory(key)
            return ret


class TOMLGenerator(object):
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

    def write_escaped_string(self, s):
        self.output.write(self.escape_string(s))

    def write_multiline_escaped_string(self, s):
        escape_re_sub_tab = self.escape_re_sub_tab
        self.output.write(
            self.ml_escape_re.sub(lambda c: '\\' + (escape_re_sub_tab.get(c.group(1), None)
                                                    or ('u%.4x' % ord(c.group(1)))), s))

    def wrap_text(self, s, offset):
        tw = self.text_wrapper
        tw.initial_indent = ' ' * offset

        lines = []
        for para in s.splitlines(True):
            line = tw.wrap(para)
            if tw.initial_indent and line:
                line[0] = line[0][offset:]
            lines.extend(line)
            tw.initial_indent = ''
        return lines

    def write_string(self, line_offset, v):
        output = self.output
        ws_match_re = self.ws_match_re

        lines = self.wrap_text(v, 0)

        if len(lines) == 0:
            output.write('""\n')
            return
        elif len(lines) == 1:
            multiline = False
            # prefer not to use literal style if there are control characters, can't if there's a quote
            use_literal_string = use_multiline_literal = False
            if not re.search(r"[\x00-\x1f\x7f\x80-\x9f]", lines[0]):
                use_literal_string = lines[0].find("'") == -1
                use_multiline_literal = not use_literal_string and lines[0].find("'''") == -1
        else:
            multiline = True

        if multiline:
            output.write('"""\n')
            self.write_multiline_escaped_string(lines[0])
            trailing_newline = lines[0][-1] == u'\n'
            trailing_quote = False
            for line in islice(lines, 1, None):
                # fix up previous line, appending any leading whitespace on this line since TOML will ignore
                # leading whitespace after a continuation
                leading_white_space = ws_match_re.match(line)
                if leading_white_space:
                    ws = leading_white_space.group()
                    trailing_newline = ws[-1] == u'\n'
                    self.write_multiline_escaped_string(ws)
                    line = line[leading_white_space.end():]
                if not trailing_newline:
                    output.write('\\\n')
                self.write_multiline_escaped_string(line)
                trailing_newline = line[-1] == u'\n'
                trailing_quote = line[-1] == u'"'
            output.write('"""\n' if not trailing_quote else '\\\n"""\n')
        else:
            if use_literal_string:
                output.write("'%s'\n" % lines[0])
            elif use_multiline_literal:
                output.write("'''%s'''\n" % lines[0])
            else:
                output.write('"')
                self.write_escaped_string(lines[0])
                output.write('"\n')


    def write_bool(self, line_offset, v):
        self.output.write('true' if v else 'false')
        self.output.write('\n')

    def write_integer(self, line_offset, v):
        self.output.write(str(v))
        self.output.write('\n')

    write_float = write_integer

    def write_datetime(self, line_offset, v):
        self.output.write(v.strftime(self.DATETIME_ISO8601_FORMAT))
        self.output.write('\n')

    VALUE_MAP = {unicode: write_string, bool: write_bool, int: write_integer, long: write_integer, float: write_float, datetime: write_datetime}

    def write_value(self, line_offset, v):
        for t, c in self.VALUE_MAP.iteritems():
            if isinstance(v, t):
                return c(self, line_offset, v)

    def write_key_value(self, k, v):
        if re.search(r"[^A-Za-z0-9_-]", k):
            ko = '"%s" = ' % self.escape_string(k)
        else:
            ko = '%s = ' % k
        self.output.write(ko)
        self.write_value(len(ko), v)


class OutputModel(object):
    def __init__(self, models, mid):
        model = models.get(mid)
        field_names = models.fieldNames(model)
        field_set = set(field_names)

        def transform_name(name):
            """
            Transform names to be unquoted TOML key friendly.
            Do it only if it will not cause ambiguity.
            """
            n = name.lower().replace(' ', '-')
            return n if n not in field_set else name

        self.field_names = [transform_name(fn) for fn in field_names]
        self.name = model['name']
        self.model = model


class TOMLNoteExporter(Exporter):
    key = _("Notes in TOML format")
    ext = ".toml"

    def __init__(self, col, query=None, sets=None, set_name=''):
        """
        Create a TOML Note Exporter.
        
        :param col: The anki collection object. 
        :param query: An anki filter string to select notes for export.
        :param sets: A set of tags to break the cards into smaller files.
        """
        Exporter.__init__(self, col)
        self.query = query
        self.sets = sets
        self.set_name = set_name

    def exportInto(self, path):
        file = codecs.open(path, "w", encoding='utf-8')
        self.doExport(file)
        file.close()

    def doExport(self, path, verify=False):
        models = self.col.models
        output_models = keydefaultdict(lambda mid: OutputModel(models, mid))

        count = 0
        grouped_notes = []
        sets = None
        if self.query is not None:
            if self.sets:
                sets = self.sets
                for group_name, expr in sets.items():
                    grouped_notes.append((group_name, self.col.findNotes('(%s) (%s)' % (self.query, expr))))
            else:
                grouped_notes.append(('', self.col.findNotes('%s' % self.query)))
        else:
            grouped_notes.append(('', self.cardIds()))

        paths = []
        for group_name, note_ids in grouped_notes:
            if group_name:
                dirname, _ = os.path.split(path)
                cur_path = os.path.join(dirname, group_name + '.toml')
            else:
                cur_path = path
            with codecs.open(cur_path, 'w', encoding='utf-8') as output:
                paths.append(cur_path)
                generator = TOMLGenerator(output)

                for guid, flds, mid, tags in self.col.db.execute(r"""
SELECT guid, flds, mid, tags FROM notes
WHERE id IN %s
ORDER BY sfld""" % ids2str(note_ids)):
                    field_data = splitFields(flds)
                    cur_model = output_models[mid]
                    output.write('[[notes]]\n')
                    output.write("model = '%s'\n" % cur_model.name)
                    output.write("guid = '%s'\n" % guid)
                    for i, name in enumerate(cur_model.field_names):
                        f = field_data[i]
                        if name == u'note-id':
                            try:
                                f = int(f)
                            except ValueError:
                                pass
                        generator.write_key_value(name, f)
                    tags = self.fixup_tags(tags)
                    generator.write_key_value(u'tags', tags)
                    output.write('\n')
                    count += 1

        mode = 'a' if not sets else 'w'
        filtered_models = []
        for v in output_models.values():
            n = v.model.copy()
            # not sure the importance of this value and it leaks unwanted data
            n['tags'] = []
            n.pop('req', None)
            filtered_models.append(n)

        with codecs.open(path, mode, encoding='utf-8') as output:
            data = {'models': filtered_models}
            toml.dump(output, data)

        if verify:
            self.verify(paths)
        self.count = count
        return True

    re_tag_fixup = re.compile(r'(?:marked|leech)(\s+|\Z)')

    @classmethod
    def fixup_tags(cls, tags):
        tags = cls.re_tag_fixup.sub('', tags)
        return tags.strip()

    def verify(self, paths):
        """
        lel at this shitty verify function
        """
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