from os import listdir, system as os_system
from os.path import basename, realpath, exists as path_exists, isdir
from re import compile
from struct import pack
from socket import inet_ntoa, gethostbyname, gethostname
from enigma import eTimer
import netifaces as ni
from Components.config import config
from Components.Console import Console
from Components.Harddisk import harddiskmanager
from Components.PluginComponent import plugins
from Plugins.Plugin import PluginDescriptor
from boxbranding import getBoxType
from six import PY3

DEFAULT_MODULE_NAME = __name__.split(".")[-1]

class Network:
	def __init__(self):
		self.ifaces = {}
		self.onlyWoWifaces = {}
		self.configuredNetworkAdapters = []
		self.NetworkState = 0
		self.DnsState = 0
		self.nameservers = []
		self.ethtool_bin = "ethtool"
		self.Console = Console()
		self.LinkConsole = Console()
		self.restartConsole = Console()
		self.deactivateInterfaceConsole = Console()
		self.activateInterfaceConsole = Console()
		self.resetNetworkConsole = Console()
		self.DnsConsole = Console()
		self.PingConsole = Console()
		self.config_ready = None
		self.friendlyNames = {}
		self.lan_interfaces = []
		self.wlan_interfaces = []
		self.remoteRootFS = None
		self.getInterfaces()

	def onRemoteRootFS(self):
		if self.remoteRootFS is None:
			from Components.Harddisk import getProcMounts
			for parts in getProcMounts():
				if parts[1] == '/' and parts[2] == 'nfs':
					self.remoteRootFS = True
					break
			else:
				self.remoteRootFS = False
		return self.remoteRootFS

	def isBlacklisted(self, iface):
		return iface in ('lo', 'wifi0', 'wmaster0', 'sit0', 'tun0', 'tap0', 'sys0', 'p2p0', 'tunl0')

	def getInterfaces(self, callback=None):
		self.configuredInterfaces = []
		for device in self.getInstalledAdapters():
			self.getAddrInet(device, callback)

	# helper function
	def regExpMatch(self, pattern, string):
		if string is None:
			return None
		try:
			return pattern.search(string).group()
		except AttributeError:
			return None

	# helper function to convert ips from a sring to a list of ints
	def convertIP(self, ip):
		return [int(n) for n in ip.split('.')]

	def getAddrInet(self, iface, callback):
		data = {'up': False, 'dhcp': False, 'preup': False, 'predown': False}
		try:
			data['up'] = int(open('/sys/class/net/%s/flags' % iface).read().strip(), 16) & 1 == 1
			if data['up']:
				self.configuredInterfaces.append(iface)
			nit = ni.ifaddresses(iface)
			data['ip'] = self.convertIP(nit[ni.AF_INET][0]['addr']) # ipv4
			data['netmask'] = self.convertIP(nit[ni.AF_INET][0]['netmask'])
			data['bcast'] = self.convertIP(nit[ni.AF_INET][0]['broadcast'])
			data['mac'] = nit[ni.AF_LINK][0]['addr'] # mac
			data['gateway'] = self.convertIP(ni.gateways()['default'][ni.AF_INET][0]) # default gw
		except:
			data['dhcp'] = True
			data['ip'] = [0, 0, 0, 0]
			data['netmask'] = [0, 0, 0, 0]
			data['gateway'] = [0, 0, 0, 0]
		self.ifaces[iface] = data
		self.loadNetworkConfig(iface, callback)

	def routeFinished(self, result, retval, extra_args):
		(iface, data, callback) = extra_args
		ipRegexp = '[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}'
		ipPattern = compile(ipRegexp)
		ipLinePattern = compile(ipRegexp)

		for line in result.splitlines():
			print(line[0:7])
			if line[0:7] == "0.0.0.0":
				gateway = self.regExpMatch(ipPattern, line[16:31])
				if gateway:
					data['gateway'] = self.convertIP(gateway)

		self.ifaces[iface] = data
		self.loadNetworkConfig(iface, callback)

	def writeNetworkConfig(self):
		self.configuredInterfaces = []
		fp = open('/etc/network/interfaces', 'w')
		fp.write("# automatically generated by enigma2\n# do NOT change manually!\n\n")
		fp.write("auto lo\n")
		fp.write("iface lo inet loopback\n\n")
		print("[%s] writeNetworkConfig onlyWoWifaces = %s" % (DEFAULT_MODULE_NAME , str(self.onlyWoWifaces)))
		for ifacename, iface in list(self.ifaces.items()):
			print("[%s] writeNetworkConfig %s = %s" % (DEFAULT_MODULE_NAME, ifacename, str(iface)))
			if 'dns-nameservers' in iface and iface['dns-nameservers']:
				dns = []
				for s in iface['dns-nameservers'].split()[1:]:
					dns.append((self.convertIP(s)))
				if dns:
					self.nameservers = dns
			WoW = False
			if ifacename in self.onlyWoWifaces:
				WoW = self.onlyWoWifaces[ifacename]
			if WoW == False and iface['up'] == True:
				fp.write("auto %s\n" % ifacename)
				self.configuredInterfaces.append(ifacename)
				self.onlyWoWifaces[ifacename] = False
			elif WoW == True:
				self.onlyWoWifaces[ifacename] = True
				fp.write("#only WakeOnWiFi %s\n" % ifacename)
			if iface['dhcp']:
				fp.write("iface %s inet dhcp\n" % ifacename)
			if not iface['dhcp']:
				fp.write("iface %s inet static\n" % ifacename)
				fp.write("  hostname $(hostname)\n")
				if 'ip' in iface:
