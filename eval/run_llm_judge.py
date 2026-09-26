"""LLM-as-a-Judge for Yusor Agent evaluation.

Reads:
    eval/cases/*.jsonl
    eval/results/eval-*.json

Evaluates all agent targets:
    HR, Consultant, Manager, Orchestrator

Outputs:
    eval/results/llm-judge-YYYYMMDD-HHMMSS.json
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "eval" / "cases"
RESULTS_DIR = ROOT / "eval" / "results"

load_dotenv(ROOT / ".env")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_cases() -> dict[str, dict]:
    cases = {}

    for path in sorted(CASES_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()

            if not line or line.startswith("//"):
                continue

            case = json.loads(line)
            cases[case["id"]] = case

    return cases


def find_latest_eval() -> Path:
    files = sorted(RESULTS_DIR.glob("eval-*.json"))

    if not files:
        raise FileNotFoundError(
            "No evaluation result found in eval/results/. "
            "Run `python -m eval.run_eval` first."
        )

    return files[-1]


# ---------------------------------------------------------------------------
# Judge prompt
# ---------------------------------------------------------------------------

def build_prompt(case: dict, result: dict) -> str:
    target = case.get("target", "unknown")
    query = case.get("query", "")
    expect = case.get("expect", {})
    output = result.get("output", {})

    return f"""
You are an evaluator for an Agentic HR system called Yusor.

Evaluate the output of ONE agent for ONE test case.

Agent:
{target}

User query:
{query}

Expected behavior / evaluation requirements:
{json.dumps(expect, ensure_ascii=False, indent=2)}

Actual agent output:
{json.dumps(output, ensure_ascii=False, indent=2)}

Evaluate the actual output using these four criteria.

1. Correctness
Is the answer/decision factually correct and consistent with the expected
behavior?

2. Relevance
Does the output directly address the user's request without unnecessary
information?

3. Groundedness
Is the output supported by the provided evidence, sources, facts, or
other information available in the actual output?

4. Completeness
Does the output cover the important requirements of this case?

Score each criterion from 1 to 5:

1 = Very poor
2 = Poor
3 = Acceptable
4 = Good
5 = Excellent

Important:
- Do not judge based only on whether the rule-based evaluator passed.
- Evaluate the actual quality of the agent output.
- Do not invent facts that are not present in the case or output.
- For HR, pay attention to whether the returned facts/actions match the
  requested topic.
- For Consultant, pay attention to legal/policy reasoning, conflicts,
  recommendations, and citations.
- For Manager, pay attention to the decision, reasons, evidence, and
  whether the decision follows the provided information.
- For Orchestrator, pay attention to coordination, final response,
  status, and consistency with the available agent results.
- Keep the reason short and specific.

Return ONLY valid JSON in exactly this structure:

{{
  "correctness": 1,
  "relevance": 1,
  "groundedness": 1,
  "completeness": 1,
  "reason": "Short explanation."
}}
"""


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def judge_case(client: OpenAI, model: str, case: dict, result: dict) -> dict:
    prompt = build_prompt(case, result)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a strict but fair evaluator. "
                    "Return only valid JSON."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content

    if not content:
        raise ValueError("Judge returned an empty response.")

    scores = json.loads(content)

    required = [
        "correctness",
        "relevance",
        "groundedness",
        "completeness",
        "reason",
    ]

    for key in required:
        if key not in scores:
            raise ValueError(f"Judge response missing field: {key}")

    for key in required[:4]:
        value = scores[key]

        if not isinstance(value, (int, float)) or not 1 <= value <= 5:
            raise ValueError(
                f"Invalid {key} score: {value!r}. Expected 1-5."
            )

    return scores


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run LLM-as-a-Judge on Yusor evaluation results."
    )

    parser.add_argument(
        "--input",
        help="Path to an eval result JSON. Defaults to latest eval result.",
    )

    parser.add_argument(
        "--model",
        default=os.getenv("LLM_MODEL"),
        help="LLM model used as judge.",
    )

    parser.add_argument(
        "--id",
        action="append",
        help="Evaluate only these case IDs. Can be repeated.",
    )

    args = parser.parse_args()

    if not args.model:
        raise SystemExit(
            "No judge model configured. Set LLM_MODEL in .env "
            "or pass --model."
        )

    input_path = Path(args.input) if args.input else find_latest_eval()

    if not input_path.is_absolute():
        input_path = ROOT / input_path

    print(f"Input: {input_path.relative_to(ROOT)}")
    print(f"Judge model: {args.model}")

    cases = load_cases()

    eval_results = json.loads(
        input_path.read_text(encoding="utf-8")
    )

    if args.id:
        selected = set(args.id)
        eval_results = [
            r for r in eval_results
            if r.get("id") in selected
        ]

    if not eval_results:
        raise SystemExit("No evaluation cases selected.")

    base_url = os.getenv("LLM_BASE_URL")
    api_key = os.getenv("LLM_API_KEY")

    if not api_key:
        raise SystemExit(
            "LLM_API_KEY is not configured."
        )

    client_kwargs = {
        "api_key": api_key,
    }

    if base_url:
        client_kwargs["base_url"] = base_url

    client = OpenAI(**client_kwargs)

    judged = []

    for result in eval_results:
        case_id = result["id"]
        case = cases.get(case_id)

        if not case:
            print(f"{case_id}: SKIP (case not found)")
            continue

        print(f"Judging {case_id} ({case['target']})...", end=" ")

        try:
            scores = judge_case(
                client,
                args.model,
                case,
                result,
            )

            judged.append(
                {
                    "id": case_id,
                    "target": case["target"],
                    "query": case.get("query", ""),
                    "rule_based_outcome": result.get("outcome"),
                    "scores": scores,
                }
            )

            average = sum(
                scores[k]
                for k in (
                    "correctness",
                    "relevance",
                    "groundedness",
                    "completeness",
                )
            ) / 4

            print(f"{average:.2f}/5")

        except Exception as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}")

            judged.append(
                {
                    "id": case_id,
                    "target": case["target"],
                    "query": case.get("query", ""),
                    "rule_based_outcome": result.get("outcome"),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    output_path = (
        RESULTS_DIR
        / f"llm-judge-{datetime.now():%Y%m%d-%H%M%S}.json"
    )

    output_path.write_text(
        json.dumps(judged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\nSaved: {output_path.relative_to(ROOT)}")

    successful = [
        r for r in judged
        if "scores" in r
    ]

    if successful:
        print("\nAverage scores")

        for key in (
            "correctness",
            "relevance",
            "groundedness",
            "completeness",
        ):
            values = [
                r["scores"][key]
                for r in successful
            ]

            avg = sum(values) / len(values)

            print(f"  {key:<15} {avg:.2f}/5")

        overall_values = [
            sum(
                r["scores"][k]
                for k in (
                    "correctness",
                    "relevance",
                    "groundedness",
                    "completeness",
                )
            ) / 4
            for r in successful
        ]

        print(
            f"  {'overall':<15} "
            f"{sum(overall_values) / len(overall_values):.2f}/5"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
