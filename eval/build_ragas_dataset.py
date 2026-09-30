import argparse
import json

from app.agents.consultant_agent import ConsultantAgent
from app.config import ROOT


GOLDEN_PATH = ROOT / "eval" / "golden" / "consultant_rag.jsonl"
OUTPUT_PATH = ROOT / "eval" / "golden" / "consultant_rag_results.jsonl"

POLICY_ROOT = ROOT / "policy_texts"

SOURCE_DIRS = [
    POLICY_ROOT / "company_policies",
    POLICY_ROOT / "saudi_labor_law",
    POLICY_ROOT / "WPS",
]


def load_cases():
    with GOLDEN_PATH.open(encoding="utf-8-sig") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_reference_contexts(source_ids):
    source_map = {}

    for folder in SOURCE_DIRS:
        if not folder.exists():
            continue

        for path in folder.glob("*.txt"):
            source_map[path.stem] = path.read_text(
                encoding="utf-8"
            ).strip()

    missing = [source_id for source_id in source_ids if source_id not in source_map]

    if missing:
        raise FileNotFoundError(
            f"Reference source files not found: {missing}"
        )

    return [source_map[source_id] for source_id in source_ids]


def load_saved_results():
    if not OUTPUT_PATH.exists():
        return {}
    with OUTPUT_PATH.open(encoding="utf-8") as f:
        return {
            result["id"]: result
            for result in map(json.loads, filter(str.strip, f))
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--id",
        action="append",
        dest="ids",
        help="Rebuild only these case IDs (repeatable); other saved results are kept.",
    )
    args = parser.parse_args()

    all_cases = load_cases()
    cases = all_cases
    saved = {}
    if args.ids:
        cases = [case for case in all_cases if case["id"] in set(args.ids)]
        saved = load_saved_results()

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

        # A WPS section chunk ("WPS021-WPS030") holds several rule ids;
        # run_ragas scores id recall against these.
        member_ids = [
            source.get("source_ids") or [source.get("id")]
            for source in output.get("sources", [])
            if source.get("id")
        ]

        reference_source_ids = case.get("reference_source_ids", [])

        result = {
            "id": case["id"],
            "question": case["question"],
            "answer": output.get("recommendation", ""),
            "contexts": contexts,
            "retrieved_source_ids": source_ids,
            "retrieved_member_ids": member_ids,
            "reference_answer": case["reference_answer"],
            "reference_contexts": load_reference_contexts(
                reference_source_ids
            ),
            "reference_source_ids": reference_source_ids,
            "success": output.get("success"),
        }

        results.append(result)

        print("  success:", result["success"])
        print("  retrieved:", source_ids)
        print("  reference:", reference_source_ids)
        print("  contexts:", len(contexts))

    if args.ids:
        saved.update({result["id"]: result for result in results})
        results = [saved[case["id"]] for case in all_cases if case["id"] in saved]

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
