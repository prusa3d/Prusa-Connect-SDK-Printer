"""
A component to handle error conditions

Conditions are tree nodes, if a parent node becomes NOK,
all children become NOK too. If all children of a node become OK,
the parent node becomes OK

Use the tracker to get the most important error according to the tracked
condition priorities. This should make it easy to show the same error
independent of whether it's the LCD or the web.
"""
from __future__ import annotations

import math
from enum import Enum
from multiprocessing import RLock
from typing import Callable, Optional, Set

cond_lock = RLock()


class CondState(Enum):
    """Describes the possible condition states"""
    UNKNOWN = None
    OK = True
    NOK = False


# pylint: disable=too-many-arguments
class Condition:
    """A more detailed condition for state tracking"""

    def __init__(self,
                 name: str,
                 long_msg: str,
                 parent: Optional[Condition] = None,
                 short_msg: Optional[str] = None,
                 priority: int = 0):
        self.name: str = name
        self.long_msg: str = long_msg
        self.short_msg: str = short_msg or name
        self._priority: int = priority
        self._broke_cb: Set[Callable[[Condition, CondState], None]] = set()
        self._fixed_cb: Set[Callable[[Condition, CondState], None]] = set()

        self._parent: Optional[Condition] = None
        self._children: Set[Condition] = set()
        self._state = CondState.UNKNOWN

        if parent is not None:
            self.set_parent(parent)

    def set_parent(self, parent: Condition):
        """Add a parent condition - meant for joining condition trees"""
        with cond_lock:
            if parent == self.parent:
                return
            if self.parent is not None:
                raise ValueError("Can't replace a parent with another")
            if not parent.state == self.state == CondState.UNKNOWN:
                raise ValueError("Re-planting of initialized trees is "
                                 "not supported.")
            self._parent = parent
            if parent is not None:
                # pylint: disable=protected-access
                parent.add_broke_handler(self._parent_broke)

                parent._children.add(self)
                # pylint: disable=protected-access
                self.add_fixed_handler(parent._child_fixed)

    def _parent_broke(self, *_):
        """Automatically become broken, if our parent breaks"""
        self.state = CondState.NOK

    def _child_fixed(self, *_):
        """Automatically become OK if all of our children are OK"""
        if all(child for child in self._children):
            self.state = CondState.OK

    def add_broke_handler(self, handler: Callable[[Condition, CondState],
                                                  None]):
        """Adds a handler for a condition breaking"""
        with cond_lock:
            if handler in self._broke_cb:
                raise ValueError("Can't add an already added handler")
            self._broke_cb.add(handler)

    def add_fixed_handler(self, handler: Callable[[Condition, CondState],
                                                  None]):
        """Adds a handler for a condition fixing itself"""
        with cond_lock:
            if handler in self._fixed_cb:
                raise ValueError("Can't add an already added handler")
            self._fixed_cb.add(handler)

    def remove_broke_handler(self, handler: Callable[[Condition, CondState],
                                                     None]):
        """Removes a handler for a condition breaking"""
        with cond_lock:
            self._broke_cb.remove(handler)

    def remove_fixed_handler(self, handler: Callable[[Condition, CondState],
                                                     None]):
        """Removes a handler for a condition fixing itself"""
        with cond_lock:
            self._fixed_cb.remove(handler)

    @property
    def parent(self):
        """Returns this node's parent"""
        return self._parent

    @property
    def children(self):
        """Returns this node's parent"""
        return self._children

    @property
    def state(self):
        """Returns the CondState Enum value"""
        return self._state

    @state.setter
    def state(self, state: CondState):
        """Sets the condition using the CondState"""
        if state == CondState.UNKNOWN:
            raise ValueError("Can't reset a condition to unknown")
        with cond_lock:  # Make sure only one thread is changing our states
            if state is self._state:  # skip handlers
                return
            old_state = self._state
            self._state = state
            if self:
                for handler in self._fixed_cb:
                    handler(self, old_state)
            else:
                for handler in self._broke_cb:
                    handler(self, old_state)

    @property
    def priority(self):
        """Returns True if current state is OK, False if not, None if unknown
        """
        return self._priority

    def successors_ok(self):
        """Returns True if all the successors are OK"""
        for successor in self:
            if not successor:
                return False
        return True

    def __bool__(self):
        """Returns True only if we know we're OK"""
        return self._state is CondState.OK

    def __str__(self):
        return f"{self.name}: {self._state.name}"

    def __iter__(self):
        for child in self._children:
            yield from child
        yield self


