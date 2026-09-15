"""Pure JSON object validation shared by offline evidence readers."""


def unique_object(pairs):
    """Build a JSON object, rejecting repeated keys even when values agree."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result
