"""The D-Bus names, kept in a module with no dependencies.

The daemon publishes the interface with dasbus and the GUI consumes it with
Qt's own bindings, so neither side should have to import the other's D-Bus
library just to learn what the service is called.
"""

BUS_NAME = "org.fancontrol.Daemon"
OBJECT_PATH = "/org/fancontrol/Daemon"
INTERFACE = "org.fancontrol.Daemon1"
