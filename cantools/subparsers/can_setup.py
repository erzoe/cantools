# pip3 install --user python-can
# note the "python" in front of the "can"
# "can" would install a different library called libcan!
import can

# standard
import platform


BITRATE_250k = 250000
BITRATE_500k = 500000


buses = list()

def get_bus(busid=0):
	if isinstance(busid, int):
		return buses[busid]
	else:
		bus = next((bus for bus in buses if bus.channel==busid), None)

		if bus == None:
			raise IndexError("bus %s is not initialized" % busid)

		return bus


class CanBusException(Exception):

	def __init__(self, msg):
		if isinstance(msg, list):
			msg = "\n".join(msg)
		super().__init__(msg)


if platform.system() == "Linux":
	import subprocess

	def format_cmd(cmd):
		return "    $ " + " ".join(cmd)

	def run(cmd):
		print("[executing %s]" % format_cmd(cmd).strip())
		subprocess.run(cmd, check=True)

	def init(bitrate, channel=None):
		if isinstance(bitrate, str):
			if bitrate.endswith('k'):
				bitrate = bitrate[:-1] + '000'
			bitrate = int(bitrate)
		global cmd_bus_up, cmd_bus_down
		can_interface = channel
		can_interface = "can0" if can_interface is None else can_interface

		cmd_bus_down = ("sudo", "ip", "link", "set", can_interface, "down")
		cmd_bus_up   = ("sudo", "ip", "link", "set", can_interface, "up", "type", "can", "bitrate", str(bitrate))

		check_configured_bitrate(can_interface, bitrate)

		try:
			bus = can.ThreadSafeBus(can_interface, bustype="socketcan")
		except Exception as e:
			raise CanBusException(e)

		buses.append(bus)
		return bus


	def check_configured_bitrate(can_interface, bitrate):
		if can_interface[0] == 'v':
			# virtual CAN bus has no bitrate
			return

		cmd = "ip -d link show " + can_interface + " | grep 'state DOWN'"
		p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
		if p.returncode == 0:
			try:
				run(cmd_bus_up)
				return
			except:
				pass

			msg = []
			msg.append("can bus is down")
			msg.append("please try to start the bus with")
			msg.append("")
			msg.append(format_cmd(cmd_bus_up))
			msg.append("")
			raise CanBusException(msg)


		cmd = "ip -d link show " + can_interface + " | awk '/bitrate/ {print $2}'"

		# check is not that useful here because awk overrides the return code of ip
		p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

		if p.stderr:
			msg = p.stderr.decode(encoding="utf8")
			raise CanBusException(msg)

		if not p.stdout:
			try:
				run(cmd_bus_up)
				return
			except:
				pass

			msg = []
			msg.append("failed to determine the bitrate")
			msg.append("please try to start the bus with")
			msg.append("")
			msg.append(format_cmd(cmd_bus_up))
			msg.append("")
			raise CanBusException(msg)

		configured_bitrate = int(p.stdout)

		if configured_bitrate != bitrate:
			try:
				run(cmd_bus_down)
				run(cmd_bus_up)
				return
			except:
				pass

			msg = []
			msg.append("please change the bitrate from {configured} bit/s to {required} bit/s.".format(configured=configured_bitrate, required=bitrate))
			msg.append()
			msg.append(format_cmd(cmd_bus_down))
			msg.append(format_cmd(cmd_bus_up))
			msg.append()
			raise CanBusException(msg)

	def handle_exception(exc):
		raise exc
		msg = []
		msg.append(exc)
		msg.append("please try to start the bus with")
		msg.append("")
		msg.append(format_cmd(cmd_bus_up))
		msg.append("")
		msg.append("", end="", flush=True)
		raise CanBusException(msg)

else:
	def init(bitrate, channel=None):
		try:
			bus = can.ThreadSafeBus(channel, bustype="pcan", bitrate=bitrate)
		except Exception as e:
			raise CanBusException(e)

		bus.channel = ""
		buses.append(bus)
		return bus

	def handle_exception(exc):
		raise exc


def ensure_one_bus_is_initialized(bitrate, channel=None):
	if buses:
		return

	return init(bitrate, channel=channel)

def reinit(bitrate, channel=None):
	if buses:
		shutdown_if_up(busid=channel)

	return init(bitrate, channel=channel)


def shutdown_if_up(busid=None):
	"""Closes one bus or all buses.
	
	Trying to close a bus which is not open is no problem.
	
	Keyword arguments:
	busid -- An identifier for the CAN bus to listen on.
	         If None: Close all CAN buses.
	         Otherwise: Either the channel passed to init
	         or the index in which order the bus was initialized.
	
	Return value:
	None
	"""

	def shutdown(bus):
		bus.shutdown()
		if bus.channel:
			print("bus %s was shutdown" % bus.channel)
		else:
			print("bus was shutdown")

	if busid == None:
		if not buses:
			print("[no buses were initialized]")
			return

		for bus in buses:
			shutdown(bus)

		buses.clear()

	else:
		try:
			bus = get_bus(busid)
			buses.remove(bus)
			shutdown(bus)

		except IndexError:
			print("[bus %s was not initialized]" % busid)
