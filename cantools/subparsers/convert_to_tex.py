#!/usr/bin/env python3

import os
import shutil
import tempfile
import subprocess
import datetime
import re


CANTOOLS_LITTLE_ENDIAN = "little_endian"
CANTOOLS_BIG_ENDIAN = "big_endian"


class Environmet:

    ENV_TABULAR = "tabular"
    ENV_TABULARX = "tabularx"
    ENV_LTABLEX = "ltablex"
    ENV_LTABLEX_KEEP_X = "ltablex-keep-x"
    ENV_XLTABULAR = "xltabular"

    def __init__(self, name):
        self.name = name

    def get_usepackage(self):
        env = self.name
        if env == self.ENV_TABULARX:
            return r"\usepackage{tabularx}"
        if env == self.ENV_LTABLEX:
            return r"\usepackage{ltablex}"
        if env == self.ENV_LTABLEX_KEEP_X:
            return r"\usepackage{ltablex}" + "\n" + r"\keepXColumns"
        if env == self.ENV_XLTABULAR:
            return r"\usepackage{xltabular}"
        return None

    def get_env_name(self):
        env = self.name
        if env in (self.ENV_LTABLEX, self.ENV_LTABLEX_KEEP_X):
            env = "tabularx"
        return env

    def needs_width(self):
        env = self.name
        if env == self.ENV_TABULARX:
            return True
        if env == self.ENV_LTABLEX:
            return True
        if env == self.ENV_LTABLEX_KEEP_X:
            return True
        if env == self.ENV_XLTABULAR:
            return True
        return False

    def supports_x_column(self):
        return self.needs_width()

    def number_required_runs(self):
        if self.name == self.ENV_LTABLEX:
            return 3
        if self.name == self.ENV_LTABLEX_KEEP_X:
            return 3
        return 1

    def is_long(self):
        if self.name == self.ENV_LTABLEX:
            return True
        if self.name == self.ENV_LTABLEX_KEEP_X:
            return True
        if self.name == self.ENV_XLTABULAR:
            return True
        return False

    def __repr__(self):
        return self.name

    def __eq__(self, other):
        if not isinstance(other, Environmet):
            return NotImplemented
        return self.name == other.name


