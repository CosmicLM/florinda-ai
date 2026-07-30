"""morning_briefing.py — delivers a short, spoken briefing once per calendar
day: a greeting, today's real tasks (from the user's own executive-engine
data), and a relevant quantum computing news item.

WHY gated on the calendar date, not "did the service just start": the
always-on service can restart many times in one day (a crash, a config
tweak, ...) — gating on today's date is what actually means "the first time
you're at the computer today," not "every time this process happens to launch."

WHY tasks come from a real file, not invented: observed live elsewhere in
this project — a task was once written to a made-up "dummy_journal" file
instead of the real one. This reads the ACTUAL obligations.json Florinda
already knows about (see INSTRUCTION.md's "Working With An App's Real
Data") rather than guessing at what the user's day looks like.

WHY this is a plain code template, not a model call: the only inputs are a
clock (time of day), a real task list, and the top web search result — none
of that needs judgment to phrase, and a template can't invent a task or
news item that isn't there the way an LLM composing "freely" always has
some chance of. This removes the morning briefing's only dependency on the
local model being up/warm/fast, at the cost of a slightly more repetitive,
less varied greeting each day — an acceptable trade for something that only
needs to run once a day and state real facts correctly every single time.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import web_search

logger = logging.getLogger(__name__)

OBLIGATIONS_PATH = Path.home() / ".executive_engine/library/obligations.json"
MARKER_PATH = Path.home() / ".local/share/flora-ai/morning_briefing.json"


class MorningBriefing:
    """Composes and delivers one greeting+tasks+news briefing per calendar day."""

    def __init__(
        self,
        on_report: Callable[[str], bool],
        web_search_host: str,
        marker_path: Path = MARKER_PATH,
        obligations_path: Path = OBLIGATIONS_PATH,
    ) -> None:
        self._on_report = on_report
        self._web_search_host = web_search_host
        self._marker_path = marker_path
        self._obligations_path = obligations_path
        self._pending_briefing: Optional[str] = None

    def poll_once(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self._already_delivered(today):
            return
        if self._pending_briefing is None:
            self._pending_briefing = self._compose()
        if self._on_report(self._pending_briefing):
            self._pending_briefing = None
            self._mark_delivered(today)

    def _compose(self) -> str:
        hour = datetime.now().hour
        time_of_day = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        parts = [f"Good {time_of_day}!", _tasks_sentence(self._read_tasks())]
        news_sentence = _news_sentence(self._fetch_quantum_news())
        if news_sentence:
            parts.append(news_sentence)
        return " ".join(parts)

    def _read_tasks(self) -> list[str]:
        try:
            data = json.loads(self._obligations_path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        return data.get("config", {}).get("items", [])

    def _fetch_quantum_news(self) -> list[dict]:
        try:
            return web_search.search("quantum computing news", max_results=3, host=self._web_search_host)
        except web_search.WebSearchError:
            logger.exception("web search failed while composing the morning briefing")
            return []

    def _already_delivered(self, today: str) -> bool:
        try:
            return json.loads(self._marker_path.read_text()).get("last_delivered_date") == today
        except (OSError, json.JSONDecodeError):
            return False

    def _mark_delivered(self, today: str) -> None:
        self._marker_path.parent.mkdir(parents=True, exist_ok=True)
        self._marker_path.write_text(json.dumps({"last_delivered_date": today}))


def _tasks_sentence(items: list[str]) -> str:
    if not items:
        return "You have nothing scheduled today."
    if len(items) == 1:
        return f"Today you have one thing on your plate: {items[0]}."
    listed = ", ".join(items[:-1]) + f", and {items[-1]}"
    return f"Today you have {len(items)} things on your plate: {listed}."


def _news_sentence(results: list[dict]) -> str:
    if not results:
        return ""
    title = results[0].get("title", "").strip()
    if not title:
        return ""
    return f"In quantum computing news: {title}."


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        marker = Path(tmp_dir) / "marker.json"
        obligations = Path(tmp_dir) / "obligations.json"
        obligations.write_text(json.dumps({"config": {"items": ["Finish report", "Call dentist"]}}))

        reports: list[str] = []
        delivery_allowed = [False]

        def on_report(text: str) -> bool:
            if not delivery_allowed[0]:
                return False
            reports.append(text)
            return True

        briefing = MorningBriefing(
            on_report=on_report, web_search_host="http://127.0.0.1:1",
            marker_path=marker, obligations_path=obligations,
        )
        briefing.poll_once()
        assert reports == [], "should not deliver while refused"
        assert not marker.exists(), "must not mark delivered until actually delivered"
        print("OK: undelivered briefing is neither lost nor marked")

        delivery_allowed[0] = True
        briefing.poll_once()
        assert len(reports) == 1, reports
        assert any(word in reports[0].lower() for word in ("morning", "afternoon", "evening")), reports
        assert "Finish report" in reports[0] and "Call dentist" in reports[0], reports
        assert marker.exists()
        print("OK: retried delivery succeeds, reports the REAL tasks, then marks today as done")

        briefing.poll_once()
        assert len(reports) == 1, "should not deliver a second time the same day"
        print("OK: no second briefing once delivered today")

        assert _tasks_sentence([]) == "You have nothing scheduled today."
        assert _tasks_sentence(["Call dentist"]) == "Today you have one thing on your plate: Call dentist."
        assert _tasks_sentence(["A", "B", "C"]) == "Today you have 3 things on your plate: A, B, and C."
        print("OK: task sentence composes correctly for 0/1/many real tasks")

        assert _news_sentence([]) == ""
        assert _news_sentence([{"title": "Big quantum breakthrough"}]) == "In quantum computing news: Big quantum breakthrough."
        print("OK: news sentence uses the real top result, or says nothing if none found")

        print("MorningBriefing self-check OK")
