import asyncio
import json

from openai import AsyncOpenAI, OpenAI

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


def load_cases():
    with open(RESULTS_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


async def evaluate_case(
    case,
    faithfulness,
    context_precision,
    context_recall,
    answer_relevancy,
):
    print(f"\nEvaluating {case['id']}...")

    faithfulness_result = await faithfulness.ascore(
        user_input=case["question"],
        response=case["answer"],
        retrieved_contexts=case["contexts"],
    )

    context_precision_result = await context_precision.ascore(
        user_input=case["question"],
        retrieved_contexts=case["contexts"],
        reference=case["reference_answer"],
    )
    context_recall_result = await context_recall.ascore(
        user_input=case["question"],
        retrieved_contexts=case["contexts"],
        reference=case["reference_answer"],
    )
    answer_relevancy_result = await answer_relevancy.ascore(
        user_input=case["question"],
        response=case["answer"],
    )

    result = {
        "id": case["id"],
        "faithfulness": faithfulness_result.value,
        "context_precision": context_precision_result.value,
        "context_recall": context_recall_result.value,
        "answer_relevancy": answer_relevancy_result.value,
        "retrieved_source_ids": case["retrieved_source_ids"],
        "reference_source_ids": case["reference_source_ids"],
    }

    print("  Faithfulness:", result["faithfulness"])
    print("  Context Precision:", result["context_precision"])
    print("  Context Recall:", result["context_recall"])
    print("  Answer Relevancy:", result["answer_relevancy"])

    return result


async def main():
    print("MAIN START")
    settings = get_settings()
    cases = load_cases()

    async_client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )

    sync_client = OpenAI(
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

    output_path = "eval/golden/ragas_results.jsonl"

    with open(output_path, "w", encoding="utf-8") as f:
        for result in results:
            f.write(
                json.dumps(
                    result,
                    ensure_ascii=False,
                )
                + "\n"
            )

    print("\nSaved:", output_path)
    print("Cases:", len(results))


if __name__ == "__main__":
    asyncio.run(main())