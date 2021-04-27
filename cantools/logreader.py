import re
import enum
import binascii
import datetime
import can


class TimestampFormat(enum.Enum):
    """Describes a type of timestamp. ABSOLUTE is referring to UNIX time
    (seconds since epoch). RELATIVE is seconds since start of log, or time
    since last frame depending of the contents of the log file. MISSING means
    that no timestamps is present in the log."""
    ABSOLUTE = 1
    RELATIVE = 2
    MISSING = 3


class DataFrame:
    """Container for a parsed log entry (ie. a CAN frame)."""

    def __init__(self, channel: str,
                 frame_id: int,
                 data: bytes,
                 timestamp: datetime.datetime,
                 timestamp_format: TimestampFormat,
                 length: int = None):
        """Constructor for DataFrame

        :param channel: A string representation of the channel, eg. 'can0'
        :param frame_id: The numeric CAN frame ID :param data: The actual data
        :param timestamp: A timestamp, datetime.datetime if absolute, or
            datetime.timedelta if relative, None if missing
        :param timestamp_format: The format of the timestamp
        :param length: The message data length in bytes
        : """
        if length is None:
            length = len(data)
        self.channel = channel
        self.frame_id = frame_id
        self.data = data
        self.length = length
        self.timestamp = timestamp
        self.timestamp_format = timestamp_format


class BasePattern:
    @classmethod
    def match(clz, line):
        mo = clz.pattern.match(line)
        if mo:
            return clz.unpack(mo)


class CandumpDefaultPattern(BasePattern):
    # vcan0  1F0   [8]  00 00 00 00 00 00 1B C1
    pattern = re.compile(
        r'^\s*?(?P<channel>[a-zA-Z0-9]+)\s+(?P<can_id>[0-9A-F]+)\s+\[\d+\]\s*(?P<can_data>[0-9A-F ]*)$')

    @staticmethod
    def unpack(match_object):
        channel = match_object.group('channel')
        frame_id = int(match_object.group('can_id'), 16)
        data = match_object.group('can_data')
        data = data.replace(' ', '')
        data = binascii.unhexlify(data)
        timestamp = None
        timestamp_format = TimestampFormat.MISSING

        return DataFrame(channel=channel, frame_id=frame_id, data=data, timestamp=timestamp, timestamp_format=timestamp_format)


class CandumpTimestampedPattern(BasePattern):
    # (000.000000)  vcan0  0C8   [8]  F0 00 00 00 00 00 00 00
    pattern = re.compile(
        r'^\s*?\((?P<timestamp>[\d.]+)\)\s+(?P<channel>[a-zA-Z0-9]+)\s+(?P<can_id>[0-9A-F]+)\s+\[\d+\]\s*(?P<can_data>[0-9A-F ]*)$')

    @staticmethod
    def unpack(match_object):
        channel = match_object.group('channel')
        frame_id = int(match_object.group('can_id'), 16)
        data = match_object.group('can_data')
        data = data.replace(' ', '')
        data = binascii.unhexlify(data)

        seconds = float(match_object.group('timestamp'))
        if seconds < 662688000:  # 1991-01-01 00:00:00, "Released in 1991, the Mercedes-Benz W140 was the first production vehicle to feature a CAN-based multiplex wiring system."
            timestamp = datetime.timedelta(seconds=seconds)
            timestamp_format = TimestampFormat.RELATIVE
        else:
            timestamp = datetime.datetime.utcfromtimestamp(seconds)
            timestamp_format = TimestampFormat.ABSOLUTE

        return DataFrame(channel=channel, frame_id=frame_id, data=data, timestamp=timestamp, timestamp_format=timestamp_format)


class CandumpDefaultLogPattern(BasePattern):
    # (1579857014.345944) can2 486#82967A6B006B07F8
    # (1613656104.501098) can2 14C##16A0FFE00606E022400000000000000A0FFFF00FFFF25000600000000000000FE
    pattern = re.compile(
        r'^\s*?\((?P<timestamp>[\d.]+)\)\s+(?P<channel>[a-zA-Z0-9]+)\s+(?P<can_id>[0-9A-F]+)#(#[0-9A-F])?(?P<can_data>[0-9A-F]*)$')

    @staticmethod
    def unpack(match_object):
        channel = match_object.group('channel')
        frame_id = int(match_object.group('can_id'), 16)
        data = match_object.group('can_data')
        data = data.replace(' ', '')
        data = binascii.unhexlify(data)
        timestamp = datetime.datetime.utcfromtimestamp(float(match_object.group('timestamp')))
        timestamp_format = TimestampFormat.ABSOLUTE

        return DataFrame(channel=channel, frame_id=frame_id, data=data, timestamp=timestamp, timestamp_format=timestamp_format)


