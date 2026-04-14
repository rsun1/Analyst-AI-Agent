import json
import os
import sys

import openai
from dotenv import load_dotenv

# Allow imports from the parent project directory (agent_core, db, etc.)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def load_test_dataset(path="evals/test_dataset.json"):
    with open(path) as f:
        return json.load(f)


def run_agent(question: str) -> tuple[str, str]:
    """
    Run the full agent pipeline on a question (auto-approving the SQL)
    and return (sql, summary).
    On failure, sql is "" and summary is an [ERROR: ...] string.
    """
    from agent_core import step_explain, step_execute, step_generate, step_rag

    try:
        print("    • retrieving RAG context...", flush=True)
        rag_context = step_rag(question)
        print("    • generating SQL...", flush=True)
        sql, _ = step_generate(question, rag_context)
        if not sql:
            return "", "[ERROR: No SQL was generated]"
        print("    • executing SQL...", flush=True)
        columns, rows, _ = step_execute(sql)
        print("    • generating insights...", flush=True)
        summary = step_explain(question, sql, columns, rows)
        return sql, summary
    except Exception as e:
        return "", f"[ERROR: {e}]"


def judge(
    question: str,
    expected_answer: str | None,
    agent_answer: str,
    eval_type: str = "answer_verification",
    eval_criteria: str = "",
) -> dict | None:
    """
    Use an LLM as judge to score the agent's answer on two dimensions.

    Returns a dict with:
        factual_accuracy  — "PASS" or "FAIL"
        answer_quality    — 1, 2, or 3
            1 = wrong or hallucinated
            2 = correct but generic
            3 = correct and insightful
    Returns None for known_failure (excluded from scoring).
    """
    if eval_type == "known_failure":
        return None  # Documented failure — excluded from score

    if agent_answer.startswith("[ERROR"):
        return {"factual_accuracy": "FAIL", "answer_quality": 1, "reasoning": "Agent returned an error."}

    # ── Ambiguity handling: separate prompt + JSON response with partial pass ──

    if eval_type == "ambiguity_handling":
        prompt = f"""
The agent was asked an ambiguous question: "{question}"

Eval criteria: {eval_criteria}

Agent response: {agent_answer}

Did the agent either (a) ask a clarifying question, or (b) explicitly state its interpretation before proceeding?
Silently assuming an interpretation is a PARTIAL PASS — correct output but missing transparency.

Return JSON only:
{{"factual_accuracy": "pass/partial/fail", "answer_quality": 1, "reasoning": "one sentence"}}
"""
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a strict but fair evaluator. Return valid JSON only, no other text.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        content = (response.choices[0].message.content or "").strip()
        # Strip markdown code fences if the model wraps the JSON
        if content.startswith("```"):
            content = content.split("```")[1].lstrip("json").strip()
        try:
            parsed = json.loads(content)
            return {
                "factual_accuracy": parsed.get("factual_accuracy", "fail").upper(),
                "answer_quality": int(parsed.get("answer_quality", 1)),
                "reasoning": parsed.get("reasoning", ""),
            }
        except (json.JSONDecodeError, ValueError):
            return {"factual_accuracy": "FAIL", "answer_quality": 1, "reasoning": content}

    # ── Standard answer_verification prompt ───────────────────────────────────

    expected_block = (
        f"Expected answer: {expected_answer}"
        if expected_answer
        else "(No expected answer — see eval criteria below)"
    )

    prompt = f"""You are evaluating an AI data analyst agent's answer about the Olist Brazilian e-commerce database.

Question: {question}

{expected_block}

Agent's answer:
{agent_answer}

Evaluation type: {eval_type}
Evaluation criteria: {eval_criteria}

Score the agent's answer on two dimensions:

1. FACTUAL_ACCURACY (PASS or FAIL):
   Do the key numbers and facts match the expected answer, even if phrased differently?
   Apply the eval criteria (e.g. within 1%, exact match).

2. ANSWER_QUALITY (1, 2, or 3):
   1 = Wrong or hallucinated — the answer contains incorrect facts or fabricated data
   2 = Correct but generic — the answer is factually right but adds no meaningful context or insight
   3 = Correct and insightful — the answer is accurate and provides useful business context, \
trends, or recommendations grounded in the data

Reply in exactly this format, with no other text:
FACTUAL_ACCURACY: PASS
ANSWER_QUALITY: 3"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": "You are a strict but fair evaluator. Follow the exact reply format requested.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )
    content = (response.choices[0].message.content or "").strip()

    factual_accuracy = "FAIL"
    answer_quality = 1
    for line in content.splitlines():
        line = line.strip()
        if line.upper().startswith("FACTUAL_ACCURACY:"):
            value = line.split(":", 1)[1].strip().upper()
            factual_accuracy = "PASS" if value == "PASS" else "FAIL"
        elif line.upper().startswith("ANSWER_QUALITY:"):
            try:
                answer_quality = int(line.split(":", 1)[1].strip())
            except ValueError:
                answer_quality = 1

    return {"factual_accuracy": factual_accuracy, "answer_quality": answer_quality, "reasoning": ""}


def run_eval(
    dataset_path: str = "evals/test_dataset.json",
    output_path: str = "evals/eval_results.json",
) -> None:
    dataset = load_test_dataset(dataset_path)
    total = len(dataset)
    results = []
    passed = partial = failed = documented = 0
    quality_scores: list[int] = []

    for i, test in enumerate(dataset, 1):
        test_id = test["id"]
        question = test["question"]
        expected_answer = test.get("expected_answer_english") or test.get("expected_answer")
        eval_type = test.get("eval_type", "answer_verification")
        eval_criteria = test.get("eval_criteria", "")

        print(f"\n[{i}/{total}] [{test_id}] ({eval_type})", flush=True)
        print(f"  Q: {question}", flush=True)

        agent_sql, agent_answer = run_agent(question)
        print("    • judging...", flush=True)
        scores = judge(question, expected_answer, agent_answer, eval_type, eval_criteria)

        if scores is None:
            factual_accuracy = "DOCUMENTED"
            answer_quality = None
            reasoning = ""
            documented += 1
        else:
            factual_accuracy = scores["factual_accuracy"]
            answer_quality = scores["answer_quality"]
            reasoning = scores.get("reasoning", "")
            quality_scores.append(answer_quality)
            if factual_accuracy == "PASS":
                passed += 1
            elif factual_accuracy == "PARTIAL":
                partial += 1
            else:
                failed += 1

        preview = agent_answer[:200] + ("..." if len(agent_answer) > 200 else "")
        print(f"  Agent: {preview}", flush=True)
        print(f"  Factual accuracy: {factual_accuracy}  |  Answer quality: {answer_quality if answer_quality is not None else 'N/A'}", flush=True)
        if reasoning:
            print(f"  Reasoning: {reasoning}", flush=True)
        print("-" * 60, flush=True)

        results.append({
            "id": test_id,
            "question": question,
            "eval_type": eval_type,
            "expected_sql": test.get("expected_sql"),
            "agent_sql": agent_sql,
            "expected_answer": expected_answer,
            "agent_answer": agent_answer,
            "factual_accuracy": factual_accuracy,
            "answer_quality": answer_quality,
            "reasoning": reasoning,
        })

    total_scored = passed + partial + failed
    avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0
    print(f"\n{'=' * 60}")
    print("EVAL SUMMARY")
    if total_scored:
        print(f"  PASS:               {passed} / {total_scored}  ({passed / total_scored * 100:.1f}%)")
        print(f"  PARTIAL:            {partial} / {total_scored}  ({partial / total_scored * 100:.1f}%)")
        print(f"  FAIL:               {failed} / {total_scored}  ({failed / total_scored * 100:.1f}%)")
    else:
        print("  Factual accuracy:  N/A")
    print(f"  Avg answer quality: {avg_quality:.2f} / 3.00")
    print(f"  Documented:         {documented}  (known_failure — excluded from score)")
    print(f"{'=' * 60}")

    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    run_eval()
