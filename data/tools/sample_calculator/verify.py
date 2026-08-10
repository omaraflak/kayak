import sys
from tool import calculate


def test_basic_arithmetic():
    res = calculate("2 + 2")
    assert "Result: 4" in res, f"Expected 'Result: 4', got {res}"
    print("✓ Basic arithmetic passed")


def test_math_functions():
    res = calculate("sqrt(144)")
    assert "Result: 12.0" in res, f"Expected 'Result: 12.0', got {res}"
    print("✓ Math functions passed")


def test_powers_and_constants():
    res = calculate("2 ** 8")
    assert "Result: 256" in res, f"Expected 'Result: 256', got {res}"
    print("✓ Powers passed")


def test_error_handling():
    res = calculate("invalid syntax + * 2")
    assert "Error" in res, f"Expected error handling, got {res}"
    print("✓ Error handling passed")


if __name__ == "__main__":
    try:
        test_basic_arithmetic()
        test_math_functions()
        test_powers_and_constants()
        test_error_handling()
        print("\nAll verification tests passed successfully! ✨")
        sys.exit(0)
    except AssertionError as e:
        print(f"\nVerification assertion failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected verification error: {e}", file=sys.stderr)
        sys.exit(1)
