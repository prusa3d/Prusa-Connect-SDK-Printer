# Author   : Martin Užák <uzak+git@mailbox.org>
# Creation : 2021-01-21 12:27


class State:
    """State as doubly linked list"""
    def __init__(self, name, long_msg, prev=None, short_msg=None):
        self.name = name
        self.prev = prev
        self.next = None
        self.ok = False
        self.long_msg = long_msg
        self.short_msg = short_msg or name

    @property
    def prev(self):
        return self._prev

    @prev.setter
    def prev(self, prev: "State"):
        self._prev = prev
        if prev is not None:
            prev.next = self

    def __str__(self):
        return f"{self.name}: {self.ok}"

    __repr__ = __str__


internet = State("internet",
                 "DNS works and other hosts in the internet can be reached")
http = State("http",
             "HTTP traffic to Connect is OK, no 5XX statuses",
             prev=internet)
connect_ok = State("connect_ok",
                   "There are no 4XX problems while communicating to Connect",
                   prev=http)

movement_ip = State("movement_ip", "Movement device alive on IP")
movement_conn = State("movement_conn",
                      "TCP/UDP connection with device is OK",
                      prev=movement_ip)
movement_proto = State("movement_proto",
                       "TCP/UDP comm. with device protocol is OK",
                       prev=movement_conn)
movement_ok = State("movement_ok", "Device is enabled", prev=movement_proto)

picker_ip = State("picker_ip", "Picker device alive on IP")
picker_conn = State("picker_conn",
                    "TCP/UDP connection with device is OK",
                    prev=picker_ip)
picker_proto = State("picker_proto",
                     "TCP/UDP comm. with device protocol is OK",
                     prev=picker_conn)
picker_ok = State("picker_ok", "Device is enabled", prev=picker_proto)

movement_proto.ok = True
print(movement_proto)
print(movement_ok)

# other end states
print(connect_ok)
print(picker_ok)
