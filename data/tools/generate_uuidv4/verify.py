import sys
import re
import uuid
from tool import generate_uuidv4

def test_default_single_uuid():
    res = generate_uuidv4()
    lines = res.splitlines()
    assert len(lines) == 1, f"Expected 1 line, got {len(lines)}"
    # Validate UUIDv4 format (8-4-4-4-12)
    parsed = uuid.UUID(lines[0])
    assert parsed.version == 4, f"Expected UUID version 4, got {parsed.version}"
    assert lines[0] == str(parsed).lower()
    print("✓ test_default_single_uuid passed")

def test_multiple_uuids():
    count = 5
    res = generate_uuidv4(count=count)
    lines = res.splitlines()
    assert len(lines) == count, f"Expected {count} lines, got {len(lines)}"
    assert len(set(lines)) == count, "Generated UUIDs must be unique"
    for line in lines:
        parsed = uuid.UUID(line)
        assert parsed.version == 4
    print("✓ test_multiple_uuids passed")

def test_formatting_options():
    # Test uppercase
    res_upper = generate_uuidv4(uppercase=True)
    assert res_upper.isupper() or not any(c.isalpha() for c in res_upper)
    assert "-" in res_upper

    # Test remove hyphens
    res_no_hyphen = generate_uuidv4(remove_hyphens=True)
    assert "-" not in res_no_hyphen
    assert len(res_no_hyphen) == 32

    # Test combined uppercase and remove hyphens
    res_both = generate_uuidv4(uppercase=True, remove_hyphens=True)
    assert "-" not in res_both
    assert res_both.isupper() or not any(c.isalpha() for c in res_both)
    assert len(res_both) == 32

    # Test prefix
    res_prefix = generate_uuidv4(prefix="user_")
    assert res_prefix.startswith("user_")
    raw_uuid = res_prefix[5:]
    assert uuid.UUID(raw_uuid).version == 4

    print("✓ test_formatting_options passed")

def test_invalid_count():
    try:
        generate_uuidv4(count=0)
        assert False, "Should have raised ValueError for count=0"
    except ValueError as e:
        assert "Count must be" in str(e)

    try:
        generate_uuidv4(count=101)
        assert False, "Should have raised ValueError for count=101"
    except ValueError as e:
        assert "Count must be" in str(e)

    print("✓ test_invalid_count passed")

if __name__ == "__main__":
    try:
        test_default_single_uuid()
        test_multiple_uuids()
        test_formatting_options()
        test_invalid_count()
        print("\nAll verification tests passed! ✨")
        sys.exit(0)
    except AssertionError as e:
        print(f"Assertion error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
