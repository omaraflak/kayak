import sys
from tool import get_weekly_moon_phases

def test_default_current_week():
    res = get_weekly_moon_phases()
    assert "🌙 Moon Phases" in res
    assert "| Date | Day | Phase | Illumination | Lunar Age |" in res
    lines = [l for l in res.split("\n") if l.startswith("| 20")]
    assert len(lines) == 7
    print("✓ test_default_current_week passed")

def test_specific_date():
    res = get_weekly_moon_phases(start_date="2024-01-11", days=7)
    assert "2024-01-11" in res
    assert "🌑 New Moon" in res
    print("✓ test_specific_date passed")

def test_full_moon_date():
    res = get_weekly_moon_phases(start_date="2024-01-25", days=1)
    assert "2024-01-25" in res
    assert "🌕 Full Moon" in res
    print("✓ test_full_moon_date passed")

def test_invalid_date():
    res = get_weekly_moon_phases(start_date="not-a-date")
    assert "Error: Invalid date format" in res
    print("✓ test_invalid_date passed")

def test_invalid_days():
    res = get_weekly_moon_phases(days=50)
    assert "Error: Argument 'days' must be an integer between 1 and 31." in res
    print("✓ test_invalid_days passed")

if __name__ == "__main__":
    try:
        test_default_current_week()
        test_specific_date()
        test_full_moon_date()
        test_invalid_date()
        test_invalid_days()
        print("\nAll verification tests passed! ✨")
        sys.exit(0)
    except Exception as e:
        print(f"Test failed: {e}", file=sys.stderr)
        sys.exit(1)
