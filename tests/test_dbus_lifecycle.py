"""Exercise real listener threads with deterministic, hardware-free GLib barriers."""

import threading
from collections.abc import Callable, Iterator
from unittest.mock import MagicMock

import pytest

from venus_observability import dbus_listener
from venus_observability.dbus_listener import DBusSignalListener


class FakeLoop:
    """Model GLib's lost pre-run quit, with barriers at each lifecycle boundary."""

    def __init__(self) -> None:
        self.construct_entered = threading.Event()
        self.allow_construct = threading.Event()
        self.run_entered = threading.Event()
        self.allow_run = threading.Event()
        self.running = threading.Event()
        self.quit_requested = threading.Event()
        self.exit_entered = threading.Event()
        self.allow_exit = threading.Event()
        self.allow_construct.set()
        self.allow_run.set()
        self.allow_exit.set()
        self.run_calls = 0
        self.on_run: Callable[[], None] | None = None

    def construct(self) -> "FakeLoop":
        self.construct_entered.set()
        assert self.allow_construct.wait(3), "constructor barrier was not released"
        return self

    def run(self) -> None:
        self.run_calls += 1
        self.run_entered.set()
        assert self.allow_run.wait(3), "run barrier was not released"
        # g_main_loop_run sets is_running to true, losing an earlier quit.
        self.quit_requested.clear()
        self.running.set()
        try:
            if self.on_run is not None:
                self.on_run()
            assert self.quit_requested.wait(3), "loop was not stopped"
            self.exit_entered.set()
            assert self.allow_exit.wait(3), "exit barrier was not released"
        finally:
            self.running.clear()

    def is_running(self) -> bool:
        return self.running.is_set()

    def quit(self) -> None:
        self.quit_requested.set()


@pytest.fixture
def lifecycle(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[DBusSignalListener, FakeLoop]]:
    loop = FakeLoop()
    monkeypatch.setattr("venus_observability.dbus_listener.GLib.MainLoop", loop.construct)
    listener = DBusSignalListener(MagicMock(), bus=MagicMock(), tracer=MagicMock())
    try:
        yield listener, loop
    finally:
        loop.allow_construct.set()
        loop.allow_run.set()
        loop.allow_exit.set()
        # Also clean up an unfixed implementation after a regression assertion fails.
        thread = listener._loop_thread
        if thread is not None:
            for _ in range(100):
                loop.quit()
                thread.join(0.02)
                if not thread.is_alive():
                    break
            assert not thread.is_alive(), "test leaked its fake GLib worker"
        listener.stop()


def stop_in_thread(
    listener: DBusSignalListener,
) -> tuple[threading.Thread, threading.Event, list[BaseException]]:
    returned = threading.Event()
    errors: list[BaseException] = []

    def stop() -> None:
        try:
            listener.stop()
        except BaseException as error:
            errors.append(error)
        finally:
            returned.set()

    thread = threading.Thread(target=stop, daemon=True)
    thread.start()
    return thread, returned, errors


def test_stop_during_constructor_never_enters_run(
    lifecycle: tuple[DBusSignalListener, FakeLoop],
) -> None:
    listener, loop = lifecycle
    loop.allow_construct.clear()
    listener.start()
    assert loop.construct_entered.wait(1)
    stopper, returned, errors = stop_in_thread(listener)
    try:
        assert not returned.wait(0.05), "stop returned with a live startup worker"
    finally:
        loop.allow_construct.set()
        stopper.join(1)
    assert not stopper.is_alive()
    assert not errors
    assert listener._loop_thread is not None
    assert not listener._loop_thread.is_alive()
    assert loop.run_calls == 0


def test_stop_during_run_transition_is_not_lost(
    lifecycle: tuple[DBusSignalListener, FakeLoop],
) -> None:
    listener, loop = lifecycle
    loop.allow_run.clear()
    listener.start()
    assert loop.run_entered.wait(1)
    stopper, returned, errors = stop_in_thread(listener)
    try:
        assert not returned.wait(0.05), "stop returned before GLib entered run"
    finally:
        loop.allow_run.set()
        stopper.join(1)
    assert not stopper.is_alive()
    assert not errors
    assert listener._loop_thread is not None
    assert not listener._loop_thread.is_alive()


