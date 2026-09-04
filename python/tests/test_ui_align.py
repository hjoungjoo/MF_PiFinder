from __future__ import annotations

import queue

import PiFinder.i18n  # noqa: F401  (installs the built-in _ translator)
import pytest

from PiFinder.types.positioning import AlignCancel, AlignOnRaDec, AlignedResult
from PiFinder.ui import align


class _Config:
    def __init__(self) -> None:
        self.saved = None

    def set_option(self, name, value) -> None:
        self.saved = (name, value)


class _SharedState:
    def __init__(self) -> None:
        self.target_pixel = None

    def set_target_pixel(self, value) -> None:
        self.target_pixel = value


class _ResponseQueue:
    """Empty while stale replies are drained, then return the live reply."""

    def __init__(self, response=None) -> None:
        self.response = response
        self.calls = []

    def get(self, block=True, timeout=None):
        self.calls.append((block, timeout))
        if block is False or self.response is None:
            raise queue.Empty
        return self.response


def _queues(response_queue):
    return {
        "align_command": queue.Queue(),
        "align_response": response_queue,
        "console": queue.Queue(),
    }


def test_align_waits_for_solver_response_without_polling():
    response_queue = _ResponseQueue(AlignedResult(y_target=123.0, x_target=234.0))
    command_queues = _queues(response_queue)
    config = _Config()
    shared_state = _SharedState()

    assert align.align_on_radec(12.3, 45.6, command_queues, config, shared_state)

    assert response_queue.calls == [
        (False, None),
        (True, align.ALIGN_TIMEOUT_SECONDS),
    ]
    assert command_queues["align_command"].get_nowait() == AlignOnRaDec(
        ra=12.3, dec=45.6
    )
    assert command_queues["console"].get_nowait() == "Alignment Set"
    assert shared_state.target_pixel == (123.0, 234.0)
    assert config.saved == ("target_pixel", (123.0, 234.0))


def test_align_cancels_after_solver_timeout(monkeypatch):
    monkeypatch.setattr(align, "ALIGN_TIMEOUT_SECONDS", 0.25)
    response_queue = _ResponseQueue()
    command_queues = _queues(response_queue)
    config = _Config()
    shared_state = _SharedState()

    assert not align.align_on_radec(12.3, 45.6, command_queues, config, shared_state)

    assert response_queue.calls == [(False, None), (True, 0.25)]
    assert command_queues["align_command"].get_nowait() == AlignOnRaDec(
        ra=12.3, dec=45.6
    )
    assert command_queues["align_command"].get_nowait() == AlignCancel()
    assert command_queues["console"].get_nowait() == "Align Timeout"
    assert shared_state.target_pixel is None
    assert config.saved is None


@pytest.mark.parametrize("target", [(-1.0, -1.0), (-1.0, 25.0)])
def test_align_rejects_solver_failure_sentinel(target):
    response_queue = _ResponseQueue(
        AlignedResult(y_target=target[0], x_target=target[1])
    )
    command_queues = _queues(response_queue)
    config = _Config()
    shared_state = _SharedState()

    assert not align.align_on_radec(12.3, 45.6, command_queues, config, shared_state)
    assert shared_state.target_pixel is None
    assert config.saved is None
