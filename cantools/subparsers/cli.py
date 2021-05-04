import os
import re
import datetime
import argparse
import readline
import atexit

import can
import cantools
from argparse_addons import Integer
from .. import database
from . import utils


class QuitException(Exception):
    pass

class CanBusListener(can.Listener):

    def __init__(self, on_receive, on_error=None):
        self.on_message_received = on_receive
        if on_error:
            self.on_error = on_error

def join_iter(*iterables):
    for i in iterables:
        yield from i

class Cli:

    list_item_choice  = "- {val}: {key}"
    reo_hex = re.compile("[0-9a-f]+", re.I)
    # separator between signal name and value
    sep = "="

    ARG_NODE_ID = "node_id"

    START = "^"
    END = "$"


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
        self.notifier = can.Notifier(bus, [CanBusListener(self.on_receive, self.on_error)])
        atexit.register(self.notifier.stop)


    # ------- main -------

    def run(self):
        while True:
            try:
                ln = self.read_line()
                ln = ln.strip()
                if not ln:
                    continue
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
        canid = msg.frame_id
        if nodes.multiple_nodes:
            node_id = data.pop(self.ARG_NODE_ID, None)
            canid = nodes.create_can_id(canid, node_id)
        is_remote_frame = not data
        if data:
            self.fill_data(msg, data)
            try:
                data = msg.encode(data)
            except cantools.database.errors.EncodeError as e:
                raise ParseError(e)
        else:
            data = []
        canmsg = can.Message(
            arbitration_id = canid,
            is_extended_id = msg.is_extended_frame,
            is_remote_frame = is_remote_frame,
            data = data,
        )
        out = output.format_tx_message(canmsg)
        if out:
            print(out)
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


            try:
                val = self.parse_number(strval)
            except ParseError as e:
                if sig.choices:
                    choice = self.find_value_by_name(msg, sig, strval)
                    val = choice[0]
                else:
                    raise e

            data[sig.name] = val

        return data

    def find_value_by_name(self, msg, sig, val):
        possibilities = list(self.find_in_list(val, sig.choices.items(), key=lambda x: x[1]))
        n = len(possibilities)
        if n <= 0:
            raise ParseError('unknown value {val!r} for {msg.name}.{sig.name}'.format(msg=msg, sig=sig, val=val))
        elif n > 1:
            error = 'value {val!r} for {msg.name}.{sig.name} is ambiguous:\n'.format(msg=msg, sig=sig, val=val)
            error += '\n'.join(help_.format_choice(choice) for choice in sorted(possibilities, key=lambda c: c[1]))
            raise ParseError(error)
        return possibilities[0]

    def fill_data(self, msg, data, default=0):
        for sig in msg.signals:
            if sig.name not in data:
                if sig.minimum is not None and default < sig.minimum:
                    data[sig.name] = sig.minimum
                else:
                    data[sig.name] = default

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
        yield from self.find_in_list(cmd_input, self.dbc.messages)

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
        signals = msg.signals
        if nodes.multiple_nodes:
            signals = join_iter(signals, [FakeSignal(self.ARG_NODE_ID)])
        yield from self.find_in_list(name, signals)

    def find_in_list(self, name, l, key=lambda x: x.name):
        if name.startswith(self.START):
            name = name[len(self.START):]
            re_prefix = ''
        else:
            re_prefix = '.*'
        if name.endswith(self.END):
            name = name[:-len(self.END)]
            re_suffix = '$'
        else:
            re_suffix = ''
        re_name = re.escape(name)
        reo = re.compile(re_prefix+re_name+re_suffix, re.I)

        for item in l:
            if reo.match(key(item)):
                yield item


    # ------- CAN bus events -------

    def on_receive(self, msg):
        out = output.format_rx_message(msg)
        if out:
            print('\r', end='')
            print(out)
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

class FakeSignal:

    def __init__(self, name):
        self.name = name
        self.choices = None

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

class log(Command):

    #TODO: log sent messages
    #TODO: stop logging

    DEFAULT_LOG_PATTERN = "%Y-%m-%d_%H-%M"

    @classmethod
    def init_parser(cls, parser):
        super().init_parser(parser)
        parser.add_argument('file', nargs='?')
        parser.add_argument('--blf', action='store_true', help='log CAN messages in CANalyzer readable BLF format')
        parser.add_argument('--asc', action='store_true', help='log CAN messages in CANalyzer readable ASC format')
        parser.add_argument('-p', '--prefix', default='', help='a prefix to prepend to the file name')
        parser.add_argument('-s', '--suffix', default='', help='a suffix to append to the file name (before the extension)')

    def execute(self, args):
        cls = type(self)

        if not args.blf and not args.asc:
            args.blf = True
            args.asc = True

        if args.blf:
            cls.append_log_listener(can.BLFWriter, args, 'blf')
        if args.asc:
            cls.append_log_listener(can.ASCWriter, args, 'asc')

    @classmethod
    def append_log_listener(cls, writer_type, args, ext):
        fn = args.file
        if not fn:
            fn = cls.get_default_log_name()
        elif os.path.isdir(fn):
            fn = os.path.join(fn, cls.get_default_log_name())
        if args.prefix:
            fn = args.prefix.join(os.path.split(fn))
        if args.suffix:
            fn += args.suffix
        fn += os.path.extsep + ext

        l = writer_type(fn)
        cls.cli.notifier.add_listener(l)

    @classmethod
    def get_default_log_name(cls):
        return datetime.datetime.now().strftime(cls.DEFAULT_LOG_PATTERN)


