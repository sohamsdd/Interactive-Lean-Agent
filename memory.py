"""
Strategy Memory System
Persists successful proof strategies keyed by theorem shape/category.
On future similar theorems, injects relevant past strategies into prompts.
"""

import json
import os
import re
from dataclasses import dataclass
from typing import Optional


MEMORY_FILE = os.path.join(os.path.dirname(__file__), "proof_memory.json")

# Theorem shape categories — used as memory keys
THEOREM_SHAPES = [
    "irrationality",
    "algebraic_identity",
    "divisibility",
    "primality",
    "inequality",
    "number_theory",
    "real_analysis",
    "induction",
    "combinatorics",
    "logic",
    "other",
]


@dataclass
class MemoryEntry:
    theorem_shape: str
    theorem_example: str        # the actual theorem statement
    lean_signature_pattern: str # e.g. "theorem _ : Irrational _"
    successful_proof: str       # the Lean proof that worked
    proof_strategy: str         # one-sentence description of the strategy
    tactics_used: list[str]     # e.g. ["exact", "irrational_sqrt_two"]
    retries_needed: int         # how many retries it took


class StrategyMemory:
    def __init__(self, memory_file: str = MEMORY_FILE):
        self.memory_file = memory_file
        self._data: dict[str, list[dict]] = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {shape: [] for shape in THEOREM_SHAPES}

    def _save(self):
        os.makedirs(os.path.dirname(self.memory_file) if os.path.dirname(self.memory_file) else ".", exist_ok=True)
        with open(self.memory_file, "w") as f:
            json.dump(self._data, f, indent=2)

    def classify_theorem(self, theorem_statement: str, lean_signature: str) -> str:
        """Classify a theorem into a shape category using keyword heuristics."""
        text = (theorem_statement + " " + lean_signature).lower()

        if any(k in text for k in ["irrational", "sqrt", "transcendental"]):
            return "irrationality"
        if any(k in text for k in ["prime", "nat.prime", "irreducible"]):
            return "primality"
        if any(k in text for k in ["dvd", "divides", "divisib", "mod", "gcd"]):
            return "divisibility"
        if any(k in text for k in ["^2", "sq", "expand", "ring", "semiring", "comm"]):
            return "algebraic_identity"
        if any(k in text for k in ["≤", "≥", "<", ">", "le", "lt", "ge", "gt", "bound", "inequal"]):
            return "inequality"
        if any(k in text for k in ["induct", "nat", "succ", "rec"]):
            return "induction"
        if any(k in text for k in ["real", "continuous", "limit", "deriv", "integr"]):
            return "real_analysis"
        if any(k in text for k in ["choose", "factorial", "permut", "binom"]):
            return "combinatorics"
        if any(k in text for k in ["even", "odd", "integer", "int", "number"]):
            return "number_theory"
        if any(k in text for k in ["iff", "implies", "forall", "exists", "logic"]):
            return "logic"
        return "other"

    def get_relevant_strategies(self, theorem_shape: str, limit: int = 3) -> list[dict]:
        """Return up to `limit` past successful strategies for this shape."""
        entries = self._data.get(theorem_shape, [])
        # Sort by fewest retries (most efficient proofs first)
        sorted_entries = sorted(entries, key=lambda e: e.get("retries_needed", 99))
        return sorted_entries[:limit]

    def store_success(
        self,
        theorem_shape: str,
        theorem_statement: str,
        lean_signature: str,
        successful_proof: str,
        proof_strategy: str,
        retries_needed: int,
    ):
        """Store a successful proof strategy for future reuse."""
        # Extract tactics used
        tactics = self._extract_tactics(successful_proof)

        entry = {
            "theorem_example": theorem_statement,
            "lean_signature_pattern": self._abstract_signature(lean_signature),
            "successful_proof": successful_proof,
            "proof_strategy": proof_strategy,
            "tactics_used": tactics,
            "retries_needed": retries_needed,
        }

        if theorem_shape not in self._data:
            self._data[theorem_shape] = []

        # Avoid storing duplicates (same proof text)
        existing_proofs = [e["successful_proof"] for e in self._data[theorem_shape]]
        if successful_proof not in existing_proofs:
            self._data[theorem_shape].append(entry)
            # Keep max 10 entries per shape
            self._data[theorem_shape] = sorted(
                self._data[theorem_shape], key=lambda e: e.get("retries_needed", 99)
            )[:10]
            self._save()

    def format_for_prompt(self, theorem_shape: str) -> str:
        """Format relevant memory entries as a string to inject into LLM prompts."""
        strategies = self.get_relevant_strategies(theorem_shape)
        if not strategies:
            return ""

        lines = [f"\n--- PAST SUCCESSFUL STRATEGIES FOR '{theorem_shape.upper()}' THEOREMS ---"]
        for i, s in enumerate(strategies, 1):
            lines.append(f"\nExample {i}: {s['theorem_example']}")
            lines.append(f"Strategy: {s['proof_strategy']}")
            lines.append(f"Tactics used: {', '.join(s['tactics_used'])}")
            lines.append(f"Proof:\n{s['successful_proof']}")
        lines.append("--- END OF PAST STRATEGIES ---\n")
        lines.append("Use these as reference patterns. Adapt, don't copy verbatim.\n")
        return "\n".join(lines)

    def _extract_tactics(self, lean_proof: str) -> list[str]:
        """Extract tactic names from a Lean proof."""
        tactic_pattern = re.compile(
            r'\b(exact|apply|simp|norm_num|ring|omega|linarith|nlinarith|decide|'
            r'norm_cast|push_cast|field_simp|constructor|intro|rcases|obtain|'
            r'contradiction|by_contra|push_neg|rw|have|calc|use|trivial|tauto|'
            r'aesop|positivity|gcongr|refine)\b'
        )
        found = tactic_pattern.findall(lean_proof)
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for t in found:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return unique

    def _abstract_signature(self, sig: str) -> str:
        """Turn a concrete signature into an abstract pattern for display."""
        # Replace concrete names with placeholders for readability
        sig = re.sub(r'\b[a-z]\b', '_', sig)
        return sig

    def get_stats(self) -> dict:
        """Return memory statistics."""
        stats = {}
        for shape, entries in self._data.items():
            if entries:
                stats[shape] = len(entries)
        return stats
