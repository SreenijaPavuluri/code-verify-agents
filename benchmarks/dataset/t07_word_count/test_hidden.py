from solution import word_count

def test_case():
    assert word_count("The the THE cat") == {"the": 3, "cat": 1}

def test_empty():
    assert word_count("   ") == {}
