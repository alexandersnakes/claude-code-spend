"""Tests for agent_spend.py.

Every test builds its own small corpus of .jsonl files in a temporary directory
and points the counter at it with --root, so nothing here reads the transcripts
of the machine it runs on. That matters twice over: those transcripts differ on
every machine, and a test that depends on them cannot be reproduced by anyone,
CI included.

The numbers below are written out as arithmetic on the published rates rather
than as constants, so a wrong assertion is visible in the test itself.
"""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_spend  # noqa: E402  (path has to be set before the import)

SCRIPT = pathlib.Path(agent_spend.__file__)
MILLION = 1e6
DAY = date(2026, 8, 10)          # any fixed day; the window is always explicit


def stamp_inside_the_day(hour=12):
    """A UTC timestamp that falls inside local DAY on any machine.

    Local noon is inside the local day in every timezone, so converting it is
    safe where hardcoding "T12:00:00Z" would not be.
    """
    local = datetime(DAY.year, DAY.month, DAY.day, hour)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"


def assistant(stamp, model="claude-opus-4-1", inp=0, out=0,
              write_5m=0, write_1h=0, read=0, request_id=None):
    """One assistant record - one step, in this counter's terms."""
    record = {
        "type": "assistant",
        "timestamp": stamp,
        "message": {
            "model": model,
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_creation_input_tokens": write_5m + write_1h,
                "cache_creation": {"ephemeral_1h_input_tokens": write_1h,
                                   "ephemeral_5m_input_tokens": write_5m},
                "cache_read_input_tokens": read,
            },
        },
    }
    if request_id:
        record["requestId"] = request_id
    return record


def tool_result(stamp, body):
    return {"type": "user", "timestamp": stamp,
            "message": {"content": [{"type": "tool_result", "content": body}]}}


BODY_400_CHARS = "x" * 398       # json.dumps adds the two quotes -> 400 -> 100 tokens
BODY_TOKENS = 100.0


class CorpusCase(unittest.TestCase):
    """Base: a throwaway corpus, and one call that runs the counter over it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.project = self.root / "a-project"
        self.project.mkdir()

    def session(self, name, records, subagent=False):
        folder = self.project / "subagents" if subagent else self.project
        folder.mkdir(exist_ok=True)
        path = folder / (name + ".jsonl")
        with path.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path

    def count(self, day_from=DAY, day_to=DAY):
        return agent_spend.run(self.root, day_from, day_to)


class Pricing(CorpusCase):

    def test_one_step_costs_the_five_published_rates(self):
        self.session("session-1", [assistant(stamp_inside_the_day(), inp=1000,
                                             out=1000, write_5m=1000,
                                             write_1h=1000, read=1000)])
        metrics = self.count()
        self.assertEqual(metrics["steps_main"], 1)
        expected = (1000 * 5.00 + 1000 * 25.00 + 1000 * 6.25
                    + 1000 * 10.00 + 1000 * 0.50) / MILLION
        self.assertAlmostEqual(metrics["cost_total"], expected, places=9)
        self.assertAlmostEqual(metrics["cost_total"], 0.04675, places=9)

    def test_the_hour_cache_costs_exactly_its_extra_rate(self):
        tokens = 100000
        path = self.session("session-1", [assistant(stamp_inside_the_day(),
                                                    write_5m=tokens)])
        cheap = self.count()["cost_total"]
        path.unlink()
        self.session("session-1", [assistant(stamp_inside_the_day(),
                                             write_1h=tokens)])
        pricey = self.count()["cost_total"]
        self.assertAlmostEqual(pricey - cheap,
                               (10.00 - 6.25) * tokens / MILLION, places=9)

    def test_an_unknown_model_is_billed_at_the_opus_rate(self):
        model = "some-model-that-does-not-exist-yet"
        self.session("session-1", [assistant(stamp_inside_the_day(),
                                             model=model, out=1000)])
        self.assertAlmostEqual(self.count()["cost_total"],
                               1000 * 25.00 / MILLION, places=9)
        self.assertEqual(agent_spend.price_family(model), "opus")

    def test_synthetic_records_are_steps_but_cost_nothing(self):
        self.session("session-1", [assistant(stamp_inside_the_day(),
                                             model="<synthetic>",
                                             inp=1000, out=1000)])
        metrics = self.count()
        self.assertEqual(metrics["steps_main"], 1)
        self.assertEqual(metrics["cost_total"], 0.0)

    def test_the_headline_cache_number_wins_over_a_bigger_breakdown(self):
        # A small share of real records disagree with themselves: the breakdown
        # claims more 1-hour tokens than the headline has in total. The headline
        # is the one that gets billed, so it is the one that decides.
        record = assistant(stamp_inside_the_day())
        record["message"]["usage"]["cache_creation_input_tokens"] = 1000
        record["message"]["usage"]["cache_creation"] = {
            "ephemeral_1h_input_tokens": 4000}
        self.session("session-1", [record])
        self.assertAlmostEqual(self.count()["cost_total"],
                               1000 * 10.00 / MILLION, places=9)


class Counting(CorpusCase):

    def test_a_subagent_step_is_not_a_main_session_step(self):
        self.session("session-1", [assistant(stamp_inside_the_day(), out=10)])
        self.session("scout", [assistant(stamp_inside_the_day(), out=10)],
                     subagent=True)
        metrics = self.count()
        self.assertEqual(metrics["steps_main"], 1)
        self.assertEqual(metrics["steps_side"], 1)
        self.assertAlmostEqual(metrics["side_share_steps"], 50.0, places=9)

    def test_both_records_of_one_api_call_count_as_steps(self):
        # One API call writes several assistant records, and this counter bills
        # each of them. It makes the step count run above the number of calls;
        # it is deliberate, and it is pinned here so it cannot drift quietly.
        stamp = stamp_inside_the_day()
        self.session("session-1", [
            assistant(stamp, out=1000, request_id="req_017"),
            assistant(stamp, out=1000, request_id="req_017"),
        ])
        metrics = self.count()
        self.assertEqual(metrics["steps_main"], 2)
        self.assertAlmostEqual(metrics["cost_total"],
                               2 * 1000 * 25.00 / MILLION, places=9)


class Amortization(CorpusCase):

    def test_a_result_read_early_is_paid_for_on_every_later_step(self):
        stamp = stamp_inside_the_day()
        records = [assistant(stamp, out=1), tool_result(stamp, BODY_400_CHARS)]
        records += [assistant(stamp, out=1) for _ in range(10)]
        self.session("session-1", records)
        metrics = self.count()
        self.assertEqual(metrics["steps_main"], 11)
        self.assertEqual(metrics["tool_results"], 1)
        self.assertEqual(metrics["tool_result_tokens"], BODY_TOKENS)
        self.assertEqual(metrics["top_read_steps_ahead"], 10)
        self.assertAlmostEqual(metrics["tool_result_cost"],
                               BODY_TOKENS * 10 * 0.50 / MILLION, places=12)

    def test_a_result_inside_a_subagent_is_not_charged_to_the_main_context(self):
        stamp = stamp_inside_the_day()
        self.session("scout", [assistant(stamp, out=1),
                               tool_result(stamp, BODY_400_CHARS),
                               assistant(stamp, out=1)], subagent=True)
        metrics = self.count()
        self.assertEqual(metrics["steps_side"], 2)
        self.assertEqual(metrics["tool_results"], 0)
        self.assertEqual(metrics["tool_result_cost"], 0.0)


class Window(CorpusCase):

    def test_the_day_is_the_local_one_not_the_utc_one(self):
        if datetime.now().astimezone().utcoffset() == timedelta(0):
            self.skipTest("machine runs on UTC, where the two days coincide")
        low, _ = agent_spend.window(DAY, DAY)
        self.assertNotEqual(low, DAY.isoformat() + "T00:00:00")
        one_second_earlier = (datetime.strptime(low, "%Y-%m-%dT%H:%M:%S")
                              - timedelta(seconds=1))
        self.session("inside", [assistant(low + ".000Z", out=1000)])
        self.session("outside", [assistant(
            one_second_earlier.strftime("%Y-%m-%dT%H:%M:%S") + ".000Z", out=1000)])
        self.assertEqual(self.count()["steps_main"], 1)


class CommandLine(CorpusCase):

    def test_an_empty_period_prints_zeros_and_exits_clean(self):
        empty = self.root / "no-transcripts-here"
        empty.mkdir()
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--from", "2026-08-01",
             "--to", "2026-08-02", "--root", str(empty), "--json"],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        metrics = json.loads(done.stdout)["metrics"]
        self.assertEqual(metrics["steps_main"], 0)
        self.assertEqual(metrics["cost_total"], 0)
        self.assertEqual(metrics["sessions"], 0)
        self.assertEqual(metrics["reread_multiplier"], 0)

    def test_a_backwards_period_is_refused_not_guessed_at(self):
        done = subprocess.run(
            [sys.executable, str(SCRIPT), "--from", "2026-08-10",
             "--to", "2026-08-01", "--root", str(self.root)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 1)
        self.assertIn("earlier", done.stderr)


if __name__ == "__main__":
    unittest.main()
