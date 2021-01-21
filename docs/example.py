# Author   : Martin Užák <uzak+git@mailbox.org>
# Creation : 2021-01-21 12:27

from enum import Enum


class DeviceType(Enum):
    Connect = "Connect"
    PrinterWeb = "PrinterWeb"
    PrinterLCD = "PrinterLCD"


class State:
    def __init__(self, name, message, parent=None):
        self.name = name
        self.parent = parent
        self.children = []
        self._state = None  # None = undefined, True = OK, False = NOK
        self.messages = {}  # XXX maybe introduce a `Messages` class
        self.set_messages(message, message, name)

    def set_messages(self, connect=None, web=None, lcd=None):
        self.messages[DeviceType.Connect] = connect
        self.messages[DeviceType.PrinterWeb] = web
        self.messages[DeviceType.PrinterLCD] = lcd

    @property
    def ok(self):
        if self.parent is None:
            return self._state
        if self.parent.ok is False:
            return False
        if self.parent.ok is True:
            return self._state
        if self.parent.ok is None:
            return None
        return self._state

    @ok.setter
    def ok(self, value):
        self._state = value
        if value is True and self.parent:
            self.parent.ok = True

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, parent: "State"):
        self._parent = parent
        if parent is not None:
            parent.children.append(self)

    def _leaves(self, result):
        if not self.children:
            result.append(self)
        else:
            for c in self.children:
                c._leaves(result)
        return result

    def leaves(self):
        result = []
        self._leaves(result)
        return result

    def fmt_for(self, output: DeviceType):
        return self.messages[output]

    def __str__(self):
        return f"{self.name}: {self.ok}"

    __repr__ = __str__

    def visualise(self):
        for leave in self.leaves():
            nodes = [leave]
            parent = leave.parent
            while parent is not None:
                nodes.append(parent)
                parent = parent.parent
            print(nodes)


root = device = State("device", "Ethernet or WIFI device exists")
phy = State("phy", "Ethernet or WIFI connected", parent=device)
lan = State("lan", "Device has assigned IP", parent=phy)

internet = State("internet",
                 "DNS works and other hosts in the internet can be reached")
http = State("http", "HTTP traffic to Connect is OK, no 5XX statuses")
connect_ok = State("connect_ok",
                   "There are no 4XX problems while communicating to Connect")
internet.parent = lan
http.parent = internet
connect_ok.parent = http

movement_ip = State("movement_ip", "Movement device alive on IP")
movement_conn = State("movement_conn", "TCP/UDP connection with device is OK")
movement_proto = State("movement_proto",
                       "TCP/UDP comm. with device protocol is OK")
movement_ok = State("movement_ok", "Device is enabled")
movement_ip.parent = lan
movement_conn.parent = movement_ip
movement_proto.parent = movement_conn
movement_ok.parent = movement_proto

picker_ip = State("picker_ip", "Picker device alive on IP")
picker_conn = State("picker_conn", "TCP/UDP connection with device is OK")
picker_proto = State("picker_proto",
                     "TCP/UDP comm. with device protocol is OK")
picker_ok = State("picker_ok", "Device is enabled")
picker_ip.parent = lan
picker_conn.parent = picker_ip
picker_proto.parent = picker_conn
picker_ok.parent = picker_proto

print("#" * 80)
# Default tree without initialization
root.visualise()

print("#" * 80)
movement_conn.ok = True
# Set parent of a leaf to True ("OK") -> everything above it should be "OK"
# Yet all the leaves should be None ("Undefined")
root.visualise()

print("#" * 80)
# Set all leaves to True -> all OK
end_states = root.leaves()

for leaf in end_states:
    leaf.ok = True
root.visualise()

print()
print(http)
print("Connect", http.fmt_for(DeviceType.Connect))
print("PrinterWeb", http.fmt_for(DeviceType.PrinterWeb))
print("PrinterLCD", http.fmt_for(DeviceType.PrinterLCD))
