from unittest.mock import Mock

import pytest

from prusa.connect.printer.conditions import CondState, Condition, \
    ConditionTracker


def test_sanity():
    root = Condition("Root", "Lorem ipsum", priority=0)
    child = Condition("Child", "Lorem ipsum", priority=1, parent=root)
    assert child.parent == root
    assert child in root.children


def test_callbacks():
    root = Condition("Root", "Lorem ipsum", priority=0)
    broke_callback = Mock()
    fixed_callback = Mock()
    root.add_broke_handler(broke_callback)
    root.add_fixed_handler(fixed_callback)
    root.state = CondState.NOK
    broke_callback.assert_called_once_with(root, CondState.UNKNOWN)
    root.state = CondState.OK
    fixed_callback.assert_called_once_with(root, CondState.NOK)


def test_removing_callbacks():
    root = Condition("Root", "Lorem ipsum", priority=0)
    broke_callback = Mock()
    fixed_callback = Mock()
    root.add_broke_handler(broke_callback)
    root.add_fixed_handler(fixed_callback)
    with pytest.raises(ValueError):
        root.add_broke_handler(broke_callback)
    with pytest.raises(ValueError):
        root.add_fixed_handler(fixed_callback)

    root.remove_broke_handler(broke_callback)
    root.remove_fixed_handler(fixed_callback)
    root.state = CondState.NOK
    broke_callback.assert_not_called()
    root.state = CondState.OK
    fixed_callback.assert_not_called()


def test_propagation():
    grandma = Condition("Grandma", "Sweet old lady baking pies")
    mom = Condition("Mom", "Reason I exist", parent=grandma)
    aunt = Condition("Aunt", "An aunt", parent=grandma)
    me = Condition("Me", "*exists*", parent=mom)

    grandma.state = CondState.OK
    mom.state = CondState.NOK
    aunt.state = CondState.NOK
    assert grandma.state == CondState.OK
    assert mom.state == CondState.NOK
    assert aunt.state == CondState.NOK
    assert me.state == CondState.NOK

    grandma.state = CondState.NOK
    mom.state = CondState.OK
    assert grandma.state == CondState.NOK
    assert mom.state == CondState.OK
    assert aunt.state == CondState.NOK
    assert me.state == CondState.NOK

    aunt.state = CondState.OK
    assert grandma.state == CondState.OK
    assert mom.state == CondState.OK
    assert aunt.state == CondState.OK
    assert me.state == CondState.NOK


def test_tracker_item_management():
    tracker = ConditionTracker()
    root = Condition("Root", "Lorem ipsum")
    a = Condition("Node A", "Lorem ipsum", parent=root)
    b = Condition("Node B", "Lorem ipsum", parent=root)
    a_l = Condition("Node A->L", "Lorem ipsum", parent=a)
    a_m = Condition("Node A->M", "Lorem ipsum", parent=a)
    b_n = Condition("Node B->N", "Lorem ipsum", parent=b)
    b_o = Condition("Node B->O", "Lorem ipsum", parent=b)
    all_nodes = {root, a, b, a_l, a_m, b_n, b_o}
    for node in all_nodes:
        node.state = CondState.OK

    tracker.add_tracked_condition_tree(a)
    assert {a, a_l, a_m} == tracker._tracked_conditions

    b_o.state = CondState.NOK
    tracker.add_tracked_condition_tree(b)
    assert tracker.get_worst() == b_o

    tracker.remove_tracked_condition(b_o)
    assert tracker.get_worst() is None
    assert b_o not in tracker._tracked_conditions

    # Test that we don't track removed condi
    tracker.remove_tracked_condition_tree(a)
    a_l.state = CondState.NOK
    assert {a, a_l, a_m} not in tracker._tracked_conditions
    assert tracker.get_worst() is None


def test_priority():
    tracker = ConditionTracker()
    root = Condition("Root", "Lorem ipsum", priority=0)
    child = Condition("Child", "Lorem ipsum", priority=1, parent=root)
    tracker.add_tracked_condition_tree(root)
    root.state = CondState.NOK
    child.state = CondState.NOK
    assert tracker.get_worst() == child


def test_replanting():
    root = Condition("Root", "Lorem ipsum", priority=0)
    child = Condition("Child", "Lorem ipsum", priority=0, parent=root)
    broke_handler = Mock()
    child.add_broke_handler(broke_handler)

    new_root = Condition("New root", "Lorem ipsum", priority=0)
    root.set_parent(new_root)
    new_root.state = CondState.NOK
    broke_handler.assert_called_once_with(child, CondState.UNKNOWN)


def test_forbidden_replanting():
    root = Condition("Root", "Lorem ipsum", priority=0)
    root.state = CondState.OK

    with pytest.raises(ValueError):
        Condition("Child", "Lorem ipsum", priority=0, parent=root)


def test_set_parent():
    root = Condition("Root", "Lorem ipsum", priority=0)
    fake_rooot = Condition("FakeRoot", "Lorem ipsum", priority=0)
    child = Condition("Child", "Lorem ipsum", priority=0)

    child.set_parent(root)
    # Setting the same parent is ok
    child.set_parent(root)
    # Changing the parent is not ok
    with pytest.raises(ValueError):
        child.set_parent(fake_rooot)



def test_bool():
    cond = Condition("Condition", "Lorem ipsum", )
    assert not bool(cond)
    cond.state = CondState.OK
    assert bool(cond)
    cond.state = CondState.NOK
    assert not bool(cond)


def test_descendants():
    root = Condition("Root", "Lorem ipsum")
    a = Condition("Node A", "Lorem ipsum", parent=root)
    b = Condition("Node B", "Lorem ipsum", parent=root)
    a_l = Condition("Node A->L", "Lorem ipsum", parent=a)
    a_m = Condition("Node A->M", "Lorem ipsum", parent=a)
    b_n = Condition("Node B->N", "Lorem ipsum", parent=b)
    b_o = Condition("Node B->O", "Lorem ipsum", parent=b)
    for node in [root, a, b, a_l, a_m, b_n]:
        node.state = CondState.OK
    b_o.state = CondState.NOK

    assert not root.successors_ok()
    b_o.state = CondState.OK
    assert root.successors_ok()


def test_init_to_ok():
    # This scenario was broken, test it does not raise errors
    tracker = ConditionTracker()
    node = Condition("Node", "Lorem ipsum")
    tracker.add_tracked_condition(node)
    node.state = CondState.OK


def test_double_add():
    # This scenario was broken, test it does not raise errors
    tracker = ConditionTracker()
    node = Condition("Node", "Lorem ipsum")
    tracker.add_tracked_condition(node)
    tracker.add_tracked_condition(node)


def test_double_remove():
    # This scenario was broken, test it does not raise errors
    tracker = ConditionTracker()
    node = Condition("Node", "Lorem ipsum")
    tracker.remove_tracked_condition(node)
    tracker.remove_tracked_condition(node)


def test_getting_all():
    tracker = ConditionTracker()
    root = Condition("Root", "Lorem ipsum")
    child = Condition("Child", "Lorem ipsum", parent=root)
    foo = Condition("Foo", "Lorem ipsum")
    tracker.add_tracked_condition_tree(root)
    tracker.add_tracked_condition(foo)
    root.state = CondState.NOK
    assert tracker.nok_conditions == {root, child}