# 					print tuple(iface['ip'])
					fp.write("	address %d.%d.%d.%d\n" % tuple(iface['ip']))
					fp.write("	netmask %d.%d.%d.%d\n" % tuple(iface['netmask']))
					if 'gateway' in iface:
						fp.write("	gateway %d.%d.%d.%d\n" % tuple(iface['gateway']))
			if "configStrings" in iface:
				fp.write(iface["configStrings"])
			if iface["preup"] is not False and "configStrings" not in iface:
				fp.write(iface["preup"])
			if iface["predown"] is not False and "configStrings" not in iface:
				fp.write(iface["predown"])
			fp.write("\n")
		fp.close()
		self.configuredNetworkAdapters = self.configuredInterfaces
		self.writeNameserverConfig()

	def writeNameserverConfig(self):
		try:
			Console().ePopen('rm -f /etc/resolv.conf')
			fp = open('/etc/resolv.conf', 'w')
			for nameserver in self.nameservers:
				fp.write("nameserver %d.%d.%d.%d\n" % tuple(nameserver))
			fp.close()
			if config.usage.dns.value.lower() not in ("dhcp-router"):
				Console().ePopen('rm -f /etc/enigma2/nameserversdns.conf')
				fp = open('/etc/enigma2/nameserversdns.conf', 'w')
				for nameserver in self.nameservers:
					fp.write("nameserver %d.%d.%d.%d\n" % tuple(nameserver))
				fp.close()
			#self.restartNetwork()
		except:
			print("[Network] resolv.conf or nameserversdns.conf - writing failed")

	def loadNetworkConfig(self, iface, callback=None):
		interfaces = []
		# parse the interfaces-file
		try:
			fp = open('/etc/network/interfaces', 'r')
			interfaces = fp.readlines()
			fp.close()
		except:
			print("[Network.py] interfaces - opening failed")

		ifaces = {}
		currif = ""
		for i in interfaces:
			split = i.strip().split(' ')
			if split[0] == "iface" and split[2] != "inet6":
				currif = split[1]
				ifaces[currif] = {}
				if len(split) == 4 and split[3] == "dhcp":
					ifaces[currif]["dhcp"] = True
				else:
					ifaces[currif]["dhcp"] = False
			if currif == iface: #read information only for available interfaces
				if split[0] == "address":
					ifaces[currif]["address"] = list(map(int, split[1].split('.')))
					if "ip" in self.ifaces[currif]:
						if self.ifaces[currif]["ip"] != ifaces[currif]["address"] and ifaces[currif]["dhcp"] == False:
							self.ifaces[currif]["ip"] = list(map(int, split[1].split('.')))
				if split[0] == "netmask":
					ifaces[currif]["netmask"] = map(int, split[1].split('.'))
					if "netmask" in self.ifaces[currif]:
						if self.ifaces[currif]["netmask"] != ifaces[currif]["netmask"] and ifaces[currif]["dhcp"] == False:
							self.ifaces[currif]["netmask"] = list(map(int, split[1].split('.')))
				if split[0] == "gateway":
					ifaces[currif]["gateway"] = map(int, split[1].split('.'))
					if "gateway" in self.ifaces[currif]:
						if self.ifaces[currif]["gateway"] != ifaces[currif]["gateway"] and ifaces[currif]["dhcp"] == False:
							self.ifaces[currif]["gateway"] = list(map(int, split[1].split('.')))
				if split[0] == "pre-up":
					if "preup" in self.ifaces[currif]:
						self.ifaces[currif]["preup"] = i
				if split[0] in ("pre-down", "post-down"):
					if "predown" in self.ifaces[currif]:
						self.ifaces[currif]["predown"] = i

		for ifacename, iface in list(ifaces.items()):
			if ifacename in self.ifaces:
				self.ifaces[ifacename]["dhcp"] = iface["dhcp"]
		if self.Console:
			if len(self.Console.appContainers) == 0:
				# save configured interfacelist
				self.configuredNetworkAdapters = self.configuredInterfaces
				# load ns only once
				self.loadNameserverConfig()
				if config.usage.dns.value.lower() not in ("dhcp-router"):
					self.writeNameserverConfig()
