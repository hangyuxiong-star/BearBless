from pathlib import Path

from bearbless.evaluation import load_evaluation_cases, validate_evaluation_policy


def test_mobile_evaluation_matrix_has_ten_heterogeneous_tasks():
    cases = load_evaluation_cases(Path("evals/mobile_tasks.json"))
    assert len(cases) == 10
    assert validate_evaluation_policy(cases) == []
    assert len({case.expected_mode for case in cases}) == 3
