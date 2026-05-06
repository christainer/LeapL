# Leap

A programming language I built for my final project.

---

## How to run

    python3 leapinterpreter.py HelloWorld.leap

That's it. No installation needed.

---

## Programs

- HelloWorld.leap - basic variables, functions, and conditionals
- DataPipeline.leap - the pipe operator
- Fizzbuzz.leap - FizzBuzz
- Fibonacci.leap - recursion and error handling
- Errorpipeline.leap - Ok, Err, and the ? operator all together

---

## Quick example

    fn safeDivide(a, b) {
        if b == 0 { Err("divide by zero") } else { Ok(a / b) }
    }

    fn main() {
        let result =
            10
            |> fn(x) { safeDivide(x, 2) }?
            |> fn(x) { Ok(x + 5) }?
        Ok(result)
    }

    print(str(main()))

Output: Ok(10)

---