#				print "read configured interface:", ifaces
#				print "self.ifaces after loading:", self.ifaces
				self.config_ready = True
				self.msgPlugins()
				if callback is not None:
					callback(True)

	def loadNameserverConfig(self):
		ipRegexp = "[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}"
		nameserverPattern = compile("nameserver +" + ipRegexp)
		ipPattern = compile(ipRegexp)

		resolv = []
		try:
			if config.usage.dns.value.lower() in ("dhcp-router"):
				fp = open('/etc/resolv.conf', 'r')
			else:
				fp = open('/etc/enigma2/nameserversdns.conf', 'r')
			resolv = fp.readlines()
			fp.close()
			self.nameservers = []
		except:
			print("[Network] resolv.conf or nameserversdns.conf - opening failed")

		for line in resolv:
			if self.regExpMatch(nameserverPattern, line) is not None:
				ip = self.regExpMatch(ipPattern, line)
				if ip:
					self.nameservers.append(self.convertIP(ip))

#		print "nameservers:", self.nameservers

	def getInstalledAdapters(self):
		return [x for x in listdir('/sys/class/net') if not self.isBlacklisted(x)]

	def getConfiguredAdapters(self):
		return self.configuredNetworkAdapters

	def getNumberOfAdapters(self):
		return len(self.ifaces)

	def getFriendlyAdapterName(self, x):
		if x in list(self.friendlyNames.keys()):
			return self.friendlyNames.get(x, x)
		self.friendlyNames[x] = self.getFriendlyAdapterNaming(x)
		return self.friendlyNames.get(x, x) # when we have no friendly name, use adapter name

	def getFriendlyAdapterNaming(self, iface):
		name = None
		if self.isWirelessInterface(iface):
			if iface not in self.wlan_interfaces:
				name = _("WLAN connection")
				if len(self.wlan_interfaces):
					name += " " + str(len(self.wlan_interfaces) + 1)
				self.wlan_interfaces.append(iface)
		else:
			if iface not in self.lan_interfaces:
				if getBoxType() == "et10000" and iface == "eth1":
					name = _("VLAN connection")
				else:
					name = _("LAN connection")
				if len(self.lan_interfaces) and not getBoxType() == "et10000" and not iface == "eth1":
					name += " " + str(len(self.lan_interfaces) + 1)
				self.lan_interfaces.append(iface)
		return name

	def getFriendlyAdapterDescription(self, iface):
		if not self.isWirelessInterface(iface):
			return _("Ethernet network interface")

		moduledir = self.getWlanModuleDir(iface)
		if moduledir:
			name = basename(realpath(moduledir))
			if name in ('ath_pci', 'ath5k', 'ar6k_wlan'):
				name = 'Atheros'
			elif name in ('rt73', 'rt73usb', 'rt3070sta'):
				name = 'Ralink'
			elif name == 'zd1211b':
				name = 'Zydas'
			elif name == 'r871x_usb_drv':
				name = 'Realtek'
			elif name == 'brcm-systemport':
				name = 'Broadcom'
			elif name == 'wlan':
				name = name.upper()
		else:
			name = _("Unknown")

		return name + " " + _("wireless network interface")

	def getAdapterName(self, iface):
		return iface

	def getAdapterList(self):
		return list(self.ifaces.keys())

	def getAdapterAttribute(self, iface, attribute):
		if iface in self.ifaces:
			if attribute in self.ifaces[iface]:
				return self.ifaces[iface][attribute]
		return None

	def setAdapterAttribute(self, iface, attribute, value):
