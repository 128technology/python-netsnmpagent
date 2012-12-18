#
# python-netsnmpagent module
#
# Copyright (c) 2012 Pieter Hollants <pieter@hollants.com>
# Licensed under the GNU Public License (GPL) version 3
#

"""Allows to write net-snmp subagents in Python.

The Python bindings that ship with net-snmp support client operations
only. I fixed a couple of issues in the existing python-agentx module
but eventually to rewrite a new module from scratch due to design
issues. For example, it implemented its own handler for registered SNMP
variables, which requires re-doing a lot of stuff which net-snmp
actually takes care of in its API's helpers.

This module, by contrast, concentrates on wrapping the net-snmp C API
for SNMP subagents in an easy manner. It is still under heavy
development and some features are yet missing."""

import sys, os
import ctypes, ctypes.util

# include/net-snmp/library/default_store.h
NETSNMP_DS_LIBRARY_ID           = 0
NETSNMP_DS_APPLICATION_ID       = 1
NETSNMP_DS_LIB_PERSISTENT_DIR   = 8

# include/net-snmp/agent/ds_agent.h
NETSNMP_DS_AGENT_ROLE           = 1
NETSNMP_DS_AGENT_X_SOCKET       = 1

# include/net-snmp/library/oid.h
c_oid                           = ctypes.c_ulong

# include/net-snmp/types.h
MAX_OID_LEN                     = 128

# include/net-snmp/agent/agent_handler.h
HANDLER_CAN_GETANDGETNEXT       = 0x01
HANDLER_CAN_SET                 = 0x02
HANDLER_CAN_RONLY               = HANDLER_CAN_GETANDGETNEXT
HANDLER_CAN_RWRITE              = (HANDLER_CAN_GETANDGETNEXT | HANDLER_CAN_SET)

# include/net-snmp/library/asn1.h
ASN_INTEGER                     = 0x02
ASN_OCTET_STR                   = 0x04
ASN_APPLICATION                 = 0x40

# include/net-snmp/library/snmp_impl.h
ASN_IPADDRESS                   = ASN_APPLICATION | 0
ASN_COUNTER                     = ASN_APPLICATION | 1
ASN_UNSIGNED                    = ASN_APPLICATION | 2
ASN_TIMETICKS                   = ASN_APPLICATION | 3

# include/net-snmp/agent/watcher.h
WATCHER_FIXED_SIZE              = 0x01
WATCHER_SIZE_STRLEN             = 0x08

# Maximum string size supported by python-netsnmpagent
MAX_STRING_SIZE                 = 1024

