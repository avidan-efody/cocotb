# Copyright cocotb contributors
# Licensed under the Revised BSD License, see LICENSE for details.
# SPDX-License-Identifier: BSD-3-Clause
"""
Tests for concurrency primitives like First and Combine
"""
import cocotb
from cocotb.triggers import Timer, First, Event, Combine
import textwrap
from common import _check_traceback
from random import randint
from collections import deque
from cocotb.utils import get_sim_time


@cocotb.test()
async def test_unfired_first_triggers(dut):
    """ Test that un-fired trigger(s) in First don't later cause a spurious wakeup """
    # gh-843
    events = [Event() for i in range(3)]

    waiters = [e.wait() for e in events]

    async def wait_for_firsts():
        ret_i = waiters.index(await First(waiters[0], waiters[1]))
        assert ret_i == 0, f"Expected event 0 to fire, not {ret_i}"

        ret_i = waiters.index(await First(waiters[2]))
        assert ret_i == 2, f"Expected event 2 to fire, not {ret_i}"

    async def wait_for_e1():
        """ wait on the event that didn't wake `wait_for_firsts` """
        ret_i = waiters.index(await waiters[1])
        assert ret_i == 1, f"Expected event 1 to fire, not {ret_i}"

    async def fire_events():
        """ fire the events in order """
        for e in events:
            await Timer(1, "ns")
            e.set()

    fire_task = cocotb.fork(fire_events())
    e1_task = cocotb.fork(wait_for_e1())
    await wait_for_firsts()

    # make sure the other tasks finish
    await fire_task.join()
    await e1_task.join()


@cocotb.test()
async def test_nested_first(dut):
    """ Test that nested First triggers behave as expected """
    events = [Event() for i in range(3)]
    waiters = [e.wait() for e in events]

    async def fire_events():
        """ fire the events in order """
        for e in events:
            await Timer(1, "ns")
            e.set()

    async def wait_for_nested_first():
        inner_first = First(waiters[0], waiters[1])
        ret = await First(inner_first, waiters[2])

        # should unpack completely, rather than just by one level
        assert ret is not inner_first
        assert ret is waiters[0]

    fire_task = cocotb.fork(fire_events())
    await wait_for_nested_first()
    await fire_task.join()


@cocotb.test()
async def test_first_does_not_kill(dut):
    """ Test that `First` does not kill coroutines that did not finish first """
    ran = False

    @cocotb.coroutine   # decorating `async def` is required to use `First`
    async def coro():
        nonlocal ran
        await Timer(2, units='ns')
        ran = True

    # Coroutine runs for 2ns, so we expect the timer to fire first
    timer = Timer(1, units='ns')
    t = await First(timer, coro())
    assert t is timer
    assert not ran

    # the background routine is still running, but should finish after 1ns
    await Timer(2, units='ns')

    assert ran


@cocotb.test()
async def test_exceptions_first(dut):
    """ Test exception propagation via cocotb.triggers.First """

    @cocotb.coroutine   # decorating `async def` is required to use `First`
    def raise_inner():
        yield Timer(10, "ns")
        raise ValueError('It is soon now')

    async def raise_soon():
        await Timer(1, "ns")
        await cocotb.triggers.First(raise_inner())

    # it's ok to change this value if the traceback changes - just make sure
    # that when changed, it doesn't become harder to read.
    expected = textwrap.dedent(r"""
    Traceback \(most recent call last\):
      File ".*common\.py", line \d+, in _check_traceback
        await running_coro
      File ".*test_concurrency_primitives\.py", line \d+, in raise_soon
        await cocotb\.triggers\.First\(raise_inner\(\)\)
      File ".*triggers\.py", line \d+, in _wait
        return await first_trigger[^\n]*
      File ".*triggers.py", line \d+, in __await__
        return \(yield self\)
      File ".*triggers.py", line \d+, in __await__
        return \(yield self\)
      File ".*test_concurrency_primitives\.py", line \d+, in raise_inner
        raise ValueError\('It is soon now'\)
    ValueError: It is soon now""").strip()

    await _check_traceback(raise_soon(), ValueError, expected)


@cocotb.test()
async def test_combine(dut):
    """ Test the Combine trigger. """
    # gh-852

    async def do_something(delay):
        await Timer(delay, "ns")

    crs = [cocotb.fork(do_something(dly)) for dly in [10, 30, 20]]

    await Combine(*(cr.join() for cr in crs))


@cocotb.test()
async def test_event_is_set(dut):
    e = Event()

    assert not e.is_set()
    e.set()
    assert e.is_set()
    e.clear()
    assert not e.is_set()


@cocotb.test()
async def test_combine_start_soon(_):
    async def coro(delay):
        start_time = cocotb.utils.get_sim_time(units="ns")
        await Timer(delay, "ns")
        assert cocotb.utils.get_sim_time(units="ns") == start_time + delay

    max_delay = 10

    coros = [cocotb.scheduler.start_soon(coro(d)) for d in range(1, max_delay + 1)]

    test_start = cocotb.utils.get_sim_time(units="ns")
    await Combine(*coros)
    assert cocotb.utils.get_sim_time(units="ns") == test_start + max_delay


@cocotb.test()
async def test_recursive_combine_and_fork(_):
    """ Test using `Combine` on forked coroutines that themselves use `Combine`. """

    async def mergesort(n):
        if len(n) == 1:
            return n
        part1 = n[:len(n) // 2]
        part2 = n[len(n) // 2:]
        sort1 = cocotb.fork(mergesort(part1))
        sort2 = cocotb.fork(mergesort(part2))
        await Combine(sort1, sort2)
        res1 = deque(sort1.retval)
        res2 = deque(sort2.retval)
        res = []
        while res1 and res2:
            if res1[0] < res2[0]:
                res.append(res1.popleft())
            else:
                res.append(res2.popleft())
        res.extend(res1)
        res.extend(res2)
        return res

    t = [randint(0, 1000) for _ in range(100)]
    res = await mergesort(t)
    t.sort()
    assert t == res


@cocotb.test()
async def test_recursive_combine(_):
    """ Test passing a `Combine` trigger directly to another `Combine` trigger. """

    done = set()

    async def waiter(N):
        await Timer(N, 'ns')
        done.add(N)

    start_time = get_sim_time('ns')
    await Combine(
        Combine(
            cocotb.fork(waiter(10)),
            cocotb.fork(waiter(20))
        ),
        cocotb.fork(waiter(30))
    )
    end_time = get_sim_time('ns')

    assert end_time - start_time == 30
    assert done == {10, 20, 30}