class Converter:

    MSG_SORT_KEY_ID = "id"
    MSG_SORT_KEY_NAME = "name"
    MSG_SORT_KEYS = (MSG_SORT_KEY_ID, MSG_SORT_KEY_NAME)

    SIG_SORT_KEY_START_BIT = "start"
    SIG_SORT_KEY_NAME = "name"
    SIG_SORT_KEYS = (SIG_SORT_KEY_START_BIT, SIG_SORT_KEY_NAME)

    ENVS = (
        Environmet(Environmet.ENV_TABULAR),
        Environmet(Environmet.ENV_TABULARX),
        Environmet(Environmet.ENV_LTABLEX),
        Environmet(Environmet.ENV_LTABLEX_KEEP_X),
        Environmet(Environmet.ENV_XLTABULAR),
    )

    ext_tex = ".tex"
    ext_pdf = ".pdf"
    supported_extensions = (ext_tex, ext_pdf)

    cmd_tex = ["pdflatex", "-interaction=nonstop"]

    magic_comment = r"% !TeX program = pdflatex"
    preamble = r"""
\usepackage{typearea}
\usepackage{parskip}
\usepackage{booktabs}
\usepackage{siunitx}
\usepackage{fancyhdr}
\usepackage{lastpage}
\usepackage{hyperref}

\hypersetup{hidelinks}
\setcounter{secnumdepth}{0}

\providecommand{\degree}{\ensuremath{^\circ}}
\newcommand{\thead}[1]{#1}

\makeatletter
    \newcommand{\thetitle}{\@title}
    \newcommand{\thedate}{\@date}
    \newcommand{\theauthor}{\@author}
\makeatother
\renewcommand{\maketitle}{%
    \begin{center}%
        \Large
        \thetitle
    \end{center}%
}

\iffalse
	\usepackage[table]{xcolor}
	\catcode`*=\active
	\def*{\rowcolor{green!20}}
\fi
"""
    begin_document = r"\begin{document}\maketitle"
    table_of_contents = r"\tableofcontents"
    end_document = r"\end{document}"

    begin_msg = r"""
\section{{0x{frame_id:03X} {name}}}
{extended_or_base} \\
DLC = {length}
"""
    end_msg = ""

    base_frame = "Base frame"
    extended_frame = "Extended frame"

    little_endian = "little-endian"
    big_endian = "big-endian"
    little_endian_abbr = "LE"
    big_endian_abbr = "BE"

    optional_line_break = r"\-"


    def save(self, fn, db, args):
        if fn.endswith(self.ext_pdf):
            self.save_pdf(fn, db, args)
        else:
            self.save_tex(fn, db, args)

    def save_pdf(self, fn, db, args):
        with tempfile.TemporaryDirectory() as path:
            tmpfn = "main"
            cwd = os.getcwd()
            os.chdir(path)
            fn_in = os.path.join(path, tmpfn + self.ext_tex)
            fn_out = os.path.join(path, tmpfn + self.ext_pdf)
            self.save_tex(fn_in, db, args)
            cmd = self.cmd_tex + [fn_in]
            for i in range(self.get_number_runs(args)):
                p = subprocess.run(cmd)
                if p.returncode != 0:
                    print("\n! Not rerunning %s because an error occurred" % cmd[0])
                    break
            os.chdir(cwd)
            shutil.move(fn_out, fn)

    def get_number_runs(self, args):
        # for pdf outline (hyperref) and lastpage
        n = 2
        if args.toc:
            n = 2
        n = max(n, args.env.number_required_runs())
        return n

    def save_tex(self, fn, db, args):
        with open(fn, 'wt') as f:
            f.write(self.create_tex_document(db, args))

    def create_tex_document(self, db, args):
        out = []
        out.append(self.magic_comment)
        out.append(r"\documentclass[%s]{%s}" % (", ".join(args.class_options), args.document_class))
        out.append(self.preamble)
        env_usepackage = args.env.get_usepackage()
        if env_usepackage:
            out.append(env_usepackage)
        out.append("")
        out.append(r"\KOMAoption{DIV}{%s}" % args.div)
        out.append("")
        out.append(r"\title{%s}" % self.texify(self.get_title(args)))
        out.append(r"\date{%s}" % self.texify(self.get_date(args)))
        out.append(r"\newcommand{\infile}{%s}" % self.texify(args.infile))
        out.append(r"\newcommand{\outfile}{%s}" % self.texify(args.outfile))
        out.append(self.get_header_footer(args))
        out.append("")
        if args.sig_name_break_anywhere:
            out.append(r"\usepackage{url}")
            out.append(r"\makeatletter")
            out.append(r"\expandafter\def\expandafter\UrlBreaks\expandafter{\UrlBreaks%s}" % "".join(r"\do %s" % chr(c) for c in range(ord('a'), ord('z')+1)))
            out.append(r"\makeatother")
            out.append(r"\newcommand{\sig}[1]{#1}")
            out.append(r"\DeclareUrlCommand\sig{\urlstyle{rm}}")
            out.append("")
        else:
            macrodef = r"\def%s{\discretionary{%s}{%s}{}}" % (self.optional_line_break, args.sig_name_before_break_character, args.sig_name_after_break_character)
            out.append(r"\newcommand{\sig}[1]{{%s#1}}" % macrodef)
            out.append("")

        out.append(self.begin_document)
        if args.toc:
            out.append(self.table_of_contents)
        out.append(self.format_db(db, args))
        out.append(self.end_document)
        return "\n".join(out)

    def get_title(self, args):
        if args.title:
            return args.title
        title = args.outfile
        title = title.rsplit(os.extsep, 1)[0]
        title = title.replace("_", " ")
        title = title[0:1].upper() + title[1:]
        title = self.capitalize_after(title, " ")
        title = self.capitalize_after(title, "-")
        return title

    def get_header_footer(self, args):
        out = []
        out.append(r"")
        out.append(r"\fancyhead{}")
        out.append(r"\fancyhead[ol,er]{%s}" % args.header_left)
        out.append(r"\fancyhead[or,el]{%s}" % args.header_right)
        out.append(r"\renewcommand{\headrulewidth}{0pt}")
        out.append(r"\fancyfoot{}")
        out.append(r"\fancyfoot[ol,er]{%s}" % args.footer_left)
        out.append(r"\fancyfoot[or,el]{%s}" % args.footer_right)
        out.append(r"\pagestyle{fancy}")
        out.append(r"")
        return "\n".join(out)

    def get_date(self, args):
        if args.date:
            return args.date
        return datetime.date.today().strftime(args.date_format)

    @staticmethod
    def capitalize_after(text, c):
        i = 0
        while True:
            i = text.find(c, i)
            if i < 0:
                break
            text = text[:i+1] + text[i+1:i+2].upper() + text[i+2:]
            i += 1
        return text

    def format_db(self, db, args):
        self.none = "{%s}" % args.none
        out = []
        for msg in sorted(db.messages, key=self.msg_sort_key(args)):
            out.append(self.format_message(msg, args))
        return "\n".join(out)

    def msg_sort_key(self, args):
        key = args.msg_sort_key
        if key == self.MSG_SORT_KEY_NAME:
            return lambda msg: msg.name
        else:
            return lambda msg: msg.frame_id

    def sig_sort_key(self, args):
        key = args.sig_sort_key
        if key == self.SIG_SORT_KEY_NAME:
            return lambda sig: sig.name
        else:
            return lambda sig: sig.start

    def format_message(self, msg, args):
        format_dict = self.message_format_dict(msg)
        out = []
        out.append(self.begin_msg.format(**format_dict))
        out.append(self.format_signals(msg.signals, args))
        out.append(self.end_msg.format(**format_dict))
        return "\n".join(out)

    def get_begin_table(self, signals, args):
        out = ""
        if args.sig_before_tabular:
            out += args.sig_before_tabular + "\n"

        out += r"\begin{%s}" % args.env.get_env_name()
        if args.env.needs_width():
            out += "{%s}" % args.sig_width
        out += r"{%s}" % self.get_colspec(signals, args)

        header = self.format_header(args)
        if not args.env.is_long():
            out += "\n\\toprule"
            out += "\n" + header
            out += "\n\\midrule"
        else:
            indentation = self.get_indentation(args)
            out += "\n" + indentation + r"\toprule"
            out += "\n" + indentation + header
            out += "\n" + indentation + r"\midrule"
            out += "\n" + r"\endfirsthead"
            out += "\n" + indentation + header
            out += "\n" + indentation + r"\midrule"
            out += "\n" + r"\endhead"
            out += "\n" + indentation + r"\midrule"
            if args.table_break_text:
                out += "\n" + indentation + indentation + r"\multicolumn{\expandafter\the\csname LT@cols\endcsname}{%s}{%s} \\" % (args.table_break_text_align, args.table_break_text)
            out += "\n" + r"\endfoot"
            out += "\n" + indentation + r"\bottomrule"
            out += "\n" + r"\endlastfoot"
            out += "\n"

        return out

    def get_indentation(self, args):
        pattern = args.sig_pattern
        i = 0
        n = len(pattern)
        while i<n and pattern[i].isspace():
            i += 1
        return pattern[:i]

    def get_end_table(self, args):
        out = ""
        if not args.env.is_long():
            out += "\\bottomrule\n"
        out += r"\end{%s}" % args.env.get_env_name()
        if args.sig_after_tabular:
            out += "\n" + args.sig_after_tabular
        return out

    def message_format_dict(self, msg):
        out = {
            'name' : msg.name,
            'frame_id' : msg.frame_id,
            'is_extended_frame' : msg.is_extended_frame,
            'extended_or_base' : self.extended_frame if msg.is_extended_frame else self.base_frame,
            'length' : msg.length,
        }
        out = {key:self.texify(val) for key,val in out.items()}
        return out

    def get_colspec(self, signals, args):
        coltypes = self.colspec_dict(args)
        out = []
        for col in args.sig_pattern.rstrip("\\").split("&"):
            col = col.strip()
            col = col[1:][:-1]
            alignment = coltypes.get(col, "l")
            if alignment == "S":
                alignment = self.measure_s_column(col, signals)
            out.append(alignment)
        return "".join(out)

    def measure_s_column(self, col, signals):
        max_left = 0
        max_right = 0
        for sig in signals:
            val = getattr(sig, col)
            if val is None:
                val = 0
            val = "{:0f}".format(val).rstrip("0")
            if "." in val:
                val = val.split(".", 1)
                left, right = len(val[0]), len(val[1])
            else:
                left = len(val)
                right = 0
            if left > max_left:
                max_left = left
            if right > max_right:
                max_right = right
        return "S[table-format=%s.%s]" % (max_left, max_right)

    def format_header(self, args):
        return args.sig_pattern.format(**self.header_format_dict())

    def format_signals(self, signals, args):
        if not signals:
            return args.sig_none

        out = []
        out.append(self.get_begin_table(signals, args))
        for sig in sorted(signals, key=self.sig_sort_key(args)):
            out.append(args.sig_pattern.format(**self.signal_format_dict(sig, args)))
        out.append(self.get_end_table(args))
        return "\n".join(out)

    def colspec_dict(self, args):
        text = 'c'
        num = 'S'
        x = 'X' if args.env.supports_x_column() else 'l'
        out = {
            'name' : x,
            'start' : num,
            'length' : num,
            'byte_order' : text,
            'byte_order_abbr' : text,

            'datatype' : text,
            'is_float' : text,
            'is_signed' : text,

            'initial' : num,
            'scale' : num,
            'offset' : num,
            'minimum' : num,
            'maximum' : num,
            'unit' : text,

            'choices' : text,
            'comment' : text,
            'comments' : text,

            'is_multiplexer' : text,
            'multiplexer_ids' : text,
            'multiplexer_signal' : text,
        }
        return out

    def header_format_dict(self):
        out = {
            'name' : 'Name',
            'start' : 'Start',
            'length' : 'Bits',
            'byte_order' : 'Byte order',
            'byte_order_abbr' : 'E',

            'datatype' : 'Type',
            'is_float' : 'Float?',
            'is_signed' : 'Digned?',

            'initial' : 'Initial',
            'scale' : 'Scale',
            'offset' : 'Offset',
            'minimum' : 'Min',
            'maximum' : 'Max',
            'unit' : 'Unit',

            'choices' : 'Choices',
            'comment' : 'Comment',
            'comments' : 'Comments',

            'is_multiplexer' : 'Multiplexer?',
            'multiplexer_ids' : 'Multiplexer IDs',
            'multiplexer_signal' : 'Multiplexer signal',
        }
        out = {key:self.texify(val) for key,val in out.items()}
        out = {key: r"{\thead{%s}}" % val for key,val in out.items()}
        return out

    def signal_format_dict(self, sig, args):
        out = {
            'start' : sig.start,
            'length' : sig.length,
            'byte_order' : self.get_byte_order(sig),
            'byte_order_abbr' : self.get_byte_order_abbr(sig),

            'datatype' : self.get_datatype(sig),
            'is_float' : sig.is_float,
            'is_signed' : sig.is_signed,

            'initial' : sig.initial,
            'scale' : sig.decimal.scale,
            'offset' : sig.decimal.offset,
            'minimum' : sig.decimal.minimum,
            'maximum' : sig.decimal.maximum,
            'unit' : sig.unit,

            'choices' : sig.choices,
            'comment' : sig.comment,
            'comments' : sig.comments,

            'is_multiplexer' : sig.is_multiplexer,
            'multiplexer_ids' : sig.multiplexer_ids,
            'multiplexer_signal' : sig.multiplexer_signal,
        }
        out = {key:self.texify(val) for key,val in out.items()}
        if args.sig_name_break_anywhere:
            out['name'] = r'\sig{%s}' % sig.name
        else:
            out['name'] = r'\sig{%s}' % self.break_sig_name(self.texify(sig.name))
        return out

    def texify(self, val):
        if val is None:
            return self.none
        if isinstance(val, int):
            return val

        val = str(val)
        val = val.replace("\\", r"\textbackslash ")
        val = val.replace("{", r"\{")
        val = val.replace("}", r"\}")
        val = val.replace("$", r"\$")
        val = val.replace("&", r"\&")
        val = val.replace("#", r"\#")
        val = val.replace("^", r"\string^")
        val = val.replace("_", r"\_")
        val = val.replace("~", r"\string~")
        val = val.replace("%", r"\%")

        val = val.replace("°", r"\degree ")
        return val

    @classmethod
    def break_sig_name(cls, name):
        # I'm starting and ending +/-1 to avoid trouble with look ahead and look back
        # I wouldn't want a line break before the first or last character, anyway
        i = 1
        n = len(name) - 1
        while i < n:
            # "CamelCase" -> "Camel\-Case", "CANMessage" -> "CAN\-Message"
            if name[i].isupper() and name[i+1].islower():
                name = name[:i] + cls.optional_line_break + name[i:]
                i += len(cls.optional_line_break)
            # "CommandID" -> "Command\-ID"
            if name[i].isupper() and name[i-1].islower():
                name = name[:i] + cls.optional_line_break + name[i:]
                i += len(cls.optional_line_break)
            # "snake\_case" -> "snake\_\-case"
            if name[i] != "\\" and not name[i].isalpha() and name[i+1].isalpha():
                name = name[:i+1] + cls.optional_line_break + name[i+1:]
                i += len(cls.optional_line_break)
            i += 1
        return name

    def get_datatype(self, sig):
        if sig.is_float:
            return "float"
        elif sig.is_signed:
            return "int"
        else:
            return "uint"

    def get_byte_order(self, sig):
        if sig.byte_order == CANTOOLS_LITTLE_ENDIAN:
            return self.little_endian
        elif sig.byte_order == CANTOOLS_BIG_ENDIAN:
            return self.big_endian
        else:
            raise ValueError("invalid value for {sig.name}.byte_order: {sig.byte_order}".format(sig=sig))

    def get_byte_order_abbr(self, sig):
        if sig.byte_order == CANTOOLS_LITTLE_ENDIAN:
            return self.little_endian_abbr
        elif sig.byte_order == CANTOOLS_BIG_ENDIAN:
            return self.big_endian_abbr
        else:
            raise ValueError("invalid value for {sig.name}.byte_order: {sig.byte_order}".format(sig=sig))