class netsnmpAgent(object):
	""" Implements an SNMP agent using the net-snmp libraries. """

	def __init__(self, **args):
		"""Initializes a new netsnmpAgent instance.
		
		"args" is a dictionary that can contain the following
		optional parameters:
		
		- AgentName    : The agent's name used for registration with net-snmp.
		- MasterSocket : The Unix domain socket of the running snmpd instance to
		                 connect to. Change this if you want to use a custom
		                 snmpd instance, eg. in example.sh or for automatic
		                 testing.
		- PersistentDir: The directory to use to store persistance information.
		                 Change this if you want to use a custom snmpd instance,
		                 eg. in example.sh or for automatic testing.
		- MIBFiles     : A list of filenames of MIBs to be loaded. Required if
		                 the OIDs, for which variables will be registered, do
		                 not belong to standard MIBs and the custom MIBs are not
		                 located in net-snmp's default MIB path
		                 (/usr/share/snmp/mibs). """

		# Default settings
		defaults = {
			"AgentName"    : os.path.splitext(os.path.basename(sys.argv[0]))[0],
			"MasterSocket" : None,
			"PersistentDir": None,
			"MIBFiles"     : None
		}
		for key in defaults:
			setattr(self, key, args.get(key, defaults[key]))
		if self.MIBFiles != None and not type(self.MIBFiles) in (list, tuple):
			self.MIBFiles = (self.MIBFiles,)

		# Get access to libnetsnmpagent
		try:
			libname = ctypes.util.find_library("netsnmpagent")
			self._agentlib = ctypes.cdll.LoadLibrary(libname)
		except:
			raise netsnmpAgentException("Could not load libnetsnmpagent!")

		# FIXME: log errors to stdout for now
		self._agentlib.snmp_enable_stderrlog()

		# Make us an AgentX client
		self._agentlib.netsnmp_ds_set_boolean(
			NETSNMP_DS_APPLICATION_ID,
			NETSNMP_DS_AGENT_ROLE,
			1
		)

		# Use an alternative Unix domain socket to connect to the master?
		if self.MasterSocket:
			self._agentlib.netsnmp_ds_set_string(
				NETSNMP_DS_APPLICATION_ID,
				NETSNMP_DS_AGENT_X_SOCKET,
				self.MasterSocket
			)

		# Use an alternative persistence directory?
		if self.PersistentDir:
			self._agentlib.netsnmp_ds_set_string(
				NETSNMP_DS_LIBRARY_ID,
				NETSNMP_DS_LIB_PERSISTENT_DIR,
				ctypes.c_char_p(self.PersistentDir)
			)

		# Initialize net-snmp library (see netsnmp_agent_api(3))
		if self._agentlib.init_agent(self.AgentName) != 0:
			raise netsnmpAgentException("init_agent() failed!")

		# Initialize MIB parser
		self._agentlib.netsnmp_init_mib()

		# If MIBFiles were specified (ie. MIBs that can not be found in
		# net-snmp's default MIB directory /usr/share/snmp/mibs), read
		# them in so we can translate OID strings to net-snmp's internal OID
		# format.
		if self.MIBFiles:
			for mib in self.MIBFiles:
				if self._agentlib.read_mib(mib) == 0:
					raise netsnmpAgentException("netsnmp_read_module({0}) " +
					                            "failed!".format(mib))

		# Initialize our SNMP object registry
		self._objs    = {}
		self._started = False

	def register(self, snmpobj, oidstr, allow_set = True):
		""" Registers the supplied SNMP object at the specified OID.
		
		    "snmpobj" is a class instance representing a particular SNMP object,
		    as returned by the appropriate netsnmpAgent methods, eg. Integer32,
		    Unsigned32 or Table. The instance's "register_func" property must
		    point to the callback function used to create and register a
		    suitable net-snmp handler.
		
		    "allow_set" indicates whether "snmpset" is allowed. """

		# Make sure the agent has not been start()ed yet
		if self._started == True:
			raise netsnmpAgentException("Attempt to register SNMP object " \
			                            "after agent has been started!")

		# We can't know the length of the internal OID representation
		# beforehand, so we use a MAX_OID_LEN sized buffer for the call to
		# read_objid() below
		oid = (c_oid * MAX_OID_LEN)()
		oid_len = ctypes.c_size_t(MAX_OID_LEN)

		# Let libsnmpagent parse the OID
		result = self._agentlib.read_objid(
			oidstr,
			ctypes.byref(oid),
			ctypes.byref(oid_len)
		)
		if result == 0:
			raise netsnmpAgentException(
				"read_objid({0}) failed!".format(oidstr)
			)

		# Do we allow SNMP SETting to this OID?
		handler_modes = HANDLER_CAN_RWRITE if allow_set \
		                                   else HANDLER_CAN_RONLY

		# Create the netsnmp_handler_registration structure. It notifies
		# net-snmp that we will be responsible for anything below the given
		# OID. We use this for leaf nodes only, processing of subtress will be
		# left to net-snmp.
		handler_reginfo = self._agentlib.netsnmp_create_handler_registration(
			oidstr,         # *oidstr
			None,           # (*handler_access_method)()
			oid,            # *oid
			oid_len,        # oid_len
			handler_modes   # handler_modes
		)

		# Call the object's register_func callback function.
		snmpobj._register_func(snmpobj, oidstr, handler_reginfo)

		# Finally, we keep track of all registered SNMP objects for the
		# getRegistered() method.
		self._objs[oidstr] = snmpobj

	def _registerWatcher(self, snmpobj, oidstr, handler_reginfo):
		""" Creates and registers a watcher to handle SNMP variables. This
		    method is a callback method to register().
		
			watchers are net-snmp helper handlers that take care of
			all SNMP details such as GET, GETNEXT etc. for simple
			variables.
		
		    "snmpobj" is a class instance representing a particular SNMP object
		    (either a variable or a table), as returned by the appropriate
		    netsnmpAgent methods, eg. Integer32, Unsigned32 or Table. The
		    instance's "register_func" property must point to the callback
		    function used to create and register a suitable net-snmp handler.
		
			"handler_reginfo" is the netsnmp_handler_registration structure
			prepared by Register(). """

		# Create the netsnmp_watcher_info structure.
		watcher = self._agentlib.netsnmp_create_watcher_info6(
			snmpobj.cref(),     # *data
			snmpobj._data_size, # data_size
			snmpobj._asntype,   # asn_type
			snmpobj._flags,     # flags
			snmpobj._max_size,  # max_size
			None                # *size_p
		)

		# Register handler and watcher with net-snmp.
		result = self._agentlib.netsnmp_register_watched_instance(
			handler_reginfo,
			watcher
		)
		if result != 0:
			raise netsnmpAgentException("Error registering variable with net-snmp!")

	def VarTypeClass(property_func):
		""" Decorator that transforms a simple property_func into a class
		    factory returning instances of a class for the particular SNMP
		    variable type. property_func is supposed to return a dictionary with
		    the following elements:
		    - "ctype"           : A reference to the ctypes constructor method
		                          yielding the appropriate C representation of
		                          the SNMP variable, eg. ctypes.c_long or
		                          ctypes.create_string_buffer.
		    - "flags"           : A net-snmp constant describing the C data
		                          type's storage behavior, currently either
		                          WATCHER_FIXED_SIZE or WATCHER_SIZE_STRLEN.
		    - "max_size"        : The maximum allowed string size if "flags"
		                          has been set to WATCHER_SIZE_STRLEN.
		    - "initval"         : The value to initialize the C data type with,
		                          eg. 0 or "".
		    - "register_func"   : The callback function to use by Register()
		                          when an instance of this variable type class
		                          is passed to the Register() method.
		    - "asntype"         : A constant defining the SNMP variable type
		                          from an ASN.1 perspective, eg. ASN_INTEGER.
		
		    The class instance returned will have no association with net-snmp
		    yet. Use the Register() method to associate it with an OID. """

		# This is the replacement function, the "decoration"
		def create_vartype_class(self, initval = None):
			# Call the original property_func to retrieve this variable type's
			# properties. Passing "initval" to property_func may seem pretty
			# useless as it won't have any effect and we use it ourselves below.
			# However we must supply it nevertheless since it's part of
			# property_func's function signature which THIS function shares.
			# That's how Python's decorators work.
			props = property_func(self, initval)

			# Use variable type's default initval if we weren't given one
			if initval == None:
				initval = props["initval"]

			# Create a class to wrap ctypes' access semantics and enable
			# Register() to do class-specific registration work.
			#
			# Since the part behind the "class" keyword can't be a variable, we
			# use the proxy name "cls" and overwrite its __name__ property
			# after class creation.
			class cls(object):
				def __init__(self):
					# The class instance inherits most of props' members as
					# properties
					for prop in ["ctype", "flags", "register_func", "asntype"]:
						setattr(self, "_{0}".format(prop), props[prop])

					# Create the ctypes class instance representing the variable
					# to be handled by the net-snmp C API. If this variable type
					# has no fixed size, pass the maximum size as second
					# argument to the constructor.
					if props["flags"] == WATCHER_FIXED_SIZE:
						self._cvar      = props["ctype"](initval)
						self._data_size = ctypes.sizeof(self._cvar)
						self._max_size  = self._data_size
					else:
						self._cvar      = props["ctype"](initval, props["max_size"])
						self._data_size = len(self._cvar.value)
						self._max_size  = max(props["max_size"], len(initval))

				def value(self):
					return self._cvar.value

				def cref(self):
					if self._flags == WATCHER_FIXED_SIZE:
						return ctypes.byref(self._cvar)
					else:
						return self._cvar

				def update(self, val):
					self._cvar.value = val
					if props["flags"] == WATCHER_SIZE_STRLEN:
						if len(val) > self._max_size:
							raise netsnmpAgentException(
								"Value passed to update() truncated: {0} > {1} "
								"bytes!".format(len(val), self._max_size))
						self._cvar.value = val
						self._data_size  = len(val)

			cls.__name__ = property_func.__name__

			# Return an instance of the just-defined class to the agent
			return cls()

		return create_vartype_class

	@VarTypeClass
	def Integer32(self, initval = None):
		return {
			"ctype"         : ctypes.c_long,
			"flags"         : WATCHER_FIXED_SIZE,
			"initval"       : 0,
			"register_func" : self._registerWatcher,
			"asntype"       : ASN_INTEGER
		}

	@VarTypeClass
	def Unsigned32(self, initval = None):
		return {
			"ctype"         : ctypes.c_ulong,
			"flags"         : WATCHER_FIXED_SIZE,
			"initval"       : 0,
			"register_func" : self._registerWatcher,
			"asntype"       : ASN_UNSIGNED
		}

	@VarTypeClass
	def Counter32(self, initval = None):
		return {
			"ctype"         : ctypes.c_ulong,
			"flags"         : WATCHER_FIXED_SIZE,
			"initval"       : 0,
			"register_func" : self._registerWatcher,
			"asntype"       : ASN_COUNTER
		}

	@VarTypeClass
	def TimeTicks(self, initval = None):
		return {
			"ctype"         : ctypes.c_ulong,
			"flags"         : WATCHER_FIXED_SIZE,
			"initval"       : 0,
			"register_func" : self._registerWatcher,
			"asntype"       : ASN_TIMETICKS
		}

	@VarTypeClass
	def IpAddress(self, initval = None):
		return {
			"ctype"         : ctypes.c_uint,
			"flags"         : WATCHER_FIXED_SIZE,
			"initval"       : 0,
			"register_func" : self._registerWatcher,
			"asntype"       : ASN_IPADDRESS
		}

	# Note we can't use ctypes.c_char_p here since that creates an immutable
	# type and net-snmp _can_ modify the buffer (unless writable is False).
	@VarTypeClass
	def OctetString(self, initval = None):
		return {
			"ctype"         : ctypes.create_string_buffer,
			"flags"         : WATCHER_SIZE_STRLEN,
			"max_size"      : MAX_STRING_SIZE,
			"initval"       : "",
			"register_func" : self._registerWatcher,
			"asntype"       : ASN_OCTET_STR
		}

	# From our point of view, DisplayString can be treated absolutely
	# identical to OctetString, but should have its own class nevertheless
	@VarTypeClass
	def DisplayString(self, initval = None):
		return {
			"ctype"         : ctypes.create_string_buffer,
			"flags"         : WATCHER_SIZE_STRLEN,
			"max_size"      : MAX_STRING_SIZE,
			"initval"       : "",
			"register_func" : self._registerWatcher,
			"asntype"       : ASN_OCTET_STR
		}

	def getRegistered(self):
		""" Returns a dictionary with the currently registered SNMP objects. """
		myobjs = {}
		for (oidstr,snmpobj) in self._objs.iteritems():
			myobjs[oidstr] = {
				"type": type(snmpobj).__name__,
				"value": snmpobj.value()
			}
		return myobjs

	def start(self):
		""" Starts the agent. Among other things, this means connecting
		    to the master agent, if configured that way. """
		self._started = True
		self._agentlib.init_snmp(self.AgentName)

	def poll(self):
		""" Blocks and processes incoming SNMP requests. """
		return self._agentlib.agent_check_and_process(1)

	def __del__(self):
		if (self._agentlib):
			self._agentlib.snmp_shutdown(self.AgentName)
			self._agentlib = None

class netsnmpAgentException(Exception):
	pass
