"""While Claude's script runs, FreeCAD keeps drawing: the line tracer lets the GUI paint every so often."""

import sys

import pytest

from claudebridge.bridge import pacing


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def run_traced(code, tracer, filename="<claude>"):
    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        exec(compile(code, filename, "exec"), {})
    finally:
        sys.settrace(previous)


def test_the_gui_is_pumped_at_most_every_interval():
    clock, pumps = Clock(), []
    tracer = pacing.make_tracer(deadline=1e9, filename="<claude>", pump=lambda: pumps.append(clock.t),
                                interval=0.1, clock=clock)
    code = "for i in range(50):\n    T.t += 0.02\n"
    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        exec(compile(code, "<claude>", "exec"), {"T": clock})
    finally:
        sys.settrace(previous)
    assert 8 <= len(pumps) <= 11, pumps                      # 1 s of work, a pump about every 0.1 s
    assert all(b - a >= 0.1 - 1e-9 for a, b in zip(pumps, pumps[1:], strict=False))


def test_the_time_limit_still_stops_a_loop():
    clock = Clock()
    tracer = pacing.make_tracer(deadline=0.5, filename="<claude>", pump=lambda: None, interval=0.1, clock=clock)
    with pytest.raises(pacing.ScriptTimeout):
        previous = sys.gettrace()
        sys.settrace(tracer)
        try:
            exec(compile("while True:\n    T.t += 0.1\n", "<claude>", "exec"), {"T": clock})
        finally:
            sys.settrace(previous)


def test_code_loaded_with_exec_keeps_the_gui_alive_too():
    clock, pumps = Clock(), []
    tracer = pacing.make_tracer(deadline=1e9, filename="<claude>", pump=lambda: pumps.append(1), interval=0.1,
                                clock=clock)
    inner = "def step(T):\n    T.t += 0.2\n"
    code = f"ns = {{}}\nexec({inner!r}, ns)\nfor i in range(10):\n    ns['step'](T)\n"
    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        exec(compile(code, "<claude>", "exec"), {"T": clock})
    finally:
        sys.settrace(previous)
    assert len(pumps) >= 9, "every call into exec'd code may pump"


def test_no_pump_means_no_pumping():
    clock = Clock()
    tracer = pacing.make_tracer(deadline=1e9, filename="<claude>", pump=None, interval=0.1, clock=clock)
    run_traced("x = 1\n", tracer)


def test_redrawing_costs_at_most_a_tenth_of_the_time():
    clock, spent = Clock(), [0.0]

    def slow_pump():                                     # a heavy model: each redraw takes 50 ms
        clock.t += 0.05
        spent[0] += 0.05

    tracer = pacing.make_tracer(deadline=1e9, filename="<claude>", pump=slow_pump, interval=0.1, clock=clock)
    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        exec(compile("for i in range(1000):\n    T.t += 0.01\n", "<claude>", "exec"), {"T": clock})
    finally:
        sys.settrace(previous)
    assert spent[0] <= 0.11 * clock.t, (spent[0], clock.t)
    assert spent[0] > 0, "it still redraws"
