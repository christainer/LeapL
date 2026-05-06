#!/usr/bin/env python3
"""
LEAP Interpreter
A simple data pipeline language built as a student project.

What this interpreter does:
  1. Lexer   -- reads source text and produces a list of tokens
  2. Parser  -- reads tokens and builds a tree of nodes
  3. Evaluator -- walks the tree and computes a result

Supported features:
  let x = expr          -- variable binding
  fn name(a, b) { }     -- named function
  fn(a) { }             -- anonymous function (used in pipelines)
  if cond { } else { }  -- conditional expression
  x |> f                -- pipe: passes x as the argument to f
  Ok(v) / Err(e)        -- result type for safe error handling
  expr?                 -- unwrap Ok, or return Err early from function
  print, str, map, filter, range, len  -- built-in functions

Usage:
  python3 leapinterpreter.py program.leap
"""

import sys, os


# PART 1 -- RUNTIME VALUES
# These are the special values the language can produce.

class Ok:
    def __init__(self, val): self.val = val
    def __repr__(self): return f"Ok({show(self.val)})"

class Err:
    def __init__(self, val): self.val = val
    def __repr__(self): return f"Err({show(self.val)})"

class LeapFn:
    """A user-defined function with its captured environment (closure)."""
    def __init__(self, name, params, body, env):
        self.name   = name or "<fn>"
        self.params = params
        self.body   = body
        self.env    = env   # the scope where the function was defined
    def __repr__(self): return f"<fn {self.name}>"

def show(v):
    """Turn any Leap value into a printable string."""
    if v is True:  return "true"
    if v is False: return "false"
    if v is None:  return "null"
    if isinstance(v, float) and v == int(v): return str(int(v))
    if isinstance(v, list): return "[" + ", ".join(show(x) for x in v) + "]"
    return str(v)


# PART 2 -- CONTROL FLOW HELPERS
# These exceptions are used internally to implement return and ?

class EarlyReturn(Exception):
    """Used to implement early return from a function."""
    def __init__(self, val): self.val = val

class PropagateErr(Exception):
    """Used to implement ? -- bubbles an Err up to the function boundary."""
    def __init__(self, err): self.err = err


# PART 3 -- LEXER  (source text -> list of tokens)

class Token:
    def __init__(self, kind, value, line):
        self.kind  = kind    # e.g. 'INT', 'PLUS', 'LET'
        self.value = value   # the actual text or number
        self.line  = line

# Words that have special meaning in Leap
KEYWORDS = {'let', 'fn', 'if', 'else', 'and', 'or', 'not', 'true', 'false'}

# After these tokens a newline does NOT end the statement
NO_BREAK = {'LBRACE', 'COMMA', 'PIPE', 'PLUS', 'MINUS', 'STAR', 'SLASH',
            'ASSIGN', 'EQ', 'NEQ', 'LT', 'GT', 'LTE', 'GTE',
            'AND', 'OR', 'NOT', 'ELSE', 'NL'}

