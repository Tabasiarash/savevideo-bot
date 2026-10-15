from __future__ import annotations

from bot.services.breaker import Breaker


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_opens_only_after_threshold():
    br = Breaker(threshold=3, cooldown=100, clock=FakeClock())
    br.record_failure("youtube")
    br.record_failure("youtube")
    assert br.is_open("youtube") is False
    br.record_failure("youtube")
    assert br.is_open("youtube") is True
    assert br.failures("youtube") == 3


def test_keys_are_independent():
    br = Breaker(threshold=1, cooldown=100, clock=FakeClock())
    br.record_failure("youtube")
    assert br.is_open("youtube") is True
    assert br.is_open("soundcloud") is False


def test_success_resets():
    br = Breaker(threshold=2, cooldown=100, clock=FakeClock())
    br.record_failure("x")
    br.record_success("x")
    assert br.failures("x") == 0
    br.record_failure("x")
    assert br.is_open("x") is False


def test_cooldown_half_open_after_elapsed():
    clock = FakeClock()
    br = Breaker(threshold=1, cooldown=30, clock=clock)
    br.record_failure("s")
    assert br.is_open("s") is True
    clock.advance(30)
    assert br.is_open("s") is False
    # After the trial the counter restarts, so one more failure re-opens it.
    br.record_failure("s")
    assert br.is_open("s") is True


def test_reset_and_unknown_key():
    br = Breaker(threshold=1, cooldown=100, clock=FakeClock())
    br.record_failure("a")
    br.reset()
    assert br.is_open("a") is False
    assert br.failures("never-seen") == 0
