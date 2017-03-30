# coding=utf-8
import codecs
from itertools import islice
from unicodedata import east_asian_width

from anki.exporting import Exporter
from anki.hooks import addHook
from anki.lang import _
from anki.utils import ids2str, splitFields
import re
from collections import defaultdict
from datetime import datetime
import textwrap

class TOMLGenerator(object):
    g_newline = '\n'

    DATETIME_ISO8601_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
    ESCAPE_RE = re.compile(r'([\x00-\x1f"\\])')
    ESCAPE_REPL = {'\t': 't', '\n': 'n', '\"': '"', '\r': 'r', '\\': '\\', '\f': 'f', '\b': 'b', '"""': r'"""'}
    ML_ESCAPE = re.compile(r'([\x00-\x09\x0b-\x1f\\]|""")')
    WS_MATCH = re.compile(r'^[\t ]+')
    SQ_DISALLOWED = re.compile(r"[\x00-\x1f']")
    KEY_DISALLOWED = re.compile(r"[^A-Za-z0-9_-]")

    def __init__(self, output):
        self.output = output
        self.text_wrapper = textwrap.TextWrapper(width=124, expand_tabs=False, replace_whitespace=False, drop_whitespace=False)

    @classmethod
    def escape_string(cls, s):
        ESCAPE_REPL = cls.ESCAPE_REPL
        return cls.ESCAPE_RE.sub(lambda c: '\\' + (ESCAPE_REPL.get(c.group(1), None)
                                                   or ('u%.4x' % ord(c.group(1)))), s)

    def g_write_escaped(self, s):
        self.output.write(self.escape_string(s))

    def g_write_ml_escaped(self, s):
        ESCAPE_REPL = self.ESCAPE_REPL
        self.output.write(
            self.ML_ESCAPE.sub(lambda c: '\\' + (ESCAPE_REPL.get(c.group(1), None)
                                                 or ('u%.4x' % ord(c.group(1)))), s))

    def wrap_text(self, s, offset):
        tw = self.text_wrapper
        tw.initial_indent = ' ' * offset

        lines = []
        for para in s.splitlines():
            sl = tw.wrap(para)
            if tw.initial_indent and sl:
                sl[0] = sl[0][offset:]
            lines.extend(sl)
            tw.initial_indent = ''
        return lines

    def g_string(self, line_offset, v):
        output = self.output
        WS_MATCH = self.WS_MATCH

        lines = self.wrap_text(v, line_offset)

        if len(lines) == 0:
            output.write('""\n')
            return
        elif len(lines) == 1:
            multiline = False
            singlequote = False if self.SQ_DISALLOWED.search(lines[0]) else True
            if not singlequote:
                unescaped_style = lines[0].find("'''") == -1
        else:
            multiline = True

        if multiline:
            output.write('"""\n')
            self.g_write_ml_escaped(lines[0])
            trailing_nl = lines[0][-1] == u'\n'
            trailing_quote = False
            for line in islice(lines, 1, None):
                # fix up previous line, appending any leading whitespace on this line
                lws = WS_MATCH.match(line)
                if lws:
                    ws = lws.group()
                    trailing_nl = ws[-1] == u'\n'
                    self.g_write_ml_escaped(ws)
                    line = line[lws.end():]
                output.write('\n' if trailing_nl else '\\\n')
                self.g_write_ml_escaped(line)
                trailing_nl = line[-1] == u'\n'
                trailing_quote = line[-1] == u'"'
            output.write('"""\n' if not trailing_quote else '\\n"""\n')
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

    # def g_array(self, line_offset, v):
    #    return '[' + ', '.join(self.gen_value(i) for i in v) + ']'

    VALUE_MAP = {unicode: g_string, bool: g_bool, int: g_integer, long: g_integer, float: g_float, datetime: g_datetime}

    def gen_value(self, line_offset, v):
        for t, c in self.VALUE_MAP.iteritems():
            if isinstance(v, t):
                return c(self, line_offset, v)

    def gen_key_value(self, k, v):
        if self.KEY_DISALLOWED.search(k):
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


class TOMLNoteExporter(Exporter):
    key = _("Notes in TOML format")
    ext = ".toml"

    def __init__(self, col):
        Exporter.__init__(self, col)
        self.includeMedia = True

    def exportInto(self, path):
        file = codecs.open(path, "w", encoding='utf-8')
        self.doExport(file)
        file.close()

    def doExport(self, output):
        generator = TOMLGenerator(output)
        models = self.col.models
        outputModels = keydefaultdict(lambda mid: OutputModel(models, mid))

        cardIds = self.cardIds()
        count = 0

        #        """SELECT guid, flds, mid, tags
        # FROM notes
        # WHERE id IN
        #  (SELECT nid
        #   FROM cards
        #   WHERE cards.id IN %s)
        # ORDER BY sfld""" % ids2str(cardIds)
        # r"""
        # SELECT guid, flds, mid, tags FROM notes
        # WHERE tags LIKE '%Brosencephalon%' ORDER BY sfld"""
        # """

        for id, flds, mid, tags in self.col.db.execute(r"""
SELECT guid, flds, mid, tags FROM notes
         WHERE tags LIKE '%Brosencephalon%' ORDER BY sfld"""):
            fieldData = splitFields(flds)
            om = outputModels[mid]
            output.write('[[note]]\n')
            output.write("model = '%s'\n" % om.name)
            for i, name in enumerate(om.fieldNames):
                f = fieldData[i]
                if name == u'note-id':
                    f = int(f)
                generator.gen_key_value(name, f)
            generator.gen_key_value(u'tags', tags.strip().decode('utf-8'))
            output.write('\n')
            count += 1

        self.count = count


def update_exporters_list(exps):
    def _id(obj):
        return "%s (*%s)" % (obj.key, obj.ext), obj

    exps.append(_id(TOMLNoteExporter))


addHook("exportersList", update_exporters_list)