def add_argument_group(parser):
    def length(val):
        if re.match(r"^\d*([.,]\d*)?$", val):
            val += r"\linewidth"
        return val
    group = parser.add_argument_group("TeX converter options")
    group.add_argument("--msg-sort", dest="msg_sort_key", choices=Converter.MSG_SORT_KEYS, default=Converter.MSG_SORT_KEY_ID)
    group.add_argument("--sig-sort", dest="sig_sort_key", choices=Converter.SIG_SORT_KEYS, default=Converter.SIG_SORT_KEY_START_BIT)
    group.add_argument("--none", default="--", help="a symbol to print if a value is None")

    group.add_argument("--document-class", default='article')
    group.add_argument("--class-option", dest="class_options", default=['a4paper'], action="append", help="")
    group.add_argument("--landscape", dest="class_options", const="landscape", action="append_const", help="a shortcut for `--class-option landscape`")
    group.add_argument("--toc", action="store_true", help="insert a table of contents")
    group.add_argument("--env", type=Environmet, choices=Converter.ENVS, default=Converter.ENVS[0], help="the environment to use for the signals")
    group.add_argument("--div", default=12, help="the bigger this factor, the bigger the text body. see https://ctan.org/pkg/typearea")
    group.add_argument("--title")
    group.add_argument("--date")
    group.add_argument("--date-format", default="%d.%m.%Y")
    group.add_argument("--header-left", default=r"")
    group.add_argument("--header-right", default=r"")
    group.add_argument("--footer-left", default=r"Created from \infile, \thedate")
    group.add_argument("--footer-right", default=r"Page \thepage\ of \pageref{LastPage}")
    group.add_argument("--table-break-text", default="(continued on next page)", help=r"a text shown at the bottom of a page where a long table is broken")
    group.add_argument("--table-break-text-align", default="r", help=r"the column specification for --table-break-text, e.g. r, l or c")

    group.add_argument("--sig-width", default="1", type=length, help=r"if --env needs a width, this is it's argument. If no unit is specified \linewidth is assumed.")
    group.add_argument("--sig-before-tabular", default="\\begingroup\n\\centering", help=r"TeX code put before each signal table")
    group.add_argument("--sig-after-tabular", default="\\par\n\\endgroup", help=r"TeX code put after each signal table")
    default_sig_pattern = "\t{name} && {start} & {length} & {byte_order_abbr} && {datatype} & {scale} & {offset} && {minimum} & {maximum} & {unit} \\\\"
    group.add_argument("--sig-pattern", default=default_sig_pattern, help=r"a pattern specifying how a row in the table of signals is supposed to look like. Must contain the \\. Remember that your shell may require to escape a backslash.")
    group.add_argument("--sig-none", default="This message has no signals.", help=r"a text to be printed instead of the signals table if no signals are defined for that message")
    group.add_argument("--sig-name-break-anywhere", action="store_true", help=r"use the url package to allow a line break in a signal name after any character")
    group.add_argument("--sig-name-before-break-character", default="-", help=r"inserted before a line break inside of a signal name (only without --sig-name-break-anywhere)")
    group.add_argument("--sig-name-after-break-character", default=r"\hbox{\footnotesize$\hookrightarrow$ }", help=r"inserted after a line break inside of a signal name (only without --sig-name-break-anywhere)")
