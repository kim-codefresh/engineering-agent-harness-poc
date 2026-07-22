from . import call_llm, gather_evidence, merge_pr, open_pr, record_outcome, run_tests

REGISTRY = {
    "call_llm":        call_llm,
    "gather_evidence": gather_evidence,
    "open_pr":         open_pr,
    "merge_pr":        merge_pr,
    "record_outcome":  record_outcome,
    "run_tests":       run_tests,
}
