"""Tests for the global record of which conversations are working.

The sidebar shows this on every page, so the signal has to be independent of
whichever conversation happens to be open -- that coupling is what made the
indicator vanish when the user switched tabs mid-turn.
"""

import asyncio

from backend.app.agent.activity import ActivityTracker


class TestRunningIds:
    def test_starts_empty(self):
        assert ActivityTracker().running_ids() == []

    def test_reports_running_conversations_in_a_stable_order(self):
        tracker = ActivityTracker()
        tracker.set_running("b")
        tracker.set_running("a")
        assert tracker.running_ids() == ["a", "b"]

    def test_finished_conversations_are_dropped(self):
        tracker = ActivityTracker()
        tracker.set_running("a")
        tracker.set_running("b")
        tracker.set_idle("a")
        assert tracker.running_ids() == ["b"]


class TestBroadcasting:
    def test_subscribers_are_told_when_a_turn_starts_and_ends(self):
        tracker = ActivityTracker()
        queue = tracker.subscribe()

        tracker.set_running("a")
        tracker.set_idle("a")

        assert queue.get_nowait() == {
            "type": "conversation_activity",
            "conversation_id": "a",
            "running": True,
        }
        assert queue.get_nowait() == {
            "type": "conversation_activity",
            "conversation_id": "a",
            "running": False,
        }

    def test_repeated_states_are_not_re_announced(self):
        # The engine can report the same state more than once; a client must not
        # see churn for something that did not change.
        tracker = ActivityTracker()
        queue = tracker.subscribe()

        tracker.set_running("a")
        tracker.set_running("a")
        tracker.set_idle("a")
        tracker.set_idle("a")

        assert queue.qsize() == 2

    def test_every_subscriber_receives_the_event(self):
        tracker = ActivityTracker()
        first, second = tracker.subscribe(), tracker.subscribe()

        tracker.set_running("a")

        assert first.get_nowait()["conversation_id"] == "a"
        assert second.get_nowait()["conversation_id"] == "a"

    def test_unsubscribing_stops_delivery(self):
        tracker = ActivityTracker()
        queue = tracker.subscribe()
        tracker.unsubscribe(queue)

        tracker.set_running("a")

        assert queue.empty()

    def test_a_listener_that_stops_draining_is_dropped(self):
        tracker = ActivityTracker()
        queue = tracker.subscribe()
        # Fill the queue past its ceiling with distinct conversations.
        for index in range(200):
            tracker.set_running(f"conv-{index}")

        # The stalled listener is discarded rather than growing without bound,
        # and the tracker itself stays correct for everyone else.
        assert queue.full()
        assert len(tracker.running_ids()) == 200

    def test_state_survives_a_listener_disconnecting_mid_turn(self):
        tracker = ActivityTracker()
        queue = tracker.subscribe()
        tracker.set_running("a")
        tracker.unsubscribe(queue)

        # A reconnecting client is caught up from the snapshot, not the events.
        assert tracker.running_ids() == ["a"]


class TestReset:
    def test_reset_clears_everything(self):
        tracker = ActivityTracker()
        tracker.set_running("a")
        tracker.reset()
        assert tracker.running_ids() == []
