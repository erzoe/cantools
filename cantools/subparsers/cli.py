import re
import argparse

import can
from argparse_addons import Integer
from .. import database
from .utils import format_message
from .utils import format_multiplexed_name


class QuitException(Exception):
    pass


class Cli:

    list_item_message = "- 0x{msg.frame_id:03x} {msg.name}"
    list_item_signal  = "- {sig.name} (start bit {sig.start}, {sig.length} bit long)"
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
            self.print_message_list(possible_messages)
            return

        msg = possible_messages[0]
        data = self.parse_data(msg, args)
        is_remote_frame = not data
        data = msg.encode(data)
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
            error += '\n'.join(self.list_item_signal.format(sig=sig) for sig in signals)
            raise ParseError(error)
        return signals[0]

    def find_signals_by_name(self, msg, name):
        reo = re.compile('.*'+re.escape(name)+'.*', re.I)
        for sig in msg.signals:
            if reo.match(sig.name):
                yield sig


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

    def print_message_list(self, messages):
        for msg in sorted(messages, key=lambda msg: msg.frame_id):
            print(self.list_item_message.format(msg=msg))


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
        cls.parser = NotExitingArgumentParser(prog=cls.get_name(), description=__doc__, add_help=False)
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

    name = "help"
    aliases = ["h", "?"]

    def execute(self, args):
        self.cli.print_message_list(self.cli.dbc.messages)


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
