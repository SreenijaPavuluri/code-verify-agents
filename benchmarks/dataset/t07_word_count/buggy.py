def word_count(text):
    counts = {}
    for word in text.lower().split():
        counts[word] = 1
    return counts
