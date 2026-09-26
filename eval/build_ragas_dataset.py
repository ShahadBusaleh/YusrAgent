import json
from pathlib import Path

from app.agents.consultant_agent import ConsultantAgent


GOLDEN_PATH = Path("eval/golden/consultant_rag.jsonl")
OUTPUT_PATH = Path("eval/golden/consultant_rag_results.jsonl")


def load_cases():
    with GOLDEN_PATH.open(encoding="utf-8-sig") as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    cases = load_cases()
    agent = ConsultantAgent()

    results = []

    for case in cases:
        print(f"\nRunning {case['id']}...")

        output = agent.run({
            "query": case["question"]
        })

        contexts = [
            source.get("text", "")
            for source in output.get("sources", [])
            if source.get("text")
        ]

        source_ids = [
            source.get("id")
            for source in output.get("sources", [])
            if source.get("id")
        ]

        result = {
            "id": case["id"],
            "question": case["question"],
            "answer": output.get("recommendation", ""),
            "contexts": contexts,
            "retrieved_source_ids": source_ids,
            "reference_answer": case["reference_answer"],
            "reference_contexts": case["reference_contexts"],
            "reference_source_ids": case["reference_source_ids"],
            "success": output.get("success"),
        }

        results.append(result)

        print("  success:", result["success"])
        print("  retrieved:", source_ids)
        print("  contexts:", len(contexts))

    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        for result in results:
            f.write(
                json.dumps(
                    result,
                    ensure_ascii=False,
                )
                + "\n"
            )

    print(f"\nSaved: {OUTPUT_PATH}")
    print(f"Cases: {len(results)}")


if __name__ == "__main__":
    main()