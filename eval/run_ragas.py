import asyncio
import json

from openai import AsyncOpenAI

from ragas.llms import llm_factory
from ragas.embeddings import OpenAIEmbeddings
from ragas.metrics.collections import (
    Faithfulness,
    ContextPrecision,
    ContextRecall,
    AnswerRelevancy,
)

from app.config import get_settings


RESULTS_PATH = "eval/golden/consultant_rag_results.jsonl"
OUTPUT_PATH = "eval/golden/ragas_results.jsonl"


def load_cases():
    with open(RESULTS_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


async def safe_score(name, metric_call):
    try:
        result = await asyncio.wait_for(metric_call(), timeout=60)
        value = result.value

        print(f"  {name}: {value}")
        return value

    except asyncio.TimeoutError:
        print(f"  {name}: TIMEOUT")
        return None

    except Exception as e:
        print(f"  {name}: ERROR - {type(e).__name__}: {e}")
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
        "retrieved_source_ids": case["retrieved_source_ids"],
        "reference_source_ids": case["reference_source_ids"],
    }

    return result


async def main():
    print("MAIN START")

    settings = get_settings()
    cases = load_cases()

    async_client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )

    llm = llm_factory(
        settings.llm_model,
        client=async_client,
    )

    embeddings = OpenAIEmbeddings(
        client=async_client,
        model="text-embedding-3-small",
    )

    faithfulness = Faithfulness(llm=llm)
    context_precision = ContextPrecision(llm=llm)
    context_recall = ContextRecall(llm=llm)

    answer_relevancy = AnswerRelevancy(
        llm=llm,
        embeddings=embeddings,
    )

    results = []

    for case in cases:
        result = await evaluate_case(
            case,
            faithfulness,
            context_precision,
            context_recall,
            answer_relevancy,
        )
        results.append(result)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for result in results:
            f.write(
                json.dumps(
                    result,
                    ensure_ascii=False,
                )
                + "\n"
            )

    print("\nSaved:", OUTPUT_PATH)
    print("Cases:", len(results))


if __name__ == "__main__":
    asyncio.run(main())