def test_stop_waits_for_worker_exit(lifecycle: tuple[DBusSignalListener, FakeLoop]) -> None:
    listener, loop = lifecycle
    loop.allow_exit.clear()
    listener.start()
    assert loop.running.wait(1)
    stopper, returned, errors = stop_in_thread(listener)
    try:
        assert loop.exit_entered.wait(1)
        assert not returned.wait(0.05), "stop did not join the exiting worker"
    finally:
        loop.allow_exit.set()
        stopper.join(1)
    assert not stopper.is_alive()
    assert not errors
    assert listener._loop_thread is not None
    assert not listener._loop_thread.is_alive()
    assert listener._main_loop is None


def test_timeout_keeps_worker_handle_and_can_be_joined_later(
    lifecycle: tuple[DBusSignalListener, FakeLoop], monkeypatch: pytest.MonkeyPatch
) -> None:
    listener, loop = lifecycle
    monkeypatch.setattr(dbus_listener, "STOP_TIMEOUT_SECONDS", 0.05)
    loop.allow_construct.clear()
    listener.start()
    assert loop.construct_entered.wait(1)
    with pytest.raises(TimeoutError, match="D-Bus event loop did not stop"):
        listener.stop()
    assert listener._loop_thread is not None
    assert listener._loop_thread.is_alive()
    with pytest.raises(RuntimeError, match="stopped"):
        listener.start()
    loop.allow_construct.set()
    listener.stop()
    assert not listener._loop_thread.is_alive()
    assert loop.run_calls == 0


def test_stop_from_worker_does_not_join_itself(
    lifecycle: tuple[DBusSignalListener, FakeLoop],
) -> None:
    listener, loop = lifecycle
    loop.on_run = listener.stop
    listener.start()
    assert listener._loop_thread is not None
    listener._loop_thread.join(1)
    assert not listener._loop_thread.is_alive()
    assert loop.exit_entered.is_set()


def test_start_cannot_create_duplicate_or_reopen_stopped_listener(
    lifecycle: tuple[DBusSignalListener, FakeLoop],
) -> None:
    listener, loop = lifecycle
    listener.start()
    assert loop.running.wait(1)
    original = listener._loop_thread
    with pytest.raises(RuntimeError, match="already started"):
        listener.start()
    assert listener._loop_thread is original
    listener.stop()
    listener.stop()
    with pytest.raises(RuntimeError, match="stopped"):
        listener.start()
    assert loop.run_calls == 1


def test_stop_before_start_is_terminal(lifecycle: tuple[DBusSignalListener, FakeLoop]) -> None:
    listener, loop = lifecycle
    listener.stop()
    listener.stop()
    with pytest.raises(RuntimeError, match="stopped"):
        listener.start()
    assert not loop.construct_entered.is_set()


def test_interrupted_thread_start_retains_handle_for_cleanup(
    lifecycle: tuple[DBusSignalListener, FakeLoop], monkeypatch: pytest.MonkeyPatch
) -> None:
    listener, loop = lifecycle
    original_start = threading.Thread.start
    started: list[threading.Thread] = []

    def interrupted_start(thread: threading.Thread) -> None:
        original_start(thread)
        started.append(thread)
        raise KeyboardInterrupt

    monkeypatch.setattr(threading.Thread, "start", interrupted_start)
    try:
        with pytest.raises(KeyboardInterrupt):
            listener.start()
        assert listener._loop_thread is started[0]
        listener.stop()
        assert not started[0].is_alive()
    finally:
        # Do not leak the captured worker if a regression loses its owner handle.
        for thread in started:
            for _ in range(100):
                loop.quit()
                thread.join(0.02)
                if not thread.is_alive():
                    break
            assert not thread.is_alive()


def test_blocking_run_uses_only_the_owned_worker(
    lifecycle: tuple[DBusSignalListener, FakeLoop],
) -> None:
    listener, loop = lifecycle
    errors: list[BaseException] = []

    def run() -> None:
        try:
            listener.run()
        except BaseException as error:
            errors.append(error)

    caller = threading.Thread(target=run, daemon=True)
    caller.start()
    try:
        assert loop.running.wait(1)
        assert caller.is_alive()
        listener.stop()
    finally:
        caller.join(1)
    assert not caller.is_alive()
    assert not errors
    assert loop.run_calls == 1