def lex(src):
    tokens = []
    i      = 0
    line   = 1
    depth  = 0   # tracks open parens/brackets/braces

    def last_kind():
        return tokens[-1].kind if tokens else 'NL'

    while i < len(src):
        c = src[i]

        # -- line comments
        if src[i:i+2] == '--':
            while i < len(src) and src[i] != '\n':
                i += 1
            continue

        # whitespace
        if c in ' \t\r':
            i += 1
            continue

        # newline -- only treated as a statement end in certain situations
        if c == '\n':
            line += 1
            i    += 1
            if depth > 0:
                continue    # inside brackets, newlines are ignored
            # if the next non-whitespace starts with |> , it's a continuation
            j = i
            while j < len(src) and src[j] in ' \t':
                j += 1
            if src[j:j+2] == '|>':
                continue
            # only insert a NL token if the previous token can end a statement
            if last_kind() not in NO_BREAK:
                tokens.append(Token('NL', '\n', line))
            continue

        # string literals
        if c == '"':
            i += 1
            buf = []
            while i < len(src) and src[i] != '"':
                if src[i] == '\\' and i + 1 < len(src):
                    esc = src[i + 1]
                    buf.append({'n': '\n', 't': '\t', '"': '"', '\\': '\\'}.get(esc, esc))
                    i += 2
                else:
                    buf.append(src[i])
                    i += 1
            i += 1   # closing quote
            tokens.append(Token('STRING', ''.join(buf), line))
            continue

        # numbers
        if c.isdigit():
            j = i
            while j < len(src) and src[j].isdigit():
                j += 1
            if j < len(src) and src[j] == '.' and j + 1 < len(src) and src[j+1].isdigit():
                j += 1
                while j < len(src) and src[j].isdigit():
                    j += 1
                tokens.append(Token('FLOAT', float(src[i:j]), line))
            else:
                tokens.append(Token('INT', int(src[i:j]), line))
            i = j
            continue

        # identifiers and keywords
        if c.isalpha() or c == '_':
            j = i
            while j < len(src) and (src[j].isalnum() or src[j] == '_'):
                j += 1
            word = src[i:j]
            # Ok and Err are treated like keywords
            if word in KEYWORDS:     kind = word.upper()
            elif word in ('Ok','Err'): kind = word.upper()
            else:                     kind = 'IDENT'
            tokens.append(Token(kind, word, line))
            i = j
            continue

        # two-character operators
        two = src[i:i+2]
        two_map = {'|>': 'PIPE', '==': 'EQ', '!=': 'NEQ', '<=': 'LTE', '>=': 'GTE'}
        if two in two_map:
            tokens.append(Token(two_map[two], two, line))
            i += 2
            continue

        # single-character tokens
        single = {
            '+': 'PLUS', '-': 'MINUS', '*': 'STAR', '/': 'SLASH', '%': 'MOD',
            '<': 'LT',   '>': 'GT',    '=': 'ASSIGN', '?': 'QUESTION',
            '(': 'LPAREN', ')': 'RPAREN',
            '{': 'LBRACE', '}': 'RBRACE',
            '[': 'LBRACKET', ']': 'RBRACKET',
            ',': 'COMMA',
        }
        if c in single:
            kind = single[c]
            if kind in ('LPAREN', 'LBRACKET', 'LBRACE'): depth += 1
            if kind in ('RPAREN', 'RBRACKET', 'RBRACE'): depth -= 1
            tokens.append(Token(kind, c, line))
            i += 1
            continue

        raise SyntaxError(f"Unexpected character {c!r} on line {line}")

    tokens.append(Token('EOF', None, line))
    return tokens


# PART 4 -- AST NODES  (the tree the parser builds)

# Each class is one kind of node in the syntax tree.
# The evaluator walks these nodes to compute results.

class LetStmt:
    def __init__(self, name, value):       self.name = name;   self.value = value
class FnStmt:
    def __init__(self, name, params, body): self.name = name;  self.params = params; self.body = body
class ExprStmt:
    def __init__(self, expr):              self.expr = expr

class Block:
    def __init__(self, stmts):             self.stmts = stmts
class IfExpr:
    def __init__(self, cond, then, els):   self.cond = cond;   self.then = then;  self.els = els
class FnExpr:
    def __init__(self, params, body):      self.params = params; self.body = body
class PipeExpr:
    def __init__(self, left, right):       self.left = left;   self.right = right
class PropagateExpr:
    def __init__(self, expr):              self.expr = expr
class CallExpr:
    def __init__(self, fn, args):          self.fn = fn;       self.args = args
class BinOp:
    def __init__(self, left, op, right):   self.left = left;   self.op = op; self.right = right
class UnaryOp:
    def __init__(self, op, val):           self.op = op;       self.val = val
class VarRef:
    def __init__(self, name):              self.name = name
class Lit:
    def __init__(self, val):               self.val = val      # int, float, str, bool
class OkLit:
    def __init__(self, val):               self.val = val
class ErrLit:
    def __init__(self, val):               self.val = val
class ListLit:
    def __init__(self, items):             self.items = items


# PART 5 -- PARSER  (token list -> AST)

