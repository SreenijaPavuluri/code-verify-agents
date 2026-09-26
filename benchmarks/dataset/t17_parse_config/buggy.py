def parse_config(text):
    result = {}
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        key, value = line.split("=")
        result[key] = value
    return result
