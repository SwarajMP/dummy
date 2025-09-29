# codebase/example.py

def faulty_function():
    """
    This function is designed to fail with a ZeroDivisionError.
    """
    numerator = 10
    denominator = 5
    result = numerator / denominator  # This line will cause an error
    print(f"The result is: {result}")

# Run the function
faulty_function()