class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos    = 0

    # -- helpers
    def cur(self):
        return self.tokens[self.pos]

    def peek(self):
        p = self.pos + 1
        return self.tokens[p] if p < len(self.tokens) else self.tokens[-1]

    def eat(self, kind=None):
        tok = self.tokens[self.pos]
        if kind and tok.kind != kind:
            raise SyntaxError(f"Expected {kind!r} but got {tok.kind!r} ({tok.value!r}) on line {tok.line}")
        self.pos += 1
        return tok

    def skip_newlines(self):
        while self.cur().kind == 'NL':
            self.eat()

    def end_stmt(self):
        if self.cur().kind == 'NL':
            self.eat()

    # -- top level
    def parse(self):
        self.skip_newlines()
        stmts = []
        while self.cur().kind != 'EOF':
            s = self.statement()
            if s: stmts.append(s)
            self.skip_newlines()
        return Block(stmts)

    def block(self):
        self.eat('LBRACE')
        stmts = []
        while self.cur().kind not in ('RBRACE', 'EOF'):
            self.skip_newlines()
            if self.cur().kind in ('RBRACE', 'EOF'): break
            s = self.statement()
            if s: stmts.append(s)
        self.skip_newlines()
        self.eat('RBRACE')
        return Block(stmts)

    # -- statements
    def statement(self):
        k = self.cur().kind

        if k == 'NL':
            self.eat()
            return None

        if k == 'LET':
            self.eat()
            name = self.eat('IDENT').value
            self.eat('ASSIGN')
            val = self.expr()
            self.end_stmt()
            return LetStmt(name, val)

        if k == 'FN' and self.peek().kind == 'IDENT':
            self.eat()
            name   = self.eat('IDENT').value
            self.eat('LPAREN')
            params = self.param_list()
            self.eat('RPAREN')
            body   = self.block()
            return FnStmt(name, params, body)

        e = self.expr()
        self.end_stmt()
        return ExprStmt(e)

    def param_list(self):
        params = []
        if self.cur().kind != 'RPAREN':
            params.append(self.eat('IDENT').value)
            while self.cur().kind == 'COMMA':
                self.eat()
                params.append(self.eat('IDENT').value)
        return params

    # -- expressions (each level calls the next for precedence)
    def expr(self):
        return self.pipe()

    def pipe(self):
        """
        Handles |> and ? together.
        x |> f      becomes  PipeExpr(x, f)
        x |> f?     becomes  PropagateExpr(PipeExpr(x, f))
        x?          becomes  PropagateExpr(x)
        """
        node = self.logic()
        while True:
            if self.cur().kind == 'PIPE':
                self.eat()
                right = self.logic()     # parse right side WITHOUT consuming ?
                node  = PipeExpr(node, right)
                if self.cur().kind == 'QUESTION':
                    self.eat()
                    node = PropagateExpr(node)
            elif self.cur().kind == 'QUESTION':
                self.eat()
                node = PropagateExpr(node)
            else:
                break
        return node

    def logic(self):
        left = self.compare()
        while self.cur().kind in ('AND', 'OR'):
            op   = self.eat().value
            left = BinOp(left, op, self.compare())
        return left

    def compare(self):
        left = self.addition()
        while self.cur().kind in ('EQ', 'NEQ', 'LT', 'GT', 'LTE', 'GTE'):
            op   = self.eat().value
            left = BinOp(left, op, self.addition())
        return left

    def addition(self):
        left = self.multiply()
        while self.cur().kind in ('PLUS', 'MINUS'):
            op   = self.eat().value
            left = BinOp(left, op, self.multiply())
        return left

    def multiply(self):
        left = self.unary()
        while self.cur().kind in ('STAR', 'SLASH', 'MOD'):
            op   = self.eat().value
            left = BinOp(left, op, self.unary())
        return left

    def unary(self):
        if self.cur().kind == 'MINUS':
            self.eat()
            return UnaryOp('-', self.unary())
        if self.cur().kind == 'NOT':
            self.eat()
            return UnaryOp('not', self.unary())
        return self.call()

    def call(self):
        node = self.primary()
        while self.cur().kind == 'LPAREN':
            args = self.arg_list()
            node = CallExpr(node, args)
        return node

    def arg_list(self):
        self.eat('LPAREN')
        args = []
        self.skip_newlines()
        while self.cur().kind != 'RPAREN':
            args.append(self.expr())
            self.skip_newlines()
            if self.cur().kind == 'COMMA':
                self.eat()
                self.skip_newlines()
        self.eat('RPAREN')
        return args

    def primary(self):
        k = self.cur().kind

        if k == 'INT':    return Lit(self.eat().value)
        if k == 'FLOAT':  return Lit(self.eat().value)
        if k == 'STRING': return Lit(self.eat().value)
        if k == 'TRUE':   self.eat(); return Lit(True)
        if k == 'FALSE':  self.eat(); return Lit(False)

        if k == 'OK':
            self.eat(); self.eat('LPAREN')
            v = self.expr(); self.eat('RPAREN')
            return OkLit(v)

        if k == 'ERR':
            self.eat(); self.eat('LPAREN')
            v = self.expr(); self.eat('RPAREN')
            return ErrLit(v)

        if k == 'LBRACKET':
            self.eat()
            items = []
            self.skip_newlines()
            while self.cur().kind != 'RBRACKET':
                items.append(self.expr())
                self.skip_newlines()
                if self.cur().kind == 'COMMA':
                    self.eat()
                    self.skip_newlines()
            self.eat('RBRACKET')
            return ListLit(items)

        if k == 'LPAREN':
            self.eat()
            e = self.expr()
            self.eat('RPAREN')
            return e

        if k == 'LBRACE':
            return self.block()

        if k == 'IF':
            self.eat()
            cond = self.expr()
            then = self.block()
            els  = None
            self.skip_newlines()
            if self.cur().kind == 'ELSE':
                self.eat()
                els = self.block() if self.cur().kind != 'IF' else self.primary()
            return IfExpr(cond, then, els)

        if k == 'FN':
            self.eat()
            self.eat('LPAREN')
            params = self.param_list()
            self.eat('RPAREN')
            body   = self.block()
            return FnExpr(params, body)

        if k == 'IDENT':
            return VarRef(self.eat().value)

        raise SyntaxError(f"Unexpected {self.cur().kind!r} ({self.cur().value!r}) on line {self.cur().line}")


