"""
RFP Word Agent - Main Orchestrator

Processes RFP Word documents through a 3-stage pipeline:
1. STAGE 1 (Local LLM - Ollama): Analyze document structure + anonymize
2. STAGE 2 (External LLM - RAG): Generate answers using llm_router
3. STAGE 3 (python-docx): Fill answers back into Word document
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from word_analyzer import analyze_document, print_analysis_summary
from word_filler import fill_document, deanonymize_answers
from llm_router import LLMRouter


def generate_answers(
    analysis: dict,
    model: str = "gemini",
    solution: str = "planning"
) -> dict:
    """
    Generate answers for all questions using the LLM router with RAG.

    Args:
        analysis: Document analysis from word_analyzer
        model: LLM model to use (default: gemini)
        solution: Solution context (default: planning)

    Returns:
        Dict mapping question IDs to answer dicts
    """
    questions = analysis.get('questions', [])

    if not questions:
        print("[WARNING] No questions found in analysis")
        return {}

    print(f"\n[INFO] Initializing LLM Router with solution: {solution}")
    router = LLMRouter(solution=solution)

    answers = {}
    total = len(questions)

    print(f"[INFO] Generating answers for {total} questions using {model.upper()}...")
    print("=" * 60)

    for i, question in enumerate(questions, 1):
        q_id = question.get('id')
        anonymized_text = question.get('anonymized_text', question.get('original_text', ''))
        category = question.get('category', 'general')

        print(f"\n[{i}/{total}] {q_id} ({category})")
        print(f"  Q: {anonymized_text[:80]}{'...' if len(anonymized_text) > 80 else ''}")

        try:
            answer = router.generate_answer(anonymized_text, model=model)

            # Truncate for display
            display_answer = answer[:100] + '...' if len(answer) > 100 else answer
            print(f"  A: {display_answer}")

            answers[q_id] = {
                "answer": answer,
                "model": model,
                "category": category,
                "question": anonymized_text
            }

        except Exception as e:
            print(f"  [ERROR] {e}")
            answers[q_id] = {
                "answer": f"Error: {str(e)}",
                "model": model,
                "category": category,
                "question": anonymized_text
            }

    print("\n" + "=" * 60)
    print(f"[SUCCESS] Generated {len(answers)} answers")

    return answers


def run_pipeline(
    input_path: str,
    solution: str = "planning",
    model: str = "gemini",
    local_llm: str = "qwen2.5:7b",
    analyze_only: bool = False,
    output_dir: Optional[str] = None
) -> dict:
    """
    Run the complete Word RFP processing pipeline.

    Args:
        input_path: Path to input .docx file
        solution: Solution context for RAG (default: planning)
        model: External LLM model (default: gemini)
        local_llm: Ollama model for analysis (default: qwen2.5:7b)
        analyze_only: Only run Stage 1 (analysis)
        output_dir: Output directory (default: data/output/)

    Returns:
        Dict with pipeline results and file paths
    """
    input_path = Path(input_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Determine output directory
    if output_dir:
        output_dir = Path(output_dir)
    else:
        output_dir = PROJECT_ROOT / "data" / "output"

    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate base name for outputs
    base_name = input_path.stem
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Output file paths
    analysis_path = output_dir / f"{base_name}_analysis.json"
    answers_path = output_dir / f"{base_name}_answers.json"
    filled_path = output_dir / f"{base_name}_FILLED.docx"

    results = {
        "input_file": str(input_path),
        "output_dir": str(output_dir),
        "timestamp": timestamp
    }

    # ===== STAGE 1: Document Analysis =====
    print("\n" + "=" * 60)
    print("STAGE 1: Document Analysis (Ollama)")
    print("=" * 60)

    analysis = analyze_document(
        docx_path=str(input_path),
        local_llm=local_llm,
        output_path=str(analysis_path)
    )

    results["analysis_file"] = str(analysis_path)
    results["total_questions"] = analysis.get('total_questions', 0)
    results["client_names"] = analysis.get('client_names_found', [])

    print_analysis_summary(analysis)

    if analyze_only:
        print("\n[INFO] --analyze-only flag set. Stopping after Stage 1.")
        results["stage"] = "analysis_only"
        return results

    # ===== STAGE 2: Answer Generation =====
    print("\n" + "=" * 60)
    print("STAGE 2: Answer Generation (RAG + External LLM)")
    print("=" * 60)

    answers = generate_answers(
        analysis=analysis,
        model=model,
        solution=solution
    )

    # Save answers
    with open(answers_path, 'w', encoding='utf-8') as f:
        json.dump(answers, f, indent=2, ensure_ascii=False)

    print(f"[SUCCESS] Answers saved to: {answers_path}")
    results["answers_file"] = str(answers_path)

    # ===== STAGE 3: Fill Document =====
    print("\n" + "=" * 60)
    print("STAGE 3: Fill Document")
    print("=" * 60)

    # De-anonymize answers before filling
    client_names = analysis.get('client_names_found', [])
    if client_names:
        print(f"[INFO] De-anonymizing answers (replacing [CLIENT] with '{client_names[0]}')")
        answers = deanonymize_answers(answers, client_names)

    filled_file = fill_document(
        docx_path=str(input_path),
        analysis=analysis,
        answers=answers,
        output_path=str(filled_path)
    )

    results["filled_file"] = filled_file
    results["stage"] = "complete"

    # ===== Summary =====
    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print(f"\nInput:    {input_path}")
    print(f"Analysis: {analysis_path}")
    print(f"Answers:  {answers_path}")
    print(f"Output:   {filled_file}")
    print(f"\nQuestions processed: {results['total_questions']}")
    print("=" * 60)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="RFP Word Agent - Process Word documents with AI-powered answers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full pipeline
  python src/rfp_word_agent.py --input "data/input/security_questionnaire.docx"

  # Analyze only (preview structure)
  python src/rfp_word_agent.py --input "data/input/rfp.docx" --analyze-only

  # Use different models
  python src/rfp_word_agent.py --input "data/input/rfp.docx" --model claude --local-llm mistral

  # Specify solution context
  python src/rfp_word_agent.py --input "data/input/rfp.docx" --solution wms
        """
    )

    parser.add_argument(
        "--input",
        required=True,
        help="Path to input .docx file"
    )
    parser.add_argument(
        "--solution",
        default="planning",
        help="Solution context: planning, wms, catman, etc. (default: planning)"
    )
    parser.add_argument(
        "--model",
        default="gemini",
        help="External LLM model: gemini, claude, gpt5, etc. (default: gemini)"
    )
    parser.add_argument(
        "--local-llm",
        default="qwen2.5:7b",
        help="Ollama model for document analysis (default: qwen2.5:7b)"
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="Only run Stage 1 (analysis), show structure and exit"
    )
    parser.add_argument(
        "--output",
        help="Output directory (default: data/output/)"
    )

    args = parser.parse_args()

    try:
        results = run_pipeline(
            input_path=args.input,
            solution=args.solution,
            model=args.model,
            local_llm=args.local_llm,
            analyze_only=args.analyze_only,
            output_dir=args.output
        )

        # Exit with success
        sys.exit(0)

    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
