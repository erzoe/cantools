import re
import argparse
import readline

import can
from argparse_addons import Integer
from .. import database
from .utils import format_message
from .utils import format_multiplexed_name


class QuitException(Exception):
    pass

class CanBusListener(can.Listener):

    def __init__(self, on_receive, on_error=None):
        self.on_message_received = on_receive
        if on_error:
            self.on_error = on_error

class Cli:

    list_item_choice  = "- {val}: {key}"
    reo_hex = re.compile("[0-9a-f]+", re.I)
    # separator between signal name and value
    sep = "="

    # ------- init -------

    def __init__(self, args):
        self.dbc = database.load_file(args.database,
                                         encoding=args.encoding,
                                         frame_id_mask=args.frame_id_mask,
                                         strict=not args.no_strict)

        self.bus = self.create_bus(args)
        self.register_bus_listener(self.bus)
        self.prompt = args.prompt

        Command.cli = self
        self.internal_commands = {}
        for cmd in globals().values():
            if self.issubclass(cmd, Command):
                self.internal_commands[cmd.get_name()] = cmd
                for alias in cmd.aliases:
                    self.internal_commands[alias] = cmd
        self.run()

    def create_bus(self, args):
        kwargs = {}

        if args.bit_rate is not None:
            kwargs['bitrate'] = int(args.bit_rate)

        try:
            return can.Bus(bustype=args.bus_type,
                           channel=args.channel,
                           **kwargs)
        except:
            raise Exception(
                "Failed to create CAN bus with bustype='{}' and "
                "channel='{}'.".format(args.bus_type,
                                       args.channel))

    def register_bus_listener(self, bus):
        can.Notifier(bus, [CanBusListener(self.on_receive, self.on_error)])


    # ------- main -------

    def run(self):
        while True:
            try:
                ln = self.read_line()
                self.process_line(ln)
            except ParseError as e:
                print(e)
            except QuitException:
                break
            except Exception:
                raise

    def read_line(self):
        return input(self.prompt)

    def process_line(self, ln):
        cmd, args = self.split(ln)
        if self.process_internal_command(cmd, args):
            return
        if self.process_message_to_be_sent(cmd, args):
            return

    def split(self, cmd):
        cmd = cmd.split(' ')
        return cmd[0], cmd[1:]

    def process_internal_command(self, cmd, args):
        cmd = self.internal_commands.get(cmd, None)
        if cmd:
            cmd(args)
            return True
        return False

    def process_message_to_be_sent(self, cmd, args):
        possible_messages = list(self.find_messages(cmd))
        n = len(possible_messages)
        if n <= 0:
            print("command {cmd!r} not found".format(cmd=cmd))
            return
        elif n > 1:
            print("command {cmd!r} is ambiguous:".format(cmd=cmd))
            help_.print_message_list(possible_messages)
            return

        msg = possible_messages[0]
        data = self.parse_data(msg, args)
        is_remote_frame = not data
        if data:
            data = msg.encode(data)
        else:
            data = []
        canmsg = can.Message(
            arbitration_id = msg.frame_id,
            is_extended_id = msg.is_extended_frame,
            is_remote_frame = is_remote_frame,
            data = data,
        )
        print(canmsg)
        self.bus.send(canmsg)


    def parse_data(self, msg, args):
        '''args: a list of strings representing the data. return: dict'''
        data = {}
        positional = True
        for i, arg in enumerate(args):
            if self.sep in arg:
                key, strval = arg.split(self.sep)
                sig = self.find_signal_by_name(msg, key)
                positional = False
            elif positional:
                if i >= len(msg.signals):
                    raise ParseError('you have passed more values than this message has signals')
                else:
                    sig = msg.signals[i]
                    strval = arg
            else:
                raise ParseError('positional argument follows keyword argument')

            if sig.choices:
                val = sig.choice_string_to_number(strval)
            else:
                val = None
            if val is None:
                try:
                    val = self.parse_number(strval)
                except ParseError as e:
                    msg = str(e)
                    if sig.choices:
                        if not msg.endswith('.'):
                            msg += '.'
                        msg += ' valid choices are:\n'
                        msg += '\n'.join(self.list_item_choice.format(key=key, val=val) for val, key in sig.choices.items())
                    raise ParseError(msg)

            data[sig.name] = val

        return data

    def find_messages(self, cmd_input):
        if self.is_int(cmd_input):
            yield from self.find_messages_by_id(self.parse_int(cmd_input))
        else:
            yield from self.find_messages_by_name(cmd_input)

    def find_messages_by_id(self, msg_id):
        for msg in self.dbc.messages:
            if msg.frame_id == msg_id:
                yield msg
                return

    def find_messages_by_name(self, cmd_input):
        reo = re.compile('.*'+re.escape(cmd_input)+'.*', re.I)
        for msg in self.dbc.messages:
            if reo.match(msg.name):
                yield msg

    def find_signal_by_name(self, msg, name):
        signals = list(self.find_signals_by_name(msg, name))
        n = len(signals)
        if n <= 0:
            raise ParseError('unknown signal %r for message %s' % (name, msg))
        elif n > 1:
            error = 'signal %r for message %s is ambiguous:\n' % (name, msg)
            error += '\n'.join(help_.format_signal(sig) for sig in sorted(signals, key=lambda s: s.name))
            raise ParseError(error)
        return signals[0]

    def find_signals_by_name(self, msg, name):
        reo = re.compile('.*'+re.escape(name)+'.*', re.I)
        for sig in msg.signals:
            if reo.match(sig.name):
                yield sig

    # ------- CAN bus events -------

    def on_receive(self, msg):
        print('\r', end='')
        print(msg)
        print(self.prompt, end='', flush=True)

    def on_error(self, error):
        print('\r', end='')
        print(error)
        print(self.prompt, end='', flush=True)


    # ------- utils -------

    @staticmethod
    def issubclass(v, cls):
        if v is cls:
            return False
        try:
            return issubclass(v, cls)
        except TypeError:
            return False

    def is_int(self, text):
        return text[:1].isdigit()

    def parse_int(self, text):
        return int(text, 16)

    def parse_number(self, text):
        try:
            return int(text, base=0)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError:
            pass
        raise ParseError('failed to parse number %r' % text)