# PART 6 -- ENVIRONMENT  (variable scopes)


class Env:
    """
    A scope is a dictionary of variable names to values.
    Each scope has a parent (the scope it was created inside).
    Looking up a variable checks the current scope first,
    then walks up to the parent until it finds it.
    """
    def __init__(self, parent=None):
        self.vars   = {}
        self.parent = parent

    def get(self, name):
        if name in self.vars:
            return self.vars[name]
        if self.parent:
            return self.parent.get(name)
        raise NameError(f"'{name}' is not defined")

    def set(self, name, value):
        self.vars[name] = value


# PART 7 -- EVALUATOR  (walks the AST and computes results)


class Evaluator:
    def __init__(self):
        self.globals = Env()
        self.globals.set('print',  lambda args: print(show(args[0])) or None)
        self.globals.set('str',    lambda args: show(args[0]))
        self.globals.set('len',    lambda args: len(args[0]))
        self.globals.set('range',  lambda args: list(range(int(args[0]), int(args[1]))) if len(args) > 1 else list(range(int(args[0]))))
        self.globals.set('map',    lambda args: [self.call(args[1], [x]) for x in args[0]])
        self.globals.set('filter', lambda args: [x for x in args[0] if self.truthy(self.call(args[1], [x]))])

    def run(self, block):
        try:
            self.exec_block(block, self.globals)
        except PropagateErr as e:
            print(f"Unhandled error: Err({e.err!r})")

    def exec_block(self, block, env):
        result = None
        for stmt in block.stmts:
            if stmt: result = self.exec(stmt, env)
        return result

    def exec(self, node, env):
        if isinstance(node, LetStmt):
            val = self.eval(node.value, env)
            env.set(node.name, val)
            return val

        if isinstance(node, FnStmt):
            fn = LeapFn(node.name, node.params, node.body, env)
            env.set(node.name, fn)
            return fn

        if isinstance(node, ExprStmt):
            return self.eval(node.expr, env)

        return self.eval(node, env)

    def eval(self, node, env):
        # literals
        if isinstance(node, Lit):          return node.val
        if isinstance(node, OkLit):        return Ok(self.eval(node.val, env))
        if isinstance(node, ErrLit):       return Err(self.eval(node.val, env))
        if isinstance(node, ListLit):      return [self.eval(x, env) for x in node.items]
        if isinstance(node, VarRef):       return env.get(node.name)

        # anonymous function becomes a LeapFn object
        if isinstance(node, FnExpr):
            return LeapFn(None, node.params, node.body, env)

        # a block evaluates each statement; last result is the value
        if isinstance(node, Block):
            child = Env(env)
            return self.exec_block(node, child)

        # if/else -- both branches are expressions
        if isinstance(node, IfExpr):
            if self.truthy(self.eval(node.cond, env)):
                return self.exec_block(node.then, Env(env))
            if node.els:
                if isinstance(node.els, IfExpr):
                    return self.eval(node.els, env)
                return self.exec_block(node.els, Env(env))
            return None

        # pipe: x |> f  becomes  f(x)
        #       x |> f(a, b)  becomes  f(x, a, b)
        if isinstance(node, PipeExpr):
            left = self.eval(node.left, env)
            if isinstance(node.right, CallExpr):
                fn   = self.eval(node.right.fn, env)
                args = [left] + [self.eval(a, env) for a in node.right.args]
            else:
                fn   = self.eval(node.right, env)
                args = [left]
            return self.call(fn, args)

        # ? operator: Ok(v) -> v, Err(e) -> stop and return Err(e)
        if isinstance(node, PropagateExpr):
            val = self.eval(node.expr, env)
            if isinstance(val, Ok):  return val.val
            if isinstance(val, Err): raise PropagateErr(val.val)
            return val   # plain values pass through

        # function call
        if isinstance(node, CallExpr):
            fn   = self.eval(node.fn, env)
            args = [self.eval(a, env) for a in node.args]
            return self.call(fn, args)

        # binary operators
        if isinstance(node, BinOp):
            return self.binop(node, env)

        # unary operators
        if isinstance(node, UnaryOp):
            v = self.eval(node.val, env)
            if node.op == '-':   return -v
            if node.op == 'not': return not self.truthy(v)

        if isinstance(node, (LetStmt, FnStmt, ExprStmt)):
            return self.exec(node, env)

        raise RuntimeError(f"Don't know how to evaluate {type(node).__name__}")

    def binop(self, node, env):
        # short-circuit: don't evaluate right side if not needed
        if node.op == 'and':
            left = self.eval(node.left, env)
            return left if not self.truthy(left) else self.eval(node.right, env)
        if node.op == 'or':
            left = self.eval(node.left, env)
            return left if self.truthy(left) else self.eval(node.right, env)

        left  = self.eval(node.left, env)
        right = self.eval(node.right, env)

        if node.op == '+':
            if isinstance(left, str) or isinstance(right, str):
                return show(left) + show(right)
            return left + right
        if node.op == '-':  return left - right
        if node.op == '*':  return left * right
        if node.op == '/':
            if right == 0: raise ZeroDivisionError("division by zero")
            return left / right
        if node.op == '%':  return left % right
        if node.op == '==': return left == right
        if node.op == '!=': return left != right
        if node.op == '<':  return left < right
        if node.op == '>':  return left > right
        if node.op == '<=': return left <= right
        if node.op == '>=': return left >= right

    def call(self, fn, args):
        # built-in functions are just Python lambdas
        if callable(fn):
            return fn(args)

        # user-defined functions run in their own scope
        if isinstance(fn, LeapFn):
            call_env = Env(fn.env)
            for param, arg in zip(fn.params, args):
                call_env.set(param, arg)
            try:
                return self.exec_block(fn.body, call_env)
            except EarlyReturn as r:
                return r.val
            except PropagateErr as pe:
                # ? hit an Err inside this function
                # the function itself returns Err(e) to its caller
                return Err(pe.err)

        raise RuntimeError(f"'{show(fn)}' is not a function")

    def truthy(self, v):
        if v is None or v is False: return False
        if isinstance(v, (int, float)) and v == 0: return False
        return True


# PART 8 -- MAIN  (tie it all together)

def run_file(path):
    with open(path) as f:
        source = f.read()
    try:
        tokens    = lex(source)
        tree      = Parser(tokens).parse()
        Evaluator().run(tree)
    except SyntaxError   as e: print(f"Syntax error  : {e}")
    except NameError     as e: print(f"Name error    : {e}")
    except ZeroDivisionError: print("Math error    : division by zero")
    except RuntimeError  as e: print(f"Runtime error : {e}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 leapinterpreter.py program.leap")
        sys.exit(1)
    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"File not found: {path!r}")
        sys.exit(1)
    run_file(path)