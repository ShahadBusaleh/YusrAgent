import asyncio
import json
import os

from openai import AsyncOpenAI

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
METRIC_KEYS = (
    "faithfulness",
    "context_precision",
    "context_recall",
    "answer_relevancy",
)


def load_cases():
    with open(RESULTS_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class Metrics:
    """The four Ragas metrics bound to one LLM key. Ragas drives a single
    client, so when that key spends its daily quota we move to the next key
    in LLM_API_KEYS instead of failing every remaining metric."""

    def __init__(self, settings, embeddings):
        self.settings = settings
        self.embeddings = embeddings
        self.keys = list(settings.llm_api_keys or (settings.llm_api_key,))
        self.index = 0
        self._build()

    def _build(self):
        print(f"  (using key {self.index + 1} of {len(self.keys)})")
        llm = llm_factory(
            self.settings.llm_model,
            client=AsyncOpenAI(
                api_key=self.keys[self.index],
                base_url=self.settings.llm_base_url,
                max_retries=6,
            ),
            # Default 1024 truncates Faithfulness' claim list on long
            # Consultant answers (gpt-oss also spends tokens reasoning).
            max_tokens=4096,
            reasoning_effort="low",
        )
        self.faithfulness = Faithfulness(llm=llm)
        self.context_precision = ContextPrecision(llm=llm)
        self.context_recall = ContextRecall(llm=llm)
        self.answer_relevancy = AnswerRelevancy(
            llm=llm,
            embeddings=self.embeddings,
        )

    def next_key(self):
        if self.index + 1 >= len(self.keys):
            return False
        self.index += 1
        self._build()
        return True


async def safe_score(name, metrics, metric_call):
    while True:
        try:
            result = await asyncio.wait_for(metric_call(metrics), timeout=120)
            value = result.value

            print(f"  {name}: {value}")
            return value

        except asyncio.TimeoutError:
            print(f"  {name}: TIMEOUT")
            return None

        except Exception as e:
            if "tokens per day" in str(e) and metrics.next_key():
                continue
            print(f"  {name}: ERROR - {type(e).__name__}: {str(e)[:200]}")
            return None


async def evaluate_case(case, metrics):
    print(f"\nEvaluating {case['id']}...")

    faithfulness_result = await safe_score(
        "Faithfulness",
        metrics,
        lambda m: m.faithfulness.ascore(
            user_input=case["question"],
            response=case["answer"],
            retrieved_contexts=case["contexts"],
        ),
    )

    context_precision_result = await safe_score(
        "Context Precision",
        metrics,
        lambda m: m.context_precision.ascore(
            user_input=case["question"],
            retrieved_contexts=case["contexts"],
            reference=case["reference_answer"],
        ),
    )

    context_recall_result = await safe_score(
        "Context Recall",
        metrics,
        lambda m: m.context_recall.ascore(
            user_input=case["question"],
            retrieved_contexts=case["contexts"],
            reference=case["reference_answer"],
        ),
    )

    answer_relevancy_result = await safe_score(
        "Answer Relevancy",
        metrics,
        lambda m: m.answer_relevancy.ascore(
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

    # Groq has no embeddings endpoint, so AnswerRelevancy uses the same
    # local sentence-transformers model as the policy index.
    embeddings = HuggingFaceEmbeddings(
        model=os.getenv(
            "EMBEDDING_MODEL",
            "sentence-transformers/all-MiniLM-L6-v2",
        ),
    )

    metrics = Metrics(settings, embeddings)

    results = []

    for case in cases:
        result = await evaluate_case(case, metrics)
        results.append(result)

    scored = [
        r for r in results
        if any(r[k] is not None for k in METRIC_KEYS)
    ]
    if not scored:
        raise SystemExit(
            f"Every metric failed; kept the previous {OUTPUT_PATH}."
        )

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