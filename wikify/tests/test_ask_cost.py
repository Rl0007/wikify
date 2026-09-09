# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
import threading
import time

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.rag import history, usage

ROUTE_USAGE = {"cost": 0.000021, "prompt_tokens": 900, "completion_tokens": 60}
RERANK_USAGE = {"cost": 0.002064, "prompt_tokens": 7800, "completion_tokens": 40}
SYNTHESIS_COST = 0.030516
SYNTHESIS_TOKENS = {"prompt_tokens": 8187, "completion_tokens": 397}
ASK_COST = ROUTE_USAGE["cost"] + RERANK_USAGE["cost"] + SYNTHESIS_COST


class FakeUsage:
	def __init__(self, prompt_tokens: int, completion_tokens: int):
		self.prompt_tokens = prompt_tokens
		self.completion_tokens = completion_tokens


def synthesise(call_id: str, cost: float | None = SYNTHESIS_COST, *, delay: float = 0.05) -> None:
	spend = usage.LitellmSpend()
	spend.log_pre_api_call("openrouter/anthropic/claude-sonnet-4.6", [], {"litellm_call_id": call_id})
	priced = threading.Thread(
		target=price_later, args=(spend, call_id, cost, delay), name=f"litellm-{call_id}"
	)
	priced.start()
	usage.add(FakeUsage(**SYNTHESIS_TOKENS))
	priced.join()


def price_later(spend, call_id: str, cost: float | None, delay: float) -> None:
	time.sleep(delay)
	if cost is None:
		spend.log_failure_event({"litellm_call_id": call_id}, None, None, None)
	else:
		spend.log_success_event({"litellm_call_id": call_id, "response_cost": cost}, None, None, None)


def ask_with_known_costs(call_id: str = "call-1") -> dict:
	with usage.collect() as spend:
		usage.add(ROUTE_USAGE)
		usage.add(RERANK_USAGE)
		synthesise(call_id)
		return dict(spend)


class TestAskCost(FrappeTestCase):
	def test_an_ask_costs_the_sum_of_the_calls_it_made(self):
		spend = ask_with_known_costs()

		self.assertAlmostEqual(spend["cost"], ASK_COST)
		self.assertEqual(
			spend["prompt_tokens"],
			ROUTE_USAGE["prompt_tokens"] + RERANK_USAGE["prompt_tokens"] + SYNTHESIS_TOKENS["prompt_tokens"],
		)

	def test_a_repeated_price_is_billed_once(self):
		spend_logger = usage.LitellmSpend()
		with usage.collect() as spend:
			spend_logger.log_pre_api_call("model", [], {"litellm_call_id": "call-repeat"})
			for _report in range(3):
				spend_logger.log_success_event(
					{"litellm_call_id": "call-repeat", "response_cost": SYNTHESIS_COST}, None, None, None
				)
			total = dict(spend)

		self.assertAlmostEqual(total["cost"], SYNTHESIS_COST)

	def test_a_failed_synthesis_costs_nothing_and_does_not_hang(self):
		started = time.monotonic()
		with usage.collect() as spend:
			synthesise("call-failed", cost=None)
			total = dict(spend)

		self.assertEqual(total["cost"], 0.0)
		self.assertLess(time.monotonic() - started, usage.SPEND_GRACE_SECONDS)

	def test_one_question_is_never_billed_for_another(self):
		reported: dict[str, float] = {}

		def answer(name: str, call_id: str, cost: float):
			with usage.collect() as spend:
				synthesise(call_id, cost)
				reported[name] = spend["cost"]

		threads = [
			threading.Thread(target=answer, args=("first", "call-first", 0.5)),
			threading.Thread(target=answer, args=("second", "call-second", 0.25)),
		]
		for thread in threads:
			thread.start()
		for thread in threads:
			thread.join()

		self.assertEqual(reported, {"first": 0.5, "second": 0.25})

	def test_a_completion_outside_an_ask_is_billed_to_nobody(self):
		spend_logger = usage.LitellmSpend()
		spend_logger.log_pre_api_call("model", [], {"litellm_call_id": "call-agent"})
		spend_logger.log_success_event(
			{"litellm_call_id": "call-agent", "response_cost": 1.0}, None, None, None
		)

		with usage.collect() as spend:
			self.assertEqual(spend, usage.empty())


class TestAskCostReachesTheLog(FrappeTestCase):
	def test_the_session_total_is_the_sum_of_its_turns(self):
		spend = ask_with_known_costs()
		result = {
			"answer": "Health and education cess is 4% [1].",
			"citations": [],
			"route": {"intent": "semantic", "query": "health and education cess"},
			"refused": False,
			**spend,
		}

		session = history.record_turn(None, "What is the health and education cess?", result)
		history.record_turn(session, "How is it computed?", result)

		messages = history.get_session(session)["messages"]
		conversation = frappe.get_doc("Wikify Ask Session", session)
		self.assertAlmostEqual(messages[1]["cost"], ASK_COST, places=6)
		self.assertAlmostEqual(conversation.total_cost, sum(row["cost"] for row in messages))
		self.assertAlmostEqual(conversation.total_cost, ASK_COST * 2, places=6)