class CandumpAbsoluteLogPattern(BasePattern):
    # (2020-12-19 12:04:45.485261)  vcan0  0C8   [8]  F0 00 00 00 00 00 00 00
    pattern = re.compile(
        r'^\s*?\((?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\)\s+(?P<channel>[a-zA-Z0-9]+)\s+(?P<can_id>[0-9A-F]+)\s+\[\d+\]\s*(?P<can_data>[0-9A-F ]*)$')

    @staticmethod
    def unpack(match_object):
        channel = match_object.group('channel')
        frame_id = int(match_object.group('can_id'), 16)
        data = match_object.group('can_data')
        data = data.replace(' ', '')
        data = binascii.unhexlify(data)
        timestamp = datetime.datetime.strptime(match_object.group('timestamp'), "%Y-%m-%d %H:%M:%S.%f")
        timestamp_format = TimestampFormat.ABSOLUTE

        return DataFrame(channel=channel, frame_id=frame_id, data=data, timestamp=timestamp, timestamp_format=timestamp_format)


class Parser:
    """A CAN log file parser.

    Automatically detects the format of the logfile by trying parser patterns
    until the first successful match.

    >>> with open('candump.log') as fd:
            for frame in cantools.logreader.Parser(fd):
                print(f'{frame.timestamp}: {frame.frame_id}')
    """

    def __init__(self, stream=None):
        self.stream = stream
        self.pattern = None

        if isinstance(stream, str):
            stream_lower = stream.lower()
            if stream_lower.endswith('.blf'):
                self.reader = can.io.blf.BLFReader(stream)
            elif stream_lower.endswith('.asc'):
                self.reader = can.io.asc.ASCReader(stream)
            elif stream_lower.endswith('.sqlite'):
                self.reader = can.io.sqlite.SqliteReader(stream)
            else:
                self.reader = can.io.log.CanutilsLogReader(stream)
            self.iterlines = self._reader_iterlines

    @staticmethod
    def detect_pattern(line):
        for p in [CandumpDefaultPattern, CandumpTimestampedPattern, CandumpDefaultLogPattern, CandumpAbsoluteLogPattern]:
            mo = p.pattern.match(line)
            if mo:
                return p

    def parse(self, line):
        if self.pattern is None:
            self.pattern = self.detect_pattern(line)
        if self.pattern is None:
            return None
        return self.pattern.match(line)

    def iterlines(self, keep_unknowns=False):
        """Returns an generator that yields (str, DataFrame) tuples with the
        raw log entry and a parsed log entry. If keep_unknowns=True, (str,
        None) tuples will be returned for log entries that couldn't be decoded.
        If keep_unknowns=False, non-parseable log entries is discarded.
        """
        if self.stream is None:
            return
        while True:
            nl = self.stream.readline()
            if nl == '':
                return
            nl = nl.strip('\r\n')
            frame = self.parse(nl)
            if frame:
                yield nl, frame
            elif keep_unknowns:
                yield nl, None
            else:
                continue

    def _reader_iterlines(self, keep_unknowns=False):
        """Returns an generator that yields (str, DataFrame) tuples with the
        raw log entry and a parsed log entry. If keep_unknowns=True, (str,
        None) tuples will be returned for log entries that couldn't be decoded.
        If keep_unknowns=False, non-parseable log entries is discarded.
        """
        try:
            for canmsg in self.reader:
                frame = self.canmsg_to_frame(canmsg)
                fake_ln = "({frame.timestamp})  {frame.channel}  {frame.frame_id:03X}   [{frame.length}]  {data}".format(
                    frame=frame, data=" ".join("%02X" % byte for byte in frame.data))
                yield fake_ln, frame
        finally:
            if hasattr(self.reader, 'close'):
                self.reader.close()

    def canmsg_to_frame(self, canmsg):
        return DataFrame(
            channel = canmsg.channel,
            frame_id = canmsg.arbitration_id,
            data = canmsg.data,
            length = canmsg.dlc,
            timestamp = datetime.datetime.fromtimestamp(canmsg.timestamp),
            timestamp_format = TimestampFormat.ABSOLUTE,
        )

    def __iter__(self):
        """Returns DataFrame log entries. Non-parseable log entries is
        discarded."""
        for _, frame in self.iterlines():
            yield frame
