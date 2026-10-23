import pytest
from gw.decision.debiasing import cyclic_permutations

def test_cyclic_permutations_empty():
    assert cyclic_permutations([]) == ()
    assert cyclic_permutations([], include_reverse=False) == ()

def test_cyclic_permutations_single():
    assert cyclic_permutations([1]) == ((1,),)
    assert cyclic_permutations([1], include_reverse=False) == ((1,),)

def test_cyclic_permutations_multiple_no_reverse():
    options = [1, 2, 3]
    result = cyclic_permutations(options, include_reverse=False)
    assert result == (
        (1, 2, 3),
        (2, 3, 1),
        (3, 1, 2)
    )

def test_cyclic_permutations_multiple_with_reverse():
    options = [1, 2, 3]
    result = cyclic_permutations(options, include_reverse=True)
    assert result == (
        (1, 2, 3),
        (2, 3, 1),
        (3, 1, 2),
        (3, 2, 1),
        (2, 1, 3),
        (1, 3, 2)
    )

def test_cyclic_permutations_uniqueness():
    # If the input has duplicate cyclic shifts, the output should still only have unique tuples
    # For example [1, 1] without reverse
    # shift 0: (1, 1)
    # shift 1: (1, 1)
    options = [1, 1]
    result = cyclic_permutations(options, include_reverse=False)
    assert result == ((1, 1),)

    options = [1, 2, 1, 2]
    result = cyclic_permutations(options, include_reverse=False)
    assert result == (
        (1, 2, 1, 2),
        (2, 1, 2, 1)
    )

    # For reverse:
    # reverse of [1, 1] is [1, 1]
    options = [1, 1]
    result = cyclic_permutations(options, include_reverse=True)
    assert result == ((1, 1),)

def test_cyclic_permutations_type():
    options = ["A", "B"]
    result = cyclic_permutations(options, include_reverse=False)
    assert result == (
        ("A", "B"),
        ("B", "A")
    )
