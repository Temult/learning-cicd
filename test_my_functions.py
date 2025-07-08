from my_functions import add

def test_add_positive_numbers():
    """Tests that the add function works with positive numbers."""
    assert add(2, 3) == 5

def test_add_negative_numbers():
    """Tests that the add function works with negative numbers."""
    assert add(-1, -1) == -2