class nodes(Command):

    '''
    If you have CAN devices with a (configurable) node id
    where the node id is encoded in the last n bits of the frame id (LSB side)
    and you are too lazy to specify every message for each possible node id in the dbc file
    this command can help you.

    Example: you have one or more devices/nodes sending the following messages
    (where X represents an arbitrary hex digit):
    - 0x01X = Message1 (0x010 = Message1 sent from node 0, 0x011 = Message1 sent from node 1, ...)
    - 0x02X = Message2 (0x020 = Message2 sent from node 0, 0x021 = Message2 sent from node 1, ...)
    but you have only specified
    - 0x010 = Message1
    - 0x020 = Message2
    in your dbc file.

    After calling this command you can pass a node_id to every message you send.
    This node_id will be added to the message id before sending the message.
    When receiving messages the frame id of a received message will first be split
    into message id and node id. Only the message id will be used to look up the
    message in the dbc file. The node id will be printed in the data of the message.

    For splitting up the frame id properly the number of possible node ids is needed.
    You can pass that as an argument to this command. Don't forget to count node 0.
    (In the above example with nodes from 0x0 to 0xF this number is 0x10.)
    If you don't specify this number the CAN message with the next smaller frame id
    from the dbc file is assumed. This can be wrong if you receive undocumented messages.

    You can disable this feature again by setting the number of possible node ids to 0.
    '''

    multiple_nodes = False
    number_node_ids = None

    @classmethod
    def init_parser(cls, parser):
        super().init_parser(parser)
        parser.add_argument('num', type=Integer(0, None), nargs='?', help='number of possible node ids')

    def execute(self, args):
        cls = type(self)
        cls.number_node_ids = args.num
        if cls.number_node_ids == 0:
            cls.multiple_nodes = False
        else:
            cls.multiple_nodes = True

    @classmethod
    def create_can_id(cls, msg_id, node_id):
        if node_id is None:
            node_id = 0

        if node_id < 0:
            raise ParseError('{cls.cli.ARG_NODE_ID} cannot be negative'.format(cls=cls))
        elif cls.number_node_ids is not None and node_id >= cls.number_node_ids:
            raise ParseError('{cls.cli.ARG_NODE_ID} must be smaller than {cls.number_node_ids}'.format(cls=cls))

        return msg_id + node_id

    @classmethod
    def split_can_id(cls, msg_id):
        if not cls.multiple_nodes:
            node_id = None
        elif cls.number_node_ids is None:
            known_message_ids = [msg.frame_id for msg in cls.cli.dbc.messages]
            node_id = 0
            while msg_id not in known_message_ids:
                msg_id -= 1
                node_id += 1
        else:
            node_id = msg_id % cls.number_node_ids
            msg_id = msg_id - node_id

        return (msg_id, node_id)