# ========== utils ==========

class ParseError(Exception):

    pass

class NotExitingArgumentParser(argparse.ArgumentParser):

    def error(self, message):
        raise ParseError(message)

class Command:
    """Abstract command class"""

    parser = None
    aliases = ()

    # ------- class methods -------

    @classmethod
    def get_name(cls):
        classdict = cls.__mro__[0].__dict__
        if 'name' in classdict and classdict['name']:
            return cls.name
        return cls.__name__

    @classmethod
    def create_parser(cls):
        cls.parser = NotExitingArgumentParser(prog=cls.get_name(), description=cls.__doc__, add_help=False)
        cls.init_parser(cls.parser)

    @classmethod
    def init_parser(cls, parser):
        """override this method if the command takes arguments"""
        parser.add_argument("-h", "--help", action="store_true", help="show this help message")


    # ------- instance methods -------

    def __init__(self, args):
        if self.parser is None:
            self.create_parser()
        args = self.parser.parse_args(args)
        if args.help:
            self.parser.print_help()
            return
        self.execute(args)

    def execute(self, args):
        """
        override this method

        args: argparse.Namespace instance
        """
        pass


# ========== internal commands ==========

class quit(Command):

    aliases = ["q"]

    def execute(self, args):
        raise QuitException()

class help_(Command):

    """
    Print a list of all defined messages (if no message is given)
    or a list of all signals (if msg is given).
    If an asterisc is passed for msg a list of all messages
    with all signals is printed.
    """

    ORDER_BY_ID = "id"
    ORDER_BY_NAME = "name"
    ALLOWED_VALUES_ORDER_BY = [
        ORDER_BY_ID,
        ORDER_BY_NAME,
    ]

    indentation = " " * 4

    name = "help"
    aliases = ["h", "?"]

    @classmethod
    def init_parser(cls, parser):
        super().init_parser(parser)
        parser.add_argument('msg', nargs='?', help='a message you want help with')

        msg_order_args = parser.add_argument_group("message arguments")
        msg_order_args.add_argument('--order-by', choices=cls.ALLOWED_VALUES_ORDER_BY, help='sort messages (default: id)')
        msg_order_args.add_argument('--descending', action='store_true', help='sort messages descending')
        msg_order_args.add_argument('--transmitters', action='store_true', help='show transmitters')

        sig_args = parser.add_argument_group("signal format")
        #sig_args.add_argument('--oneline', action='store_false', dest='multiline', help='print all information regarding one signal in one line')
        sig_args.add_argument('-m', '--multiline', action='store_true', help='break the information regarding a signal across several lines')
        sig_args.add_argument('-a', '--all', action='store_true', help='show all information')
        sig_args.add_argument('--datatype', action='store_true', help='show is_signed and is_float')
        sig_args.add_argument('--min-max', action='store_true', help='show minimum and maximum value if specified')
        sig_args.add_argument('--bits', action='store_true', help='show start bit, length and endianness')
        sig_args.add_argument('--receivers', action='store_true', help='show receivers if specified')

    def execute(self, args):
        if args.all:
            args.datatype = True
            args.min_max = True
            args.bits = True
            args.receivers = True
        msglistkw = dict(order_by=args.order_by, descending=args.descending, show_transmitter=args.transmitters)
        signalkw = dict(multiline=args.multiline, show_datatype=args.datatype, show_min_max=args.min_max, show_bits=args.bits, show_receivers=args.receivers)
        if args.msg == '*':
            self.print_message_list(self.cli.dbc.messages, **msglistkw, signalkw=signalkw)
        elif args.msg:
            messages = list(self.cli.find_messages(args.msg))
            n = len(messages)
            if n <= 0:
                print("unknown message {cmd!r}".format(cmd=args.msg))
            elif n > 1:
                print("{cmd!r} is ambiguous:".format(cmd=args.msg))
                self.print_message_list(messages, **msglistkw, signalkw=signalkw)
            else:
                msg = messages[0]
                self.print_message_help(msg, bullet="", **msglistkw, signalkw=signalkw)
        else:
            self.print_message_list(self.cli.dbc.messages, **msglistkw)


    @classmethod
    def print_message_list(cls, messages, indent=0, bullet="- ", order_by=ORDER_BY_ID, descending=False, show_dlc=True, show_transmitter=False, signalkw=None):
        if order_by == cls.ORDER_BY_NAME:
            key = lambda msg: msg.name
        else:
            key = lambda msg: msg.frame_id

        for msg in sorted(messages, key=key, reverse=descending):
            cls.print_message_help(msg, indent=indent, bullet=bullet, show_dlc=show_dlc, show_transmitter=show_transmitter, order_by=order_by, descending=descending, signalkw=signalkw)

    @classmethod
    def print_message_help(cls, msg, indent=0, bullet="", show_dlc=True, show_transmitter=False, order_by=ORDER_BY_ID, descending=False, signalkw={}):
        print(cls.format_message(msg, bullet=bullet, show_dlc=show_dlc, show_transmitter=show_transmitter, order_by=order_by, descending=descending, indent=indent))
        if signalkw is None:
            return
        for sig in msg.signals:
            print(cls.format_signal(sig, indent=indent+1, **signalkw))


    @classmethod
    def format_message(cls, msg, indent=0, bullet="- ", show_dlc=True, show_transmitter=False, order_by=ORDER_BY_ID, descending=False):
        out = cls.indentation * indent
        out += bullet

        if show_dlc:
            dlc = "DLC=%s" % msg.length
        else:
            dlc = ""

        if show_transmitter and msg.senders:
            sent_by = "sent by %s" % ", ".join(msg.senders)
        else:
            sent_by = ""

        if order_by == cls.ORDER_BY_NAME:
            out += "%s (0x%03x" % (msg.name, msg.frame_id)
            if dlc:
                out += ", %s" % dlc
            if sent_by:
                out += ", %s" % sent_by
            out += ")"
        else:
            out += "0x%03x %s" % (msg.frame_id, msg.name)
            if dlc or sent_by:
                out += " ("
                if dlc:
                    out += dlc
                    if sent_by:
                        out += ", "
                if sent_by:
                    out += sent_by
                out += ")"

        return out

    @classmethod
    def format_signal(cls, sig, indent=0, bullet="- ", multiline=True,
            show_datatype=True, show_bits=False, show_min_max=True, show_receivers=False):
        if multiline:
            newline = "\n" + cls.indentation*indent + " "*len(bullet)
        else:
            newline = ". "

        # line 1
        out = cls.indentation * indent
        out += bullet
        out += "%s" % sig.name

        if sig.is_multiplexer:
            out += " [multiplexer]"

        if show_datatype:
            if sig.is_signed:
                out += ": signed"
            else:
                out += ": unsigned"
            if sig.is_float:
                out += " float"
            else:
                out += " int"

        if sig.scale != 1 or sig.unit:
            out += " in "
            if sig.scale != 1:
                out += "%s" % sig.scale
            if sig.unit:
                out += "%s" % sig.unit
        if sig.offset:
            out += ", offset: %s" % sig.offset

        # next line
        if sig.choices:
            out += newline
            out += "possible values: %s" % ", ".join("%s=%s" % (val,text) for val,text in sig.choices.items())

        # next line
        if show_min_max and (sig.minimum or sig.maximum):
            out += newline
            out += "min: %s, max: %s" % (sig.minimum, sig.maximum)

        # next line
        if show_bits:
            out += newline
            out += "start bit: %s, %s bit(s) long, %s" % (sig.start, sig.length, sig.byte_order)

        # next line
        if sig.multiplexer_ids:
            if len(sig.multiplexer_ids) == 1:
                muxed = "%s == %s" % (sig.multiplexer_signal, sig.multiplexer_ids[0])
            else:
                muxed = "%s is one of %s" % (sig.multiplexer_signal, ", ".join("%s"%mid for mid in sig.multiplexer_ids))
            out += newline
            out += "if %s" % muxed

        # next line
        if sig.comment:
            out += newline
            out += "%s" % sig.comment

        # next line
        if show_receivers and sig.receivers:
            out += newline
            out += "received by %s" % ", ".join(sig.receivers)

        return out