# 		print "setting for adapter", iface, "attribute", attribute, " to value", value
		if iface in self.ifaces:
			self.ifaces[iface][attribute] = value

	def removeAdapterAttribute(self, iface, attribute):
		if iface in self.ifaces:
			if attribute in self.ifaces[iface]:
				del self.ifaces[iface][attribute]

	def getNameserverList(self):
		if len(self.nameservers) == 0:
			return [[0, 0, 0, 0], [0, 0, 0, 0]]
		else:
			return self.nameservers

	def clearNameservers(self):
		self.nameservers = []

	def addNameserver(self, nameserver):
		if nameserver not in self.nameservers:
			self.nameservers.append(nameserver)

	def removeNameserver(self, nameserver):
		if nameserver in self.nameservers:
			self.nameservers.remove(nameserver)

	def changeNameserver(self, oldnameserver, newnameserver):
		if oldnameserver in self.nameservers:
			for i in list(range(len(self.nameservers))):
				if self.nameservers[i] == oldnameserver:
					self.nameservers[i] = newnameserver

	def resetNetworkConfig(self, mode='lan', callback=None):
		self.resetNetworkConsole = Console()
		self.commands = []
		self.commands.append("/etc/init.d/avahi-daemon stop")
		for iface in list(self.ifaces.keys()):
			if iface != 'eth0' or not self.onRemoteRootFS():
				self.commands.append("ip addr flush dev %s scope global" % iface)
		self.commands.append("/etc/init.d/networking stop")
		self.commands.append("killall -9 udhcpc")
		self.commands.append("rm /var/run/udhcpc*")
		self.resetNetworkConsole.eBatch(self.commands, self.resetNetworkFinishedCB, [mode, callback], debug=True)

	def resetNetworkFinishedCB(self, extra_args):
		(mode, callback) = extra_args
		if len(self.resetNetworkConsole.appContainers) == 0:
			self.writeDefaultNetworkConfig(mode, callback)

	def writeDefaultNetworkConfig(self, mode='lan', callback=None):
		fp = open('/etc/network/interfaces', 'w')
		fp.write("# automatically generated by enigma2\n# do NOT change manually!\n\n")
		fp.write("auto lo\n")
		fp.write("iface lo inet loopback\n\n")
		if mode == 'wlan':
			fp.write("auto wlan0\n")
			fp.write("iface wlan0 inet dhcp\n")
		if mode == 'wlan-mpci':
			fp.write("auto ath0\n")
			fp.write("iface ath0 inet dhcp\n")
		if mode == 'lan':
			fp.write("auto eth0\n")
			fp.write("iface eth0 inet dhcp\n")
		fp.write("\n")
		fp.close()

		self.resetNetworkConsole = Console()
		self.commands = []
		if mode == 'wlan':
			self.commands.append("ifconfig eth0 down")
			self.commands.append("ifconfig ath0 down")
			self.commands.append("ifconfig wlan0 up")
		if mode == 'wlan-mpci':
			self.commands.append("ifconfig eth0 down")
			self.commands.append("ifconfig wlan0 down")
			self.commands.append("ifconfig ath0 up")
		if mode == 'lan':
			self.commands.append("ifconfig eth0 up")
			self.commands.append("ifconfig wlan0 down")
			self.commands.append("ifconfig ath0 down")
		self.commands.append("/etc/init.d/avahi-daemon start")
		self.resetNetworkConsole.eBatch(self.commands, self.resetNetworkFinished, [mode, callback], debug=True)

	def resetNetworkFinished(self, extra_args):
		(mode, callback) = extra_args
		if len(self.resetNetworkConsole.appContainers) == 0:
			if callback is not None:
				callback(True, mode)

	def checkNetworkState(self, statecallback):
		self.NetworkState = 0
		cmd1 = "ping -c 1 www.openpli.org"
		cmd2 = "ping -c 1 www.google.nl"
		cmd3 = "ping -c 1 www.google.com"
		self.PingConsole = Console()
		self.PingConsole.ePopen(cmd1, self.checkNetworkStateFinished, statecallback)
		self.PingConsole.ePopen(cmd2, self.checkNetworkStateFinished, statecallback)
		self.PingConsole.ePopen(cmd3, self.checkNetworkStateFinished, statecallback)

	def checkNetworkStateFinished(self, result, retval, extra_args):
		(statecallback) = extra_args
		if self.PingConsole is not None:
			if retval == 0:
				self.PingConsole = None
				statecallback(self.NetworkState)
			else:
				self.NetworkState += 1
				if len(self.PingConsole.appContainers) == 0:
					statecallback(self.NetworkState)

	def restartNetwork(self, callback=None):
		self.restartConsole = Console()
		self.config_ready = False
		self.msgPlugins()
		self.commands = []
		self.commands.append("/etc/init.d/avahi-daemon stop")
		for iface in list(self.ifaces.keys()):
			if iface != 'eth0' or not self.onRemoteRootFS():
				self.commands.append("ifdown %s" % iface)
				self.commands.append("ip addr flush dev %s scope global" % iface)
		self.commands.append("/etc/init.d/networking stop")
		self.commands.append("killall -9 udhcpc")
		self.commands.append("rm /var/run/udhcpc*")
		self.commands.append("/etc/init.d/networking start")
		self.commands.append("/etc/init.d/avahi-daemon start")
		self.restartConsole.eBatch(self.commands, self.restartNetworkFinished, callback, debug=True)

	def restartNetworkFinished(self, extra_args):
		(callback) = extra_args
		if callback is not None:
			try:
				callback(True)
			except:
				pass

	def getLinkState(self, iface, callback):
		cmd = "%s %s" % (self.ethtool_bin, iface)
		self.LinkConsole = Console()
		self.LinkConsole.ePopen(cmd, self.getLinkStateFinished, callback)

	def getLinkStateFinished(self, result, retval, extra_args):
		(callback) = extra_args
		if PY3 and isinstance(result, bytes):
			result = result.decode()

		if self.LinkConsole is not None:
			if len(self.LinkConsole.appContainers) == 0:
				callback(result)

	def stopPingConsole(self):
		if self.PingConsole is not None:
			if len(self.PingConsole.appContainers):
				for name in list(self.PingConsole.appContainers.keys()):
					self.PingConsole.kill(name)

	def stopLinkStateConsole(self):
		if self.LinkConsole is not None:
			if len(self.LinkConsole.appContainers):
				for name in list(self.LinkConsole.appContainers.keys()):
					self.LinkConsole.kill(name)

	def stopDNSConsole(self):
		if self.DnsConsole is not None:
			if len(self.DnsConsole.appContainers):
				for name in list(self.DnsConsole.appContainers.keys()):
					self.DnsConsole.kill(name)

	def stopRestartConsole(self):
		if self.restartConsole is not None:
			if len(self.restartConsole.appContainers):
				for name in list(self.restartConsole.appContainers.keys()):
					self.restartConsole.kill(name)

	def stopGetInterfacesConsole(self):
		if self.Console is not None:
			if len(self.Console.appContainers):
				for name in list(self.Console.appContainers.keys()):
					self.Console.kill(name)

	def stopDeactivateInterfaceConsole(self):
		if self.deactivateInterfaceConsole is not None:
			self.deactivateInterfaceConsole.killAll()
			self.deactivateInterfaceConsole = None

	def stopActivateInterfaceConsole(self):
		if self.activateInterfaceConsole is not None:
			self.activateInterfaceConsole.killAll()
			self.activateInterfaceConsole = None

	def checkforInterface(self, iface):
		if self.getAdapterAttribute(iface, 'up') is True:
			return True
		else:
			ret = os_system("ifconfig %s up" % iface)
			os_system("ifconfig %s down" % iface)
			if ret == 0:
				return True
			else:
				return False

	def checkDNSLookup(self, statecallback):
		cmd1 = "nslookup www.cloudflare.com"
		cmd2 = "nslookup www.google.com"
		cmd3 = "nslookup www.microsoft.com"
		self.DnsConsole = Console()
		self.DnsConsole.ePopen(cmd1, self.checkDNSLookupFinished, statecallback)
		self.DnsConsole.ePopen(cmd2, self.checkDNSLookupFinished, statecallback)
		self.DnsConsole.ePopen(cmd3, self.checkDNSLookupFinished, statecallback)

	def checkDNSLookupFinished(self, result, retval, extra_args):
		(statecallback) = extra_args
		if self.DnsConsole is not None:
			if retval == 0:
				self.DnsConsole = None
				statecallback(self.DnsState)
			else:
				self.DnsState += 1
				if len(self.DnsConsole.appContainers) == 0:
					statecallback(self.DnsState)

	def deactivateInterface(self, ifaces, callback=None):
		self.config_ready = False
		self.msgPlugins()
		commands = []

		def buildCommands(iface):
			commands.append("ifdown %s" % iface)
			commands.append("ip addr flush dev %s scope global" % iface)
			#wpa_supplicant sometimes doesn't quit properly on SIGTERM
			if path_exists('/var/run/wpa_supplicant/%s' % iface):
				commands.append("wpa_cli -i%s terminate" % iface)

		if not self.deactivateInterfaceConsole:
			self.deactivateInterfaceConsole = Console()

		if isinstance(ifaces, (list, tuple)):
			for iface in ifaces:
				if iface != 'eth0' or not self.onRemoteRootFS():
					buildCommands(iface)
		else:
			if ifaces == 'eth0' and self.onRemoteRootFS():
				if callback is not None:
					callback(True)
				return
			buildCommands(ifaces)
		self.deactivateInterfaceConsole.eBatch(commands, self.deactivateInterfaceFinished, [ifaces, callback], debug=True)

	def deactivateInterfaceFinished(self, extra_args):
		(ifaces, callback) = extra_args

		def checkCommandResult(iface):
			if self.deactivateInterfaceConsole and "ifdown %s" % iface in self.deactivateInterfaceConsole.appResults:
				result = str(self.deactivateInterfaceConsole.appResults.get("ifdown %s" % iface)).strip("\n")
				if result == "ifdown: interface %s not configured" % iface:
					return False
				else:
					return True
		#ifdown sometimes can't get the interface down.
		if isinstance(ifaces, (list, tuple)):
			for iface in ifaces:
				if checkCommandResult(iface) is False:
					Console().ePopen(("ifconfig %s down" % iface))
		else:
			if checkCommandResult(ifaces) is False:
				Console().ePopen(("ifconfig %s down" % ifaces))

		if self.deactivateInterfaceConsole:
			if len(self.deactivateInterfaceConsole.appContainers) == 0:
				if callback is not None:
					callback(True)

	def activateInterface(self, iface, callback=None):
		if self.config_ready:
			self.config_ready = False
			self.msgPlugins()
		if iface == 'eth0' and self.onRemoteRootFS():
			if callback is not None:
				callback(True)
			return
		if not self.activateInterfaceConsole:
			self.activateInterfaceConsole = Console()
		commands = ["ifup %s" % iface]
		self.activateInterfaceConsole.eBatch(commands, self.activateInterfaceFinished, callback, debug=True)

	def activateInterfaceFinished(self, extra_args):
		callback = extra_args
		if self.activateInterfaceConsole:
			if len(self.activateInterfaceConsole.appContainers) == 0:
				if callback is not None:
					try:
						callback(True)
					except:
						pass

	def sysfsPath(self, iface):
		return '/sys/class/net/%s' % iface

	def isWirelessInterface(self, iface):
		if iface in self.wlan_interfaces:
			return True

		if isdir("%s/wireless" % self.sysfsPath(iface)):
			return True

		# r871x_usb_drv on kernel 2.6.12 is not identifiable over /sys/class/net/'ifacename'/wireless so look also inside /proc/net/wireless
		device = compile('[a-z]{2,}[0-9]*:')
		ifnames = []
		fp = open('/proc/net/wireless', 'r')
		for line in fp:
			try:
				ifnames.append(device.search(line).group()[:-1])
			except AttributeError:
				pass
		fp.close()
		if iface in ifnames:
			return True

		return False

	def canWakeOnWiFi(self, iface):
		if self.sysfsPath(iface) == "/sys/class/net/wlan3" and path_exists("/tmp/bcm/%s" % iface):
			return True

	def getWlanModuleDir(self, iface=None):
		if self.sysfsPath(iface) == "/sys/class/net/wlan3" and path_exists("/tmp/bcm/%s" % iface):
			devicedir = "%s/device" % self.sysfsPath("sys0")
		else:
			devicedir = "%s/device" % self.sysfsPath(iface)
		moduledir = "%s/driver/module" % devicedir
		if isdir(moduledir):
			return moduledir

		# identification is not possible over default moduledir
		try:
			for x in listdir(devicedir):
				# rt3070 on kernel 2.6.18 registers wireless devices as usb_device (e.g. 1-1.3:1.0) and identification is only possible over /sys/class/net/'ifacename'/device/1-xxx
				if x.startswith("1-"):
					moduledir = "%s/%s/driver/module" % (devicedir, x)
					if isdir(moduledir):
						return moduledir
			# rt73, zd1211b, r871x_usb_drv on kernel 2.6.12 can be identified over /sys/class/net/'ifacename'/device/driver, so look also here
			moduledir = "%s/driver" % devicedir
			if isdir(moduledir):
				return moduledir
		except:
			pass
		return None

	def detectWlanModule(self, iface=None):
		if not self.isWirelessInterface(iface):
			return None

		devicedir = "%s/device" % self.sysfsPath(iface)
		if isdir("%s/ieee80211" % devicedir):
			return 'nl80211'

		moduledir = self.getWlanModuleDir(iface)
		if moduledir:
			module = basename(realpath(moduledir))
			if module in ('brcm-systemport',):
				return 'brcm-wl'
			if module in ('ath_pci', 'ath5k'):
				return 'madwifi'
			if module in ('rt73', 'rt73'):
				return 'ralink'
			if module == 'zd1211b':
				return 'zydas'
		return 'wext'

	def calc_netmask(self, nmask):
		mask = 1 << 31
		xnet = (1 << 32) - 1
		cidr_range = range(0, 32)
		cidr = int(nmask)
		if cidr not in list(cidr_range):
			print('cidr invalid: %d' % cidr)
			return None
		else:
			nm = ((1 << cidr) - 1) << (32 - cidr)
			netmask = str(inet_ntoa(pack('>L', nm)))
			return netmask

	def msgPlugins(self):
		if self.config_ready is not None:
			for p in plugins.getPlugins(PluginDescriptor.WHERE_NETWORKCONFIG_READ):
				p(reason=self.config_ready)

	def hotplug(self, event):
		interface = event['INTERFACE']
		if self.isBlacklisted(interface):
			return
		action = event['ACTION']
		if action == "add":
			print("[Network] Add new interface: %s" % interface)
			self.getAddrInet(interface, None)
		elif action == "remove":
			print("[Network] Removed interface: %s" % interface)
			try:
				del self.ifaces[interface]
			except KeyError:
				pass

	def getInterfacesNameserverList(self, iface):
		result = []
		nameservers = self.getAdapterAttribute(iface, "dns-nameservers")
		if nameservers:
			ipRegexp = '[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}'
			ipPattern = compile(ipRegexp)
			for x in nameservers.split()[1:]:
				ip = self.regExpMatch(ipPattern, x)
				if ip:
					result.append([int(n) for n in ip.split('.')])
		if len(self.nameservers) and not result: # use also global nameserver if we got no one from interface
			result.extend(self.nameservers)
		return result


iNetwork = Network()


class NetworkCheck:
	def __init__(self):
		self.Timer = eTimer()
		self.Timer.callback.append(self.startCheckNetwork)

	def startCheckNetwork(self):
		self.Timer.stop()
		if self.Retry > 0:
			try:
				if gethostbyname(gethostname()) != "127.0.0.1":
					print("[NetworkCheck] CheckNetwork - Done")
					harddiskmanager.enumerateNetworkMounts(refresh=True)
					return
				self.Retry = self.Retry - 1
				self.Timer.start(1000, True)
			except Exception as e:
				print("[NetworkCheck] CheckNetwork - Error: %s" % str(e))

	def Start(self):
		self.Retry = 10
		self.Timer.start(1000, True)


def InitNetwork():
	global networkCheck
	networkCheck = NetworkCheck()
	networkCheck.Start()