class output(Command):

    aliases = ['out']

    rx_raw = False
    rx_pretty = True
    rx_single_line = True
    rx_decode_choices = False

    tx_raw = False
    tx_pretty = True
    tx_single_line = True
    tx_decode_choices = False

    rx_prefix = "[rx] "
    tx_prefix = "[tx] "

    @classmethod
    def init_parser(cls, parser):
        super().init_parser(parser)
        arggroup = parser.add_argument_group('direction')
        arggroup.add_argument('--tx', action='store_true', help='apply the format options to transmitted messages')
        arggroup.add_argument('--rx', action='store_true', help='apply the format options to received messages')

        arggroup = parser.add_argument_group('format')
        arggroup.add_argument('-r', '--raw', action='store_true', help='stringify the python-can message object')
        arggroup.add_argument('-p', '--pretty', action='store_true', help='format message with cantools')
        arggroup.add_argument('-n', '--none', action='store_true', help='no output')

        arggroup = parser.add_argument_group('pretty options')
        arggroup.add_argument('-m', '--multiline', action='store_true')
        arggroup.add_argument('-c', '--no-choice-names', action='store_true')

    def execute(self, args):
        if not args.tx and not args.rx:
            args.tx = True
            args.rx = True
        if args.multiline:
            args.pretty = True
        if args.no_choice_names:
            args.pretty = True
        if not args.raw and not args.pretty and not args.none:
            args.pretty = True

        cls = type(self)
        if args.tx:
            cls.tx_raw = args.raw
            cls.tx_pretty = args.pretty
            cls.tx_single_line = not args.multiline
            cls.tx_decode_choices = not args.no_choice_names
        if args.rx:
            cls.rx_raw = args.raw
            cls.rx_pretty = args.pretty
            cls.rx_single_line = not args.multiline
            cls.rx_decode_choices = not args.no_choice_names

    @classmethod
    def format_tx_message(cls, canmsg):
        out = []
        if cls.tx_raw:
            out.append(str(canmsg))
        if cls.tx_pretty:
            out.extend(cls.format_pretty_message(canmsg, cls.tx_decode_choices, cls.tx_single_line).splitlines())

        if out:
            indent = " " * len(cls.tx_prefix)
            sep = "\n" + indent
            return cls.tx_prefix + sep.join(out)

        return ""

    @classmethod
    def format_rx_message(cls, canmsg):
        out = []
        if cls.rx_raw:
            out.append(str(canmsg))
        if cls.rx_pretty:
            out.extend(cls.format_pretty_message(canmsg, cls.rx_decode_choices, cls.rx_single_line).splitlines())

        if out:
            indent = " " * len(cls.rx_prefix)
            sep = "\n" + indent
            return cls.rx_prefix + sep.join(out)

        return ""

    @classmethod
    def format_pretty_message(cls, canmsg, decode_choices, single_line):
        msgid, node_id = nodes.split_can_id(canmsg.arbitration_id)
        try:
            msg = cls.cli.dbc.get_message_by_frame_id(msgid)
        except KeyError:
            return 'unknown message: {canmsg}'.format(canmsg=cls.format_message_dump(canmsg))

        if canmsg.is_remote_frame:
            if node_id is not None:
                return '{msg.name}(node_id: {node_id}) #remote request'.format(msg=msg, node_id=node_id)
            else:
                return '{msg.name} #remote request'.format(msg=msg)

        try:
            decoded_signals = msg.decode(canmsg.data, decode_choices)
        except Exception as e:
            return 'failed to decode data for {msg.name} ({canmsg}): {error}'.format(canmsg=cls.format_message_dump(canmsg), msg=msg, error=e)

        formatted_signals = utils._format_signals(msg, decoded_signals)
        if node_id is not None:
            formatted_signals.insert(0, "node_id: %s" % node_id)

        if single_line:
            out = utils._format_message_single_line(msg, formatted_signals)
        else:
            out = utils._format_message_multi_line(msg, formatted_signals)

        return out.lstrip()

    @classmethod
    def format_message_dump(cls, canmsg):
        out = "0x{canmsg.arbitration_id:03X} [{canmsg.dlc}] ".format(canmsg=canmsg)
        if canmsg.is_remote_frame:
            out += "remote request"
        else:
            out += " ".join("%02X"%b for b in canmsg.data)
        return out


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
        parser.add_argument('-a', '--all', action='store_true', help='show all information')

        msg_order_args = parser.add_argument_group("message arguments")
        msg_order_args.add_argument('--order-by', choices=cls.ALLOWED_VALUES_ORDER_BY, help='sort messages (default: id)')
        msg_order_args.add_argument('--descending', action='store_true', help='sort messages descending')
        msg_order_args.add_argument('--transmitters', action='store_true', help='show transmitters')

        sig_args = parser.add_argument_group("signal format")
        #sig_args.add_argument('--oneline', action='store_false', dest='multiline', help='print all information regarding one signal in one line')
        sig_args.add_argument('-m', '--multiline', action='store_true', help='break the information regarding a signal across several lines')
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
            args.transmitters = True
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

        if isinstance(sig, FakeSignal):
            return out

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

        if sig.unit:
            out += " in "
            out += "%s" % sig.unit

        # next line
        if sig.choices:
            out += newline
            out += "Possible values: %s" % ", ".join("%s=%s" % (val,text) for val,text in sig.choices.items())

        # next line
        if show_min_max and (sig.minimum or sig.maximum):
            out += newline
            out += "Min: %s, max: %s" % (sig.minimum, sig.maximum)

        # next line
        if show_bits:
            out += newline
            out += "Start bit: %s, %s bit(s) long, %s" % (sig.start, sig.length, sig.byte_order)

        # next line
        if show_bits:
            out += newline
            out += "Factor: %s, offset: %s" % (sig.scale, sig.offset)

        # next line
        if sig.multiplexer_ids:
            if len(sig.multiplexer_ids) == 1:
                muxed = "%s == %s" % (sig.multiplexer_signal, sig.multiplexer_ids[0])
            else:
                muxed = "%s is one of %s" % (sig.multiplexer_signal, ", ".join("%s"%mid for mid in sig.multiplexer_ids))
            out += newline
            out += "Available if %s" % muxed

        # next line
        if sig.comment:
            out += newline
            out += "%s" % sig.comment

        # next line
        if show_receivers and sig.receivers:
            out += newline
            out += "Received by %s" % ", ".join(sig.receivers)

        return out

    @classmethod
    def format_choice(cls, choice, indent=0, bullet="- ", multiline=True):
        out = cls.indentation * indent
        out += bullet
        out += "{val[0]}: {val[1]}".format(val=choice)
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
