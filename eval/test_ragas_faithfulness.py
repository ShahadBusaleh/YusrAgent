import asyncio
import json

from openai import AsyncOpenAI
from ragas.llms import llm_factory
from ragas.metrics.collections import Faithfulness

from app.config import get_settings


RESULTS_PATH = "eval/golden/consultant_rag_results.jsonl"


async def main():
    settings = get_settings()

    client = AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )

    llm = llm_factory(
        settings.llm_model,
        client=client,
    )

    with open(RESULTS_PATH, encoding="utf-8") as f:
        case = json.loads(f.readline())

    metric = Faithfulness(llm=llm)

    result = await metric.ascore(
        user_input=case["question"],
        response=case["answer"],
        retrieved_contexts=case["contexts"],
    )

    print("CASE:", case["id"])
    print("FAITHFULNESS:", result.value)


if __name__ == "__main__":
    asyncio.run(main())