from copilot.benchmark import results_match
from copilot.executor import Result


def gold(cols, rows):
    return Result(columns=cols, rows=rows)


def test_order_and_names_dont_matter():
    g = gold(["name", "n"], [["a", 1], ["b", 2]])
    assert results_match(g, ["counterparty", "failed"], [["b", 2], ["a", 1]])


def test_extra_columns_are_fine():
    g = gold(["name", "n"], [["a", 1], ["b", 2]])
    assert results_match(g, ["name", "n", "notional"], [["a", 1, 10.0], ["b", 2, 20.0]])


def test_missing_column_fails():
    g = gold(["name", "n"], [["a", 1], ["b", 2]])
    assert not results_match(g, ["name"], [["a"], ["b"]])


def test_row_alignment_matters():
    g = gold(["name", "n"], [["a", 1], ["b", 2]])
    assert not results_match(g, ["name", "n"], [["a", 2], ["b", 1]])


def test_numbers_compare_rounded():
    assert results_match(gold(["x"], [[1.0000001]]), ["y"], [[1]])
    assert not results_match(gold(["x"], [[1.5]]), ["y"], [[1.6]])


def test_row_count_must_match():
    assert not results_match(gold(["x"], [[1], [2]]), ["x"], [[1]])
