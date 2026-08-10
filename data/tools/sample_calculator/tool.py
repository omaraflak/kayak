import math


def calculate(expression: str) -> str:
    """Evaluates a safe mathematical expression and returns the calculated numerical result.

    Args:
        expression: A mathematical expression string, such as '2 + 2', 'sqrt(144)', 'cos(3.1415 / 2)', or '2 ** 10'.
    """
    safe_env = {
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "sqrt": math.sqrt,
        "log": math.log,
        "log10": math.log10,
        "exp": math.exp,
        "pi": math.pi,
        "e": math.e,
        "abs": abs,
        "round": round,
        "pow": pow,
    }

    try:
        # Evaluate safely restricted to math builtins
        result = eval(expression, {"__builtins__": {}}, safe_env)
        return f"Result: {result}"
    except Exception as e:
        return f"Error evaluating expression '{expression}': {str(e)}"
