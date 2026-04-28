"""
AI Proof Assistant Agent
- Stage 1: Extract assumptions + classify theorem shape
- Stage 2: Generate informal proof sketch (memory-informed)
- Stage 3: Generate Lean 4 proof (memory-informed)
- Stage 4: Verify + error-type-aware correction + session failure tracking + memory
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional
from openai import OpenAI

from lean.repl import LeanREPL, ErrorType
from core.prompts import (
    ASSUMPTION_EXTRACTOR_PROMPT,
    SKETCH_PROMPT,
    FULL_PROOF_PROMPT,
    CORRECTION_PROMPT,
    ESCALATION_PROMPT,
    correction_prompt_for_error_type,
)
from core.memory import StrategyMemory

MAX_RETRIES = 8
MODEL = "o4-mini"


@dataclass
class ProofState:
    user_query: str
    assumptions: list[str] = field(default_factory=list)
    theorem_statement: str = ""
    lean_theorem_sig: str = ""
    informal_sketch: str = ""
    lean_proof: str = ""
    theorem_shape: str = "other"
    status: str = "init"
    last_error: str = ""
    last_error_type: ErrorType = ErrorType.UNKNOWN
    last_error_detail: dict = field(default_factory=dict)
    retries: int = 0
    history: list[dict] = field(default_factory=list)
    # Tracks all proof texts tried this session — prevents repeating same failed attempt
    failed_attempts: list[str] = field(default_factory=list)


class ProofAssistantAgent:
    def __init__(self, lean_repl: LeanREPL, verbose: bool = True):
        self.client = OpenAI()
        self.lean = lean_repl
        self.verbose = verbose
        self.memory = StrategyMemory()

    def run(self, user_query: str, user_callback=None) -> ProofState:
        state = ProofState(user_query=user_query)
        self._log("=== Proof Assistant Agent Started ===")
        self._log(f"Query: {user_query}")

        stats = self.memory.get_stats()
        if stats:
            self._log(f"[Memory] Loaded strategies: {stats}")

        state = self._extract_assumptions(state, user_callback)
        state = self._generate_sketch(state)
        state = self._prove_and_verify(state, user_callback)
        return state

    # ── Stage 1 ──────────────────────────────────────────────────────────── #
    def _extract_assumptions(self, state: ProofState, user_callback) -> ProofState:
        state.status = "extracting_assumptions"
        self._log("\n[Stage 1] Extracting assumptions...")
        response = self._call_llm(
            system=ASSUMPTION_EXTRACTOR_PROMPT,
            user=f"Statement: {state.user_query}"
        )
        parsed = self._parse_json(response)
        state.assumptions      = parsed.get("assumptions", [])
        state.theorem_statement = parsed.get("theorem_statement", state.user_query)
        state.lean_theorem_sig  = parsed.get("lean_signature", "")
        clarifications          = parsed.get("clarifications", [])

        state.theorem_shape = self.memory.classify_theorem(
            state.theorem_statement, state.lean_theorem_sig
        )

        self._log(f"Assumptions: {state.assumptions}")
        self._log(f"Lean signature: {state.lean_theorem_sig}")
        self._log(f"[Memory] Theorem shape: '{state.theorem_shape}'")

        if clarifications and user_callback:
            for q in clarifications:
                answer = user_callback(state, f"Clarification needed: {q}")
                state.assumptions.append(f"{q} -> {answer}")
        return state

    # ── Stage 2 ──────────────────────────────────────────────────────────── #
    def _generate_sketch(self, state: ProofState) -> ProofState:
        state.status = "sketching"
        self._log("\n[Stage 2] Generating informal proof sketch...")

        memory_context = self.memory.format_for_prompt(state.theorem_shape)
        if memory_context:
            self._log(f"[Memory] Injecting {state.theorem_shape} strategies into sketch prompt.")

        response = self._call_llm(
            system=SKETCH_PROMPT + memory_context,
            user=json.dumps({
                "theorem": state.theorem_statement,
                "assumptions": state.assumptions,
                "lean_signature": state.lean_theorem_sig,
            }),
        )
        parsed = self._parse_json(response)
        state.informal_sketch = parsed.get("sketch", "")
        self._log(f"Sketch: {state.informal_sketch[:200]}...")
        return state

    # ── Stage 3 + 4 ──────────────────────────────────────────────────────── #
    def _prove_and_verify(self, state: ProofState, user_callback) -> ProofState:
        state.status = "formalizing"
        self._log("\n[Stage 3] Generating Lean 4 proof...")

        memory_context = self.memory.format_for_prompt(state.theorem_shape)
        state.lean_proof = self._generate_full_proof(state, memory_context)
        self._log(f"\nInitial Lean proof:\n{state.lean_proof}")

        state.status = "verifying"
        self._log("\n[Stage 4] Verification + correction loop...")

        while state.retries < MAX_RETRIES:
            result = self.lean.check(state.lean_proof)
            self._log(f"\n  Lean check #{state.retries + 1}: {'OK' if result.success else 'Error'}")

            if result.success:
                state.status = "done"
                self._log("Proof verified!")
                self._store_success(state)
                return state

            # Record error
            state.last_error       = result.error
            state.last_error_type  = result.error_type
            state.last_error_detail = result.error_detail
            state.retries += 1

            self._log(f"  Error type: {result.error_type.value}")
            self._log(f"  Error: {result.error[:300]}")
            if result.error_detail.get("suggestion"):
                self._log(f"  Suggestion: {result.error_detail['suggestion']}")

            # Track this failed attempt
            state.failed_attempts.append(state.lean_proof)

            # Max retries — escalate to user
            if state.retries >= MAX_RETRIES:
                if user_callback:
                    msg = (
                        f"Stuck after {state.retries} attempts.\n"
                        f"Theorem: {state.theorem_statement}\n"
                        f"Error type: {result.error_type.value}\n"
                        f"Last error:\n{state.last_error}\n"
                        f"Proof so far:\n{state.lean_proof}\n"
                        "Provide a Mathlib lemma name, a tactic hint, or press Enter to skip:"
                    )
                    hint = user_callback(state, msg)
                    if hint and hint.strip().lower() not in ("", "sorry"):
                        state.history.append({"hint": hint})
                        state.lean_proof = self._correct_with_hint(state, hint)
                        state.retries = 0
                        state.failed_attempts.clear()
                        continue
                state.status = "escalated"
                break

            self._log("  Correcting...")
            state.lean_proof = self._correct_proof(state)
            self._log(f"  New proof:\n{state.lean_proof}")

        state.status = "done"
        return state

    # ── LLM calls ────────────────────────────────────────────────────────── #
    def _generate_full_proof(self, state: ProofState, memory_context: str = "") -> str:
        response = self._call_llm(
            system=FULL_PROOF_PROMPT + memory_context,
            user=json.dumps({
                "theorem_statement": state.theorem_statement,
                "lean_signature": state.lean_theorem_sig,
                "assumptions": state.assumptions,
                "informal_sketch": state.informal_sketch,
            }),
        )
        parsed = self._parse_json(response)
        return parsed.get("lean_proof", "")

    def _correct_proof(self, state: ProofState) -> str:
        """Error-type-aware correction, with failed-attempt history injected."""
        system_prompt = correction_prompt_for_error_type(
            state.last_error_type,
            state.last_error_detail
        )
        # Summarise what has already been tried so the model doesn't repeat
        tried_summary = ""
        if state.failed_attempts:
            tried_summary = "\n\nALREADY TRIED AND FAILED (do NOT repeat these):\n"
            for i, attempt in enumerate(state.failed_attempts[-4:], 1):  # last 4 max
                # Extract just the proof tactic body to keep tokens low
                short = attempt.strip().split("\n")[-1][:120]
                tried_summary += f"  Attempt {i}: ...{short}\n"

        response = self._call_llm(
            system=system_prompt + tried_summary,
            user=json.dumps({
                "lean_signature": state.lean_theorem_sig,
                "current_proof": state.lean_proof,
                "lean_error": state.last_error,
                "error_type": state.last_error_type.value,
                "error_detail": state.last_error_detail,
                "informal_sketch": state.informal_sketch,
                "retry_number": state.retries,
                "theorem_shape": state.theorem_shape,
            }),
        )
        parsed = self._parse_json(response)
        return parsed.get("lean_proof", state.lean_proof)

    def _correct_with_hint(self, state: ProofState, hint: str) -> str:
        response = self._call_llm(
            system=CORRECTION_PROMPT + "\nThe user has provided a hint — prioritize it above all else.",
            user=json.dumps({
                "lean_signature": state.lean_theorem_sig,
                "current_proof": state.lean_proof,
                "lean_error": state.last_error,
                "user_hint": hint,
            }),
        )
        parsed = self._parse_json(response)
        return parsed.get("lean_proof", state.lean_proof)

    def _store_success(self, state: ProofState):
        try:
            resp = self._call_llm(
                system="Describe this Lean 4 proof strategy in ONE sentence. Return only that sentence.",
                user=f"Theorem: {state.theorem_statement}\nProof:\n{state.lean_proof}"
            )
            strategy = resp.strip()[:200]
        except Exception:
            strategy = f"Proved using: {state.lean_proof[:100]}"

        self.memory.store_success(
            theorem_shape=state.theorem_shape,
            theorem_statement=state.theorem_statement,
            lean_signature=state.lean_theorem_sig,
            successful_proof=state.lean_proof,
            proof_strategy=strategy,
            retries_needed=state.retries,
        )
        self._log(f"[Memory] Stored successful strategy for '{state.theorem_shape}'.")

    # ── Utilities ─────────────────────────────────────────────────────────── #
    def _call_llm(self, system: str, user: str) -> str:
        response = self.client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content

    def _parse_json(self, text: str) -> dict:
        text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except Exception:
                    pass
        return {}

    def _log(self, msg: str):
        if self.verbose:
            print(msg)