class ConditionTracker:
    """
    Tracks the NOK conditions and their priorities

    The time complexity is O(n), but the number of
    items is expected to be low
    """

    def __init__(self) -> None:
        self._nok_conditions: Set[Condition] = set()
        self._tracked_conditions: Set[Condition] = set()
        self._cached_worst = None

    def add_tracked_condition_tree(self, root_condition: Condition):
        """Add a tree of conditions to be tracked"""
        with cond_lock:
            for condition in root_condition:
                self.add_tracked_condition(condition)

    def add_tracked_condition(self, condition: Condition):
        """Add a conditions to be tracked"""
        with cond_lock:
            if condition not in self._tracked_conditions:
                self._tracked_conditions.add(condition)
                condition.add_broke_handler(self._tracked_broke)
                condition.add_fixed_handler(self._tracked_fixed)
                if condition.state == CondState.NOK:
                    self._tracked_broke(condition, CondState.OK)

    def remove_tracked_condition_tree(self, condition_root: Condition):
        """Remove a tree of conditions, so they're not tracked anymore"""
        with cond_lock:
            for condition in condition_root:
                self.remove_tracked_condition(condition)

    def remove_tracked_condition(self, condition: Condition):
        """Remove a condition, so it's not tracked anymore"""
        with cond_lock:
            if condition in self._tracked_conditions:
                self._tracked_conditions.remove(condition)
                condition.remove_broke_handler(self._tracked_broke)
                condition.remove_fixed_handler(self._tracked_fixed)
            if condition in self._nok_conditions:
                self._tracked_fixed(condition)

    def _tracked_broke(self, condition: Condition, _=None):
        """
        Handle a tracked condition breaking by adding it to the broken ones
        if it's not ignored

        expectation is the cond_lock is already locked from the state setter
        """
        self._nok_conditions.add(condition)
        self._cached_worst = None

    def _tracked_fixed(self, condition: Condition, _=None):
        """
        Handle a tracked condition fixing itself - remove it to the broken ones

        expectation is the cond_lock is already locked from the state setter
        """
        if condition in self._nok_conditions:
            self._nok_conditions.remove(condition)
            self._cached_worst = None

    @property
    def nok_conditions(self):
        """Gets every tracked nok condition"""
        return self._nok_conditions

    def get_worst(self):
        """
        Gets the broken condition with the highest priority
        Returns None if there's no broken ones
        """
        with cond_lock:
            if self._cached_worst is not None:
                return self._cached_worst

            worst_error = None
            worst_priority = math.inf * -1
            for condition in self._nok_conditions:
                if condition.priority >= worst_priority:
                    worst_error = condition
                    worst_priority = condition.priority
            self._cached_worst = worst_error
            return worst_error

    def is_tracked(self, condition: Condition):
        """Is the specified condition being tracked"""
        return condition in self._tracked_conditions


# ---- The same condition chain as before ----

# Error chain representing a kind of semaphore signaling the status
# of the connection to Connect
INTERNET = Condition("Internet", "DNS does not work, or there are other "
                     "problems in communication to other hosts "
                     "in the Internet.",
                     priority=130)
HTTP = Condition("HTTP", "HTTP communication to Connect fails, "
                 "we're getting 5XX statuses",
                 parent=INTERNET,
                 priority=120)
# Signal if we have a token or not
TOKEN = Condition("Token", "Printer has no valid token, "
                  "it needs to be registered with Connect.",
                  parent=HTTP,
                  priority=110)
API = Condition("API", "Encountered 4XX problems while "
                "communicating to Connect",
                parent=TOKEN,
                priority=100)

COND_TRACKER = ConditionTracker()
COND_TRACKER.add_tracked_condition_tree(INTERNET)
