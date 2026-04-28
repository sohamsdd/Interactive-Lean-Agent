"""
CLI entry point for the AI Proof Assistant.
Usage:
    python main.py                                          # interactive mode
    python main.py --mock                                   # mock Lean (no Lean install needed)
    python main.py --query "sqrt 2 is irrational"
    python main.py --lake-project ~/RP-project/my_proofs   # real Lean + Mathlib
    python main.py --memory-stats                          # show what's in memory
"""

import argparse
import os
import sys
from core.agent import ProofAssistantAgent
from core.memory import StrategyMemory
from lean.repl import LeanREPL

os.environ["OPENAI_API_KEY"] = ""


def user_callback(state, question: str) -> str:
    """Called when the agent needs user input."""
    print("\n" + "=" * 60)
    print("AGENT NEEDS YOUR INPUT:")
    print(question)
    print("=" * 60)
    return input("Your answer (or press Enter to skip): ").strip()


def print_result(state):
    print("\n" + "=" * 60)
    print("PROOF RESULT")
    print("=" * 60)
    print(f"Status        : {state.status}")
    print(f"Theorem shape : {state.theorem_shape}")
    print(f"Theorem       : {state.theorem_statement}")
    print(f"Retries needed: {state.retries}")
    print(f"\nAssumptions:")
    for a in state.assumptions:
        print(f"  • {a}")
    print(f"\nInformal Sketch:\n{state.informal_sketch}")
    print(f"\nLean 4 Proof:\n{state.lean_proof}")
    if state.status == "done":
        print("\n✓ Proof verified by Lean 4.")
    elif state.status == "escalated":
        print(f"\n⚠ Could not verify after {state.retries} attempts.")
        print(f"  Last error type: {state.last_error_type.value}")
        print(f"  Last error: {state.last_error[:200]}")


def main():
    parser = argparse.ArgumentParser(description="AI Proof Assistant")
    parser.add_argument("--query", type=str, default=None, help="Math statement to prove")
    parser.add_argument("--mock", action="store_true", help="Use mock Lean (no install needed)")
    parser.add_argument("--lean-path", type=str, default="lean", help="Path to lean binary")
    parser.add_argument("--lake-project", type=str, default=None, help="Path to lake+Mathlib project")
    parser.add_argument("--memory-stats", action="store_true", help="Show memory statistics and exit")
    args = parser.parse_args()

    # Show memory stats and exit
    if args.memory_stats:
        mem = StrategyMemory()
        stats = mem.get_stats()
        if stats:
            print("Memory contents:")
            for shape, count in stats.items():
                print(f"  {shape}: {count} stored strategy/strategies")
                for entry in mem.get_relevant_strategies(shape):
                    print(f"    - {entry['theorem_example'][:60]} [{entry['retries_needed']} retries]")
        else:
            print("Memory is empty — no successful proofs stored yet.")
        return

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set.")
        sys.exit(1)

    # Set up Lean REPL
    if args.mock:
        lean = LeanREPL(mode="mock")
        print("Running in MOCK mode (Lean not required).")
    elif args.lake_project:
        lean = LeanREPL(mode="lake", project_dir=args.lake_project)
    else:
        lean = LeanREPL(mode="lean_binary", lean_binary=args.lean_path)

    agent = ProofAssistantAgent(lean_repl=lean, verbose=True)

    # Get query
    if args.query:
        query = args.query
    else:
        print("\nAI Proof Assistant (powered by o4-mini + Lean 4)")
        print("-" * 50)
        query = input("Enter a mathematical statement to prove:\n> ").strip()
        if not query:
            print("No query provided.")
            sys.exit(1)

    state = agent.run(query, user_callback=user_callback)
    print_result(state)


if __name__ == "__main__":
    main()