# ========== command line arguments ==========

def add_subparser(subparsers):
    monitor_parser = subparsers.add_parser(
        'cli',
        description='Send CAN bus messages via a command line interface.')
    monitor_parser.add_argument(
        '--prompt',
        default='>>> ',
        help='The string to show when waiting for input.')
    monitor_parser.add_argument(
        '-e', '--encoding',
        help='File encoding.')
    monitor_parser.add_argument(
        '--no-strict',
        action='store_true',
        help='Skip database consistency checks.')
    monitor_parser.add_argument(
        '-m', '--frame-id-mask',
        type=Integer(0),
        help=('Only compare selected frame id bits to find the message in the '
              'database. By default the received and database frame ids must '
              'be equal for a match.'))
    monitor_parser.add_argument(
        '-b', '--bus-type',
        default='socketcan',
        help='Python CAN bus type (default: socketcan).')
    monitor_parser.add_argument(
        '-c', '--channel',
        default='vcan0',
        help='Python CAN bus channel (default: vcan0).')
    monitor_parser.add_argument(
        '-B', '--bit-rate',
        help='Python CAN bus bit rate.')
    monitor_parser.add_argument(
        '-f', '--fd',
        action='store_true',
        help='Python CAN CAN-FD bus.')
    monitor_parser.add_argument(
        'database',
        help='Database file.')
    monitor_parser.set_defaults(func=Cli)
