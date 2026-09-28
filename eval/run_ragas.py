import asyncio
import json
import argparse

from openai import AsyncOpenAI
from openai import RateLimitError

from ragas.llms import llm_factory
from ragas.embeddings import HuggingFaceEmbeddings
from ragas.metrics.collections import (
    Faithfulness,
    ContextPrecision,
    ContextRecall,
    AnswerRelevancy,
)

from app.config import get_settings


RESULTS_PATH = "eval/golden/consultant_rag_results.jsonl"
OUTPUT_PATH = "eval/golden/ragas_results.jsonl"

METRICS = [
    "faithfulness",
    "context_precision",
    "context_recall",
    "answer_relevancy",
]

# Per-metric timeout in seconds (--timeout); 120 s left some cases null.
TIMEOUT = 300


def load_cases():
    with open(RESULTS_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_existing_results():
    existing = {}

    try:
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    result = json.loads(line)
                    existing[result["id"]] = result
    except FileNotFoundError:
        pass

    return existing


def is_complete(result):
    if not result:
        return False

    return all(result.get(metric) is not None for metric in METRICS)


def id_scores(case):
    """Source-id recall and precision, next to the LLM-judged Ragas ones.

    Ragas judges recall against the reference answer's wording; these check
    whether the reference sources were actually retrieved. A retrieved WPS
    section counts for every rule id inside it (retrieved_member_ids).
    """
    reference = set(case.get("reference_source_ids") or [])
    chunks = case.get("retrieved_member_ids") or [
        [source_id] for source_id in case.get("retrieved_source_ids") or []
    ]

    if not reference or not chunks:
        return {"id_recall": None, "id_precision": None}

    retrieved = {source_id for chunk in chunks for source_id in chunk}
    relevant_chunks = sum(1 for chunk in chunks if reference & set(chunk))

    return {
        "id_recall": len(reference & retrieved) / len(reference),
        "id_precision": relevant_chunks / len(chunks),
    }


def print_summary(results):
    print("\nSummary (mean over scored cases):")

    for metric in [*METRICS, "id_recall", "id_precision"]:
        values = [
            r[metric] for r in results if r.get(metric) is not None
        ]
        missing = len(results) - len(values)
        mean = sum(values) / len(values) if values else float("nan")
        print(
            f"  {metric:18} {mean:.3f}"
            f"  (n={len(values)}, missing={missing})"
        )


async def safe_score(name, metric_call):
    try:
        result = await asyncio.wait_for(
            metric_call(),
            timeout=TIMEOUT,
        )

        value = result.value

        print(f"  {name}: {value}")
        return value

    except asyncio.TimeoutError:
        print(f"  {name}: TIMEOUT")
        return None

    except RateLimitError:
        print(f"  {name}: RATE LIMIT")
        raise

    except Exception as e:
        print(
            f"  {name}: ERROR - "
            f"{type(e).__name__}: {e}"
        )
        return None


async def evaluate_case(
    case,
    faithfulness,
    context_precision,
    context_recall,
    answer_relevancy,
):
    print(f"\nEvaluating {case['id']}...")

    faithfulness_result = await safe_score(
        "Faithfulness",
        lambda: faithfulness.ascore(
            user_input=case["question"],
            response=case["answer"],
            retrieved_contexts=case["contexts"],
        ),
    )

    context_precision_result = await safe_score(
        "Context Precision",
        lambda: context_precision.ascore(
            user_input=case["question"],
            retrieved_contexts=case["contexts"],
            reference=case["reference_answer"],
        ),
    )

    context_recall_result = await safe_score(
        "Context Recall",
        lambda: context_recall.ascore(
            user_input=case["question"],
            retrieved_contexts=case["contexts"],
            reference=case["reference_answer"],
        ),
    )

    answer_relevancy_result = await safe_score(
        "Answer Relevancy",
        lambda: answer_relevancy.ascore(
            user_input=case["question"],
            response=case["answer"],
        ),
    )

    result = {
        "id": case["id"],
        "faithfulness": faithfulness_result,
        "context_precision": context_precision_result,
        "context_recall": context_recall_result,
        "answer_relevancy": answer_relevancy_result,
        **id_scores(case),
        "retrieved_source_ids": case["retrieved_source_ids"],
        "reference_source_ids": case["reference_source_ids"],
    }

    return result


async def main():
    global TIMEOUT

    print("MAIN START")

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--id",
        action="append",
        dest="ids",
        help="Start from this case ID or evaluate only the specified IDs.",
    )

    parser.add_argument(
        "--only",
        action="append",
        dest="only_ids",
        help="Score only these case IDs (repeatable), even if already scored.",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=TIMEOUT,
        help=f"Seconds per metric call before it counts as missing (default {TIMEOUT}).",
    )

    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore saved scores and re-score every case (after rebuilding the dataset).",
    )

    args = parser.parse_args()

    TIMEOUT = args.timeout

    settings = get_settings()

    cases = load_cases()
    existing_results = {} if args.fresh else load_existing_results()
    all_cases = cases

    if args.only_ids:
        cases = [case for case in cases if case["id"] in set(args.only_ids)]
        for case in cases:
            existing_results.pop(case["id"], None)

    # If --id is provided, start from the first requested ID
    if args.ids:
        requested_ids = set(args.ids)

        first_index = next(
            (
                i
                for i, case in enumerate(cases)
                if case["id"] in requested_ids
            ),
            None,
        )

        if first_index is None:
            print(f"No matching cases found for: {args.ids}")
            return

        cases = cases[first_index:]

        print(
            "Starting from:",
            cases[0]["id"],
        )

    async_client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )

    llm = llm_factory(
        settings.llm_model,
        client=async_client,
        temperature=0,
        max_tokens=2048,
    )

    embeddings = HuggingFaceEmbeddings(
        model=settings.embedding_model,
    )

    faithfulness = Faithfulness(llm=llm)
    context_precision = ContextPrecision(llm=llm)
    context_recall = ContextRecall(llm=llm)

    answer_relevancy = AnswerRelevancy(
        llm=llm,
        embeddings=embeddings,
    )

    for case in cases:

        case_id = case["id"]

        # Skip cases that already have all four metrics
        if is_complete(existing_results.get(case_id)):
            existing_results[case_id].update(id_scores(case))
            print(
                f"\nSkipping {case_id} - already complete"
            )
            continue

        try:
            result = await evaluate_case(
                case,
                faithfulness,
                context_precision,
                context_recall,
                answer_relevancy,
            )

        except RateLimitError as e:
            print("\nRATE LIMIT REACHED.")
            print("Stopping without saving incomplete case.")
            print(e)
            break

        # Only save if all four metrics were successfully calculated
        if not is_complete(result):
            print(
                f"  {case_id} is incomplete - NOT saved"
            )
            continue

        existing_results[case_id] = result

        # Rewrite the file while preserving previous completed results
        with open(
            OUTPUT_PATH,
            "w",
            encoding="utf-8",
        ) as f:

            for saved_case in all_cases:
                saved_id = saved_case["id"]

                if saved_id in existing_results:
                    f.write(
                        json.dumps(
                            existing_results[saved_id],
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

        print(f"  Saved complete result: {case_id}")

    completed = sum(
        1
        for result in existing_results.values()
        if is_complete(result)
    )

    print_summary(
        [
            existing_results.get(case["id"]) or {"id": case["id"]}
            for case in all_cases
        ]
    )

    print("\nFinished.")
    print("Complete cases:", completed)
    print("Output:", OUTPUT_PATH)


if __name__ == "__main__":
    asyncio.run(main())
