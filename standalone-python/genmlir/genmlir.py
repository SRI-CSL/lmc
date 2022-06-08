from __future__ import annotations
import ast
import sys
from typing import Dict

import mlir_python.ir as mlir
from mlir_python.dialects import (
  builtin as builtin_d,
  cf as cf_d,
  func as func_d,
  python as python_d
)

# Allocate a cell
def cell_alloc(block: mlir.Block) -> mlir.Value:
    with mlir.InsertionPoint(block):
        return python_d.CellAlloc()

# Return value stored in the cell.
def cell_load(block: mlir.Block, cell: mlir.Value) -> mlir.Value:
    with mlir.InsertionPoint(block):
        return python_d.CellLoad(cell)

# Set value stored in cell.
def cell_store(block: mlir.Block, cell: mlir.Value, value: mlir.Value):
    with mlir.InsertionPoint(block):
        python_d.CellStore(cell, value)

# invoke_next(next, nextBlock, doneBlock, throwBlock) invokes the next method and returns
# a pair (bodyBlock, value) where:
# * bodyBlock is a fresh block and
# * value is a value within bodyBlock that refers to the next value in the iterator.
#
# * next is the method called to invoke next.
# * nextBlock is the block that new blocks are appended too
# *
def invoke_next(next: mlir.Value, curBlock: mlir.Block, doneBlock: mlir.Block, throwBlock: mlir.Block):
    valueType = python_d.ValueType.get()

    nextBlock = curBlock.create_after()
    with mlir.InsertionPoint(curBlock):
        cf_d.BranchOp([], nextBlock)

    bodyBlock   = nextBlock.create_after(valueType)
    exceptBlock = nextBlock.create_after(valueType)

    with mlir.InsertionPoint(nextBlock):
        python_d.InvokeOp(next, [], None, [], [], bodyBlock, exceptBlock)

    exception   = exceptBlock.arguments[0]
    with mlir.InsertionPoint(exceptBlock):
        c = python_d.IsInstance(exception, mlir.StringAttr.get("StopIteration"))
        cf_d.CondBranchOp(c, [], [exception], doneBlock, throwBlock)

    return nextBlock, bodyBlock, bodyBlock.arguments[0]

# Return the none vlaue
def truthy(block: mlir.Block, x: mlir.Value) -> mlir.Value:
    with mlir.InsertionPoint(block):
        return python_d.Truthy(x)

class BuiltinSet:
    def __init__(self,):
        self._builtins = {}
        self.addBuiltins()

    def addBuiltin(self, mlirName: str, pyName=None):
        name = pyName if pyName != None else mlirName
        self._builtins[name] = mlirName

    def addBuiltins(self):
        self.addBuiltin("abs")
        self.addBuiltin("aiter")
        self.addBuiltin("all")
        self.addBuiltin("any")
        self.addBuiltin("anext")
        self.addBuiltin("ascii")
        self.addBuiltin("bin")
        self.addBuiltin("bool_builtin", "bool")
        self.addBuiltin("breakpoint")
        self.addBuiltin("bytearray")
        self.addBuiltin("bytes")
        self.addBuiltin("callable")
        self.addBuiltin("chr")
        self.addBuiltin("classmethod")
        self.addBuiltin("compile")
        self.addBuiltin("complex")
        self.addBuiltin("delattr")
        self.addBuiltin("dict")
        self.addBuiltin("dir")
        self.addBuiltin("divmod")
        self.addBuiltin("enumerate")
        self.addBuiltin("eval")
        self.addBuiltin("exec")
        self.addBuiltin("filter")
        self.addBuiltin("float_builtin", "float")
        self.addBuiltin("format")
        self.addBuiltin("frozenset")
        self.addBuiltin("getattr")
        self.addBuiltin("globals")
        self.addBuiltin("hasattr")
        self.addBuiltin("hash")
        self.addBuiltin("help")
        self.addBuiltin("hex")
        self.addBuiltin("id")
        self.addBuiltin("input")
        self.addBuiltin("int_builtin", "int")
        self.addBuiltin("isinstance")
        self.addBuiltin("issubclass")
        self.addBuiltin("iter")
        self.addBuiltin("len")
        self.addBuiltin("list")
        self.addBuiltin("locals")
        self.addBuiltin("map")
        self.addBuiltin("max")
        self.addBuiltin("memoryview")
        self.addBuiltin("min")
        self.addBuiltin("next")
        self.addBuiltin("object")
        self.addBuiltin("oct")
        self.addBuiltin("open")
        self.addBuiltin("ord")
        self.addBuiltin("pow")
        self.addBuiltin("print")
        self.addBuiltin("property")
        self.addBuiltin("range")
        self.addBuiltin("repr")
        self.addBuiltin("reversed")
        self.addBuiltin("round")
        self.addBuiltin("set")
        self.addBuiltin("setattr")
        self.addBuiltin("slice")
        self.addBuiltin("sorted")
        self.addBuiltin("staticmethod")
        self.addBuiltin("str")
        self.addBuiltin("sum")
        self.addBuiltin("super")
        self.addBuiltin("tuple")
        self.addBuiltin("type")
        self.addBuiltin("vars")
        self.addBuiltin("zip")
        self.addBuiltin("import", "__import__")
        self.addBuiltin("scriptmain", "__name__")

    def builtin_mlir_name(self, name: str) -> str|None:
        return self._builtins.get(name)

bc = BuiltinSet()
def builtin_mlir_name(name: str) -> str|None:
    return bc.builtin_mlir_name(name)

# Represent a scope
class VariableScope:
    # Variables defined in this scope
    vars: Dict[str, None]
    # Variables referenced in this scope
    parent_vars: list[ast.Name]

    def __init__(self, vars: Dict[str, None], parentVars: Dict[str, ast.Name]):
        self.vars = vars
        self.parent_vars = []
        for v in parentVars.values():
            self.parent_vars.append(v)

class Module:
    # MLIR module
    mlir: mlir.Module
    # Maps identifiers for specific AST nodes to the scope associated
    _scope_map : Dict[int, VariableScope]
    # Maps string names to the number of variables with that name
    _vars : Dict[str, int]

    def __init__(self, mlir:mlir.Module, scope_map: Dict[int, VariableScope]):
        self.mlir = mlir
        self._scope_map = scope_map
        self._vars = {}

    # Ensure a symbol has a fresh name.
    def fresh_symbol(self, nm: str|None) -> str:
        if nm == None:
            nm = "_mlir_gen"
        else:
            # Strip out @ sign as it is used for marking count.
            nm = nm.replace('@', '')
            if nm == "":
                nm = "_mlir_gen"
        cnt = self._vars.get(nm)
        if cnt == None:
            self._vars[nm] = 0
            return nm
        else:
            self._vars[nm] = cnt+1
            return f'{nm}@{cnt}'

    # Get scope for item with given identifier
    def get_scope(self, ident: int) -> VariableScope:
        return self._scope_map[ident]

# This class is responsible for visiting statements in a function
class VariableCapture(ast.NodeVisitor):
    map: Dict[int, VariableScope]
    # Variables defined in this scope
    vars: Dict[str, None]
    # Variables referenced in this scope
    references: Dict[str, ast.Name]

    # Create variable capture visitor with given identifier and parent.
    def __init__(self, map: Dict[int, VariableScope]):
        self.map = map
        self.vars = {}
        self.references = {}

    # Record variable referenced in this scope
    def add_reference(self, ast: ast.Name):
        name = ast.id
        if (builtin_mlir_name(name) == None) and not (name in self.vars) and not (name in self.references):
            self.references[name] = ast

    # Add inner scope to this scope
    def close_scope(self, id: int, inner: VariableCapture):
        self.map[id] = inner.mkScope()
        for v in inner.references.values():
            self.add_reference(v)

    def mkScope(self) -> VariableScope:
        return VariableScope(self.vars, self.references)

    # Record variable with name defined in this scope.
    def addVar(self, ast: ast.AST, name: str):
        self.vars[name] = None
        self.references.pop(name, None)

    def addLhs(self, tgt: ast.expr):
        if isinstance(tgt, ast.Tuple):
            n = len(tgt.elts)
            for i in range(0, n):
                self.addLhs(tgt.elts[i])
        elif isinstance(tgt, ast.Name):
            self.addVar(tgt, tgt.id)
        else:
            raise Exception(f'Unexpected target {tgt.__class__.__name__} {tgt.lineno}:{tgt.col_offset}')


    # Treat expression as a left-hand side and add variables to write vars.
    def addAssignLhs(self, tgt: ast.expr):
        if isinstance(tgt, ast.Subscript):
            self.checked_visit_expr(tgt.value)
            self.checked_visit_expr(tgt.slice)
        else:
            self.addLhs(tgt)


    def addArgs(self, args: ast.arguments):
        for a in args.args:
            self.addVar(a, a.arg)
        assert(args.vararg == None)
        assert(len(args.kwonlyargs) == 0)
        assert(len(args.kw_defaults) == 0)
        assert(args.kwarg == None)
        assert(len(args.defaults) == 0)

    # Visitors for expressions
    def checked_visit_expr(self, e: ast.expr):
        r = self.visit(e)
        if r == None:
            raise Exception(f'Unsupported expression {type(e)}')

    def visit_Attribute(self, a: ast.Attribute):
        self.checked_visit_expr(a.value)
        if isinstance(a.ctx, ast.Load):
            return True
        elif isinstance(a.ctx, ast.Store):
            raise Exception(f'Store attribute unsupported.')
        elif isinstance(a.ctx, ast.Del):
            raise Exception(f'Delete attribute unsupported.')
        else:
            raise Exception(f'Unknown context {type(a.ctx)}')

    def visit_BinOp(self, e: ast.BinOp) -> bool:
        self.checked_visit_expr(e.left)
        self.checked_visit_expr(e.right)
        return True

    def visit_Call(self, c: ast.Call) -> bool:
        self.checked_visit_expr(c.func)
        for a in c.args:
            self.checked_visit_expr(a)
        for k in c.keywords:
            self.checked_visit_expr(k.value)
        return True

    def visit_Compare(self, e: ast.Compare) -> bool:
        self.checked_visit_expr(e.left)
        for r in e.comparators:
            self.checked_visit_expr(r)
        return True

    def visit_Constant(self, c: ast.Constant) -> bool:
        return True

    def visit_FormattedValue(self, fv: ast.FormattedValue) -> bool:
        self.checked_visit_expr(fv.value)
        if fv.conversion != -1:
            raise Exception(f'Conversion unsupported')
        if fv.format_spec != None:
            self.checked_visit_expr(fv.format_spec)
        return True

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> bool:
        return True # FIXME

    # See https://www.python.org/dev/peps/pep-0498/
    def visit_JoinedStr(self, s: ast.JoinedStr) -> bool:
        for a in s.values:
            self.checked_visit_expr(a)
        return True

    def visit_Lambda(self, e: ast.Lambda) -> bool:
        # Create variable capture
        var_capture = VariableCapture(self.map)
        var_capture.addArgs(e.args)
        var_capture.checked_visit_expr(e.body)
        self.close_scope(id(e), var_capture)
        return True

    def visit_List(self, e: ast.List) -> bool:
        for x in e.elts:
            self.checked_visit_expr(x)
        return True

    # List comprehensions such [f(x) for i in x for j in y] may
    # introduce multiple scopes.  We introduce the capture scopes
    # to record references.
    def visit_ListComp(self, e: ast.ListComp) -> bool:
        # Get generatores
        gen = e.generators
        # Store current capture
        var_capture = self
        # List of variable capture values with outer capture first.
        captures = [None]*len(gen)
        for i in range(len(gen)):
            g = gen[i]
            assert(not g.is_async)
            assert(len(g.ifs) == 0)
            var_capture.checked_visit_expr(g.iter)

            captures[i] = var_capture
            var_capture = VariableCapture(self.map)
            var_capture.addLhs(g.target)

        var_capture.checked_visit_expr(e.elt)

        # Pop captures
        for i in range(len(gen)-1, -1, -1):
            outer = captures[i]
            outer.close_scope(id(gen[i]), var_capture)
            var_capture = outer

        return True

    def visit_Name(self, node: ast.Name):
        self.add_reference(node)
        return True

    def visit_Slice(self, e: ast.Slice):
        if e.upper != None:
            self.checked_visit_expr(e.upper)
        if e.lower != None:
            self.checked_visit_expr(e.lower)
        if e.step != None:
            self.checked_visit_expr(e.step)
        return True

    def visit_Subscript(self, e: ast.Subscript):
        self.checked_visit_expr(e.value)
        self.checked_visit_expr(e.slice)
        return True

    def visit_Tuple(self, e: ast.Tuple):
        for e in e.elts:
            self.checked_visit_expr(e)
        return True

    def visit_UnaryOp(self, e: ast.UnaryOp) -> bool:
        self.checked_visit_expr(e.operand)
        return True

    # Visitors for statements

    def visit_Assign(self, a: ast.Assign) -> bool:
        self.checked_visit_expr(a.value)
        for tgt in a.targets:
            self.addAssignLhs(tgt)
        return True

    def visit_AugAssign(self, a: ast.AugAssign):
        self.addAssignLhs(a.target)
        self.checked_visit_expr(a.value)
        return True

    def visit_Expr(self, e: ast.Expr):
        self.checked_visit_expr(e.value)
        return True

    def visit_For(self, s: ast.For) -> bool:
        self.addLhs(s.target)
        self.checked_visit_expr(s.iter)
        self.visit_stmts(s.body)
        self.visit_stmts(s.orelse)
        return True

    def visit_FunctionDef(self, s: ast.FunctionDef) -> bool:
        self.addVar(s, s.name)

        # Create variable capture
        var_capture = VariableCapture(self.map)
        var_capture.addArgs(s.args)
        var_capture.visit_stmts(s.body)
        self.close_scope(id(s), var_capture)
        return True

    def visit_If(self, s: ast.If) -> bool:
        self.checked_visit_expr(s.test)
        if s.body:
            self.visit_stmts(s.body)
        if s.orelse:
            self.visit_stmts(s.orelse)
        return True

    def visit_Import(self, s: ast.Import):
        for a in s.names:
            self.addVar(a, a.asname or a.name)
        return True

    def visit_Return(self, node: ast.Return):
        if node.value != None:
            self.checked_visit_expr(node.value)
        return True

    # See https://docs.python.org/3/reference/compound_stmts.html#with
    def visit_With(self, w: ast.With):
        for item in w.items:
            self.checked_visit_expr(item.context_expr)
        self.visit_stmts(w.body)
        return True

    def visit_While(self, s: ast.While) -> bool:
        self.checked_visit_expr(s.test)
        self.visit_stmts(s.body)
        self.visit_stmts(s.orelse)
        return True

    def checked_visit_stmt(self, e: ast.stmt):
        r = self.visit(e)
        if r == None:
            raise Exception(f'Unsupported expression {type(e)}')

    def visit_stmts(self, stmts):
        for stmt in stmts:
            self.checked_visit_stmt(stmt)

CellMap = Dict[str, mlir.Value]

def alloc_variable_cells(map: CellMap, names: list[mlir.StringAttr], cells: list[mlir.Value], block:mlir.Block, vars: Dict[str, None]):
    with mlir.InsertionPoint(block):
        for var in vars:
            cell = python_d.CellAlloc()
            map[var] = cell
            names.append(mlir.StringAttr.get(var))
            cells.append(cell)

# This is the main class
class Translator(ast.NodeVisitor):
    _cell_map: CellMap
    _scope_value: mlir.Value

    def __init__(self,
                 m: Module,
                 block: mlir.Block,
                 scope: mlir.Value,
                 cell_map: CellMap):
        self.module = m
        self.block = block
        self.onDone = None
        self.exceptBlock = None
        self._cell_map = cell_map
        self._scope_value = scope

    # Internal support
    def assign_lhs(self, tgt: ast.expr, v:mlir.Value):
        if isinstance(tgt, ast.Tuple):
            n = len(tgt.elts)
            with mlir.InsertionPoint(self.block):
                python_d.TupleCheck(v, mlir.IntegerAttr.get(mlir.IntegerType.get_signed(64), n))
                for i in range(0, n):
                    sv = python_d.TupleGet(v, mlir.IntegerAttr.get(mlir.IntegerType.get_signed(64), i))
                    self.assign_lhs(tgt.elts[i], sv)
        elif isinstance(tgt, ast.Subscript):
            a = self.checked_visit_expr(tgt.value)
            idx = self.checked_visit_expr(tgt.slice)
            with mlir.InsertionPoint(self.block):
                python_d.ArraySet(a, idx, v)
        elif isinstance(tgt, ast.Name):
            cell_store(self.block, self._cell_map[tgt.id], v)
        else:
            raise Exception(f'Unexpected target {tgt.__class__.__name__} {tgt.lineno}:{tgt.col_offset}')

    def undef_value(self, e: ast.AST) -> mlir.Value:
        sys.stderr.write(f'Unsupported value {e.__class__.__name__} at {e.lineno}:{e.col_offset}\n')
        with mlir.InsertionPoint(self.block):
            return python_d.UndefinedOp()

    # Return the none vlaue
    def none_value(self) -> mlir.Value:
        with mlir.InsertionPoint(self.block):
            return python_d.NoneOp()

    # Return value denoting method with given name in value
    def get_method(self, w:mlir.Value, name: str) -> mlir.Value:
        with mlir.InsertionPoint(self.block):
            return python_d.GetMethod(w, mlir.StringAttr.get(name))


    def get_except_block(self):
        if self.exceptBlock != None:
           return self.exceptBlock

        valueType = python_d.ValueType.get()
        exceptBlock = self.block.create_after(valueType)
        with mlir.InsertionPoint(exceptBlock):
            r = python_d.MkExceptOp(exceptBlock.arguments[0])
            func_d.ReturnOp([r])

        self.exceptBlock = exceptBlock
        return exceptBlock

    # Invoke the given method.
    def invoke(self, method: mlir.Value, args: list[mlir.Value], keywords=None) -> mlir.Value:
        if keywords == None or len(keywords) == 0:
            keyAttr = None
        else:
            keyAttrs = []
            for k in keywords:
                keyAttrs.append(mlir.StringAttr.get(k))
            keyAttr = mlir.ArrayAttr.get(keyAttrs)

        valueType = python_d.ValueType.get()
        exceptBlock = self.get_except_block()
        returnBlock = self.block.create_after(valueType)

        with mlir.InsertionPoint(self.block):
            python_d.InvokeOp(method, args, keyAttr, [], [], returnBlock, exceptBlock)

        self.block = returnBlock
        return returnBlock.arguments[0]

    # Invoke format value
    def format_value(self, v: mlir.Value, format: mlir.Value) -> mlir.Value:
        m = self.get_method(v, '__format__')
        return self.invoke(m, [v, format])

    # Import a module and give it the given name
    def pythonImport(self, module:str, name: str) -> mlir.Value:
        with mlir.InsertionPoint(self.block):
            m = python_d.Module(mlir.StringAttr.get(module))
            python_d.CellStore(self._cell_map[name], m)

    # Create a formatted string
    def joined_string(self, args: list[mlir.Value]) -> mlir.Value:
        with mlir.InsertionPoint(self.block):
            return python_d.FormattedString(args)

    def string_constant(self, c: str) -> mlir.Value:
        with mlir.InsertionPoint(self.block):
            return python_d.StrLit(mlir.StringAttr.get(c))

    def int_constant(self, c: int) -> mlir.Value:
        with mlir.InsertionPoint(self.block):
            if -2**63 <= c and c < 2**63:
                return python_d.S64Lit(mlir.IntegerAttr.get(mlir.IntegerType.get_signed(64), c))
            else:
                return python_d.IntLit(mlir.StringAttr.get(str(c)))

    # Create a nuiltin attribute
    def builtin(self, name: str):
        with mlir.InsertionPoint(self.block):
            return python_d.Builtin(mlir.StringAttr.get(name))

    def load_value_attribute(self, v: mlir.Value, attr: str):
        get = self.builtin("getattr")
        return self.invoke(get, [v, self.string_constant(attr)])

    def returnOp(self, v: mlir.Value):
        with mlir.InsertionPoint(self.block):
            func_d.ReturnOp([python_d.MkReturnOp(v)])

    # Create a tranlator for functions and value for denoting it.
    def create_fun(self, name: str, args: ast.arguments, var_scope: VariableScope) -> tuple[Translator, mlir.Value] :
        scopeType = python_d.ScopeType.get()
        cellType  = python_d.CellType.get()
        valueType = python_d.ValueType.get()
        returnValueType = python_d.ReturnValueType.get()

        assert(args.vararg == None)
        assert(len(args.kwonlyargs) == 0)
        assert(len(args.kw_defaults) == 0)
        assert(args.kwarg == None)
        assert(len(args.defaults) == 0)
        arg_count = len(args.args)

        symbol_name = self.module.fresh_symbol(name)

        # Count arguments referenced in parent
        captured_vars = var_scope.parent_vars
        capture_count = len(captured_vars)

        # Define arguments
        arg_types = [scopeType] + capture_count*[cellType] + arg_count*[valueType]

        with mlir.InsertionPoint(self.module.mlir.body):
            tp = mlir.FunctionType.get(arg_types, [returnValueType])
            fun = builtin_d.FuncOp(symbol_name, tp)
        fun_block = mlir.Block.create_at_start(fun.regions[0], arg_types)
        # Initialize scope from function arguments.

        fun_cell_map = {}

        fun_name_attrs = []
        fun_cells = []
        # Add arguments for captured variables
        closure_cells = []
        for i in range(capture_count):
            ast = captured_vars[i]
            name = ast.id
            cell = fun_block.arguments[1+i]
            fun_cell_map[name] = cell
            fun_name_attrs.append(mlir.StringAttr.get(name))
            fun_cells.append(cell)
            closure_cells.append(self._lookup_cell(ast, name))

        alloc_variable_cells(fun_cell_map, fun_name_attrs, fun_cells, fun_block,  var_scope.vars)

        with mlir.InsertionPoint(fun_block):
            fun_scope = python_d.ScopeExtend(fun_block.arguments[0], mlir.ArrayAttr.get(fun_name_attrs), fun_cells)

        # Add explicit function arguments
        arg_names = []
        for i in range(arg_count):
            name = args.args[i].arg
            arg_names.append(mlir.StringAttr.get(name))
            arg_cell = fun_cell_map[name]
            value = fun_block.arguments[1+capture_count+i]
            cell_store(fun_block, arg_cell, value)

        fun_translator = Translator(self.module, fun_block, fun_scope, fun_cell_map)

        symbol_attr = mlir.FlatSymbolRefAttr.get(symbol_name)
        arg_attrs = mlir.ArrayAttr.get(arg_names)
        with mlir.InsertionPoint(self.block):
            fun_value = python_d.FunctionRef(symbol_attr, arg_attrs, self._scope_value, closure_cells)

        return fun_translator, fun_value

    # Expressions
    def checked_visit_expr(self, e: ast.expr) -> mlir.Value:
        r = self.visit(e)
        if r == None:
            raise Exception(f'Unsupported expression {type(e)}')
        return r

    def visit_Attribute(self, a: ast.Attribute):
        val = self.checked_visit_expr(a.value)
        if isinstance(a.ctx, ast.Load):
            return self.load_value_attribute(val, a.attr)
        elif isinstance(a.ctx, ast.Store):
            raise Exception(f'Store attribute unsupported.')
        elif isinstance(a.ctx, ast.Del):
            raise Exception(f'Delete attribute unsupported.')
        else:
            raise Exception(f'Unknown context {type(a.ctx)}')

    bin_operator_map = {
        ast.Add: python_d.AddOp,
        ast.BitAnd: python_d.BitAndOp,
        ast.BitOr: python_d.BitOrOp,
        ast.BitXor: python_d.BitXorOp,
        ast.Div: python_d.DivOp,
        ast.FloorDiv: python_d.FloorDivOp,
        ast.LShift: python_d.LShiftOp,
        ast.Mod: python_d.ModOp,
        ast.Mult: python_d.MultOp,
        ast.MatMult: python_d.MatMultOp,
        ast.Pow: python_d.PowOp,
        ast.RShift: python_d.RShiftOp,
        ast.Sub: python_d.SubOp
    }

    def apply_binop(self, left: mlir.Value, op, right: mlir.Value) -> mlir.Value:
        valueType = python_d.ValueType.get()
        exceptBlock = self.get_except_block()
        returnBlock = self.block.create_after(valueType)

        with mlir.InsertionPoint(self.block):
            op(left, right, [], [], returnBlock, exceptBlock)

        self.block = returnBlock
        return returnBlock.arguments[0]

    def visit_BinOp(self, e: ast.BinOp) -> mlir.Value:
        op = Translator.bin_operator_map.get(e.op.__class__)
        if op == None:
            return self.undef_value(e)

        left  = self.checked_visit_expr(e.left)
        right = self.checked_visit_expr(e.right)
        return self.apply_binop(left, op, right)

    def visit_Call(self, c: ast.Call) -> mlir.Value:
        f = self.visit(c.func)
        assert(f != None)
        args = []
        for a in c.args:
            args.append(self.checked_visit_expr(a))

        keywords = []
        for k in c.keywords:
            if k.arg == None:
                raise Exception(f'Did not expect ** in call')
            args.append(self.checked_visit_expr(k.value))
            keywords.append(k.arg)
        return self.invoke(f, args, keywords)


    # FIXME Make static
    comparison_map = {
        ast.Eq : python_d.EqOp,
        ast.Gt : python_d.GtOp,
        ast.GtE : python_d.GtEOp,
        ast.In : python_d.InOp,
        ast.Is : python_d.IsOp,
        ast.IsNot : python_d.IsNotOp,
        ast.Lt : python_d.LtOp,
        ast.LtE : python_d.LtEOp,
        ast.NotEq : python_d.NotEqOp,
        ast.NotIn : python_d.NotInOp
    }

    def visit_Compare(self, e: ast.Compare) -> mlir.Value:
        left  = self.checked_visit_expr(e.left)
        args = e.comparators.__iter__()
        for cmpop in e.ops:
            right = self.checked_visit_expr(args.__next__())
            op = self.comparison_map.get(cmpop.__class__)
            if op == None:
                left = self.undef_value(e)
            else:
                left = self.apply_binop(left, op, right)
        return left

    def visit_Constant(self, c: ast.Constant) -> mlir.Value:
        if isinstance(c.value, str):
            return self.string_constant(c.value)
        elif isinstance(c.value, int):
            r = self.int_constant(c.value)
            if r == None:
                raise Exception(f'Integer constant {c.value} at {c.lineno}:{c.col_offset} is out of range.')
            return r
        else:
            raise Exception(f'Unknown Constant {c.value}')

    def visit_FormattedValue(self, fv: ast.FormattedValue) -> mlir.Value:
        v = self.checked_visit_expr(fv.value)
        if fv.conversion != -1:
            raise Exception(f'Conversion unsupported')
        if fv.format_spec is None:
            format = self.none_value()
        else:
            format = self.checked_visit_expr(fv.format_spec)
        return self.format_value(v, format)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> mlir.Value:
        return self.undef_value(node) # FIXME

    # See https://www.python.org/dev/peps/pep-0498/
    def visit_JoinedStr(self, s: ast.JoinedStr) -> mlir.Value:
        args = []
        for a in s.values:
            if isinstance(a, ast.Constant):
                args.append(self.visit_Constant(a))
            elif isinstance(a, ast.FormattedValue):
                args.append(self.visit_FormattedValue(a))
            else:
                raise Exception(f'Join expression expected constant or formatted value.')
        return self.joined_string(args)

    def visit_Lambda(self, e: ast.Lambda) -> mlir.Value:
        funTranslator, fun_value = self.create_fun("_lambda", e.args, self.module.get_scope(id(e)))
        funTranslator.returnOp(funTranslator.checked_visit_expr(e.body))
        return fun_value

    def visit_List(self, e: ast.List) -> mlir.Value:
        args = map(self.checked_visit_expr, e.elts)
        with mlir.InsertionPoint(self.block):
            return python_d.List(args)

    def visit_ListComp(self, e: ast.ListComp) -> mlir.Value:
        # Get identifier of block to throw
        throwBlock = self.get_except_block()

        # Save scope
        orig_cell_map = self._cell_map
        orig_scope = self._scope_value

        # Create empty list for storing result.
        with mlir.InsertionPoint(self.block):
            r = python_d.List([])

        # Get append method off of list.
        append = self.get_method(r, 'append')

        # Create block for evaluating expression
        finalBlock = self.block.create_after()
        doneBlock = finalBlock
        self._cell_map = orig_cell_map.copy()
        for g in e.generators:
            assert(not g.is_async)
            assert(len(g.ifs) == 0)


            l = self.checked_visit_expr(g.iter)
            i = self.invoke(self.get_method(l, '__iter__'), [])
            next = self.get_method(i, '__next__')

            nextBlock, self.block, bodyValue = invoke_next(next, self.block, doneBlock, throwBlock)

            # Update cell_map
            inner_var_scope = self.module.get_scope(id(g))
            var_name_attrs = []
            cells = []
            alloc_variable_cells(self._cell_map, var_name_attrs, cells, self.block, inner_var_scope.vars)

            with mlir.InsertionPoint(self.block):
                # Variables in comprehension do not escape.
                self._scope_value = python_d.ScopeExtend(self._scope_value, mlir.ArrayAttr.get(var_name_attrs), cells)

            # Extend scope

            self.assign_lhs(g.target, bodyValue)
            # Map done block to next so other loop.
            doneBlock = nextBlock

        e = self.checked_visit_expr(e.elt)
        # Append element to list
        self.invoke(append, [e])

        with mlir.InsertionPoint(self.block):
            cf_d.BranchOp([], doneBlock)

        self.block = finalBlock
        self._cell_map = orig_cell_map
        self._scope_value = orig_scope

        return l

    def _lookup_cell(self, loc: ast.AST, name: str) -> mlir.Value:
        try:
            return self._cell_map[name]
        except KeyError:
            raise Exception(f'Could not find variable {name} at {loc.lineno}:{loc.col_offset}')

    def visit_Name(self, node: ast.Name) -> mlir.Value:
        name = node.id
        try:
            cell = self._cell_map[name]
            return cell_load(self.block, cell)
        except KeyError:
            mlir_name = builtin_mlir_name(name)
            if mlir_name == None:
                raise Exception(f'Could not find variable {node.id} at {node.lineno}:{node.col_offset}')
            return self.builtin(mlir_name)

    def visit_Slice(self, e: ast.Slice) -> mlir.Value:
        upper = self.checked_visit_expr(e.upper) if e.upper != None else self.none_value()

        sliceFn = self.builtin('slice')
        if e.step != None:
            lower = self.checked_visit_expr(e.lower) if e.lower != None else self.none_value()
            step = self.checked_visit_expr(e.step)
            return self.invoke(sliceFn, [lower, upper, step])
        elif e.lower != None:
            lower = self.checked_visit_expr(e.lower)
            return self.invoke(sliceFn, [lower, upper])
        else:
            return self.invoke(sliceFn, [upper])

    def visit_Subscript(self, e: ast.Subscript):
        v = self.checked_visit_expr(e.value)
        slice = self.checked_visit_expr(e.slice)
        getitem = self.get_method(v, '__getitem__')
        return self.invoke(getitem, [slice])

    def visit_Tuple(self, e: ast.Tuple):
        args = map(self.checked_visit_expr, e.elts)
        with mlir.InsertionPoint(self.block):
            return python_d.Tuple(args)

    unary_operator_map = {
        ast.Invert: python_d.InvertOp,
        ast.Not: python_d.NotOp,
        ast.UAdd: python_d.UAddOp,
        ast.USub: python_d.USubOp,
    }

    def visit_UnaryOp(self, e: ast.UnaryOp) -> mlir.Value:
        op = Translator.unary_operator_map.get(e.op.__class__)
        if op == None:
            return self.undef_value(e)

        arg  = self.checked_visit_expr(e.operand)

        valueType = python_d.ValueType.get()
        exceptBlock = self.get_except_block()
        returnBlock = self.block.create_after(valueType)

        with mlir.InsertionPoint(self.block):
            op(arg, [], [], returnBlock, exceptBlock)

        self.block = returnBlock
        return returnBlock.arguments[0]

    # Statements
    def checked_visit_stmt(self, e: ast.stmt):
        r = self.visit(e)
        if r == None:
            raise Exception(f'Unsupported expression {type(e)}')
        return r

    def visit_stmts(self, stmts):
        for stmt in stmts:
            if not self.checked_visit_stmt(stmt):
                return False
        return True

    def visit_Assign(self, a: ast.Assign):
        if len(a.targets) != 1:
            raise Exception('Assignment must have single left-hand side.')
        tgt = a.targets[0]
        r = self.checked_visit_expr(a.value)
        self.assign_lhs(tgt, r)
        return True

    def visit_AugAssign(self, node: ast.AugAssign):
        #FIXME
        return True

    def visit_Expr(self, e: ast.Expr):
        r = self.checked_visit_expr(e.value)
        return True

    def visit_For(self, s: ast.For) -> bool:
        if len(s.orelse) > 0:
            raise Exception('For orelse unsupported.')
        r = self.checked_visit_expr(s.iter)
        enter = self.get_method(r, '__iter__')
        i = self.invoke(enter, [])
        next = self.get_method(i, '__next__')

        throwBlock = self.get_except_block()

        doneBlock = self.block.create_after()

        nextBlock, bodyBlock, value = invoke_next(next, self.block, doneBlock, throwBlock)

        # Get value for loop
        self.block = bodyBlock
        self.assign_lhs(s.target, value)
        bodyCont = self.visit_stmts(s.body)
        if bodyCont:
            with mlir.InsertionPoint(self.block):
                cf_d.BranchOp([], nextBlock)

        self.block = doneBlock
        return True

    def visit_FunctionDef(self, s: ast.FunctionDef):
        fun_translator, fun_value = self.create_fun(s.name, s.args, self.module.get_scope(id(s)))
        cont = fun_translator.visit_stmts(s.body)
        if cont:
            fun_translator.returnOp(fun_translator.none_value())

        # Fixme lookup cell value in map
        cell_store(self.block, self._cell_map[s.name], fun_value)

        return True

    def visit_If(self, s: ast.If):
        test = self.checked_visit_expr(s.test)
        c = truthy(self.block, test)

        initBlock = self.block
        newBlock = self.block.create_after()

        if s.orelse:
            falseBlock = initBlock.create_after()
            self.block = falseBlock
            if self.visit_stmts(s.orelse):
                with mlir.InsertionPoint(self.block):
                    cf_d.BranchOp([], newBlock)
        else:
            falseBlock = newBlock

        if s.body:
            trueBlock = initBlock.create_after()
            self.block = trueBlock
            if self.visit_stmts(s.body):
                with mlir.InsertionPoint(self.block):
                    cf_d.BranchOp([], newBlock)
        else:
            trueBlock = newBlock

        with mlir.InsertionPoint(initBlock):
            cf_d.CondBranchOp(c, [], [], trueBlock, falseBlock)
        self.block = newBlock
        return True

    def visit_Import(self, s: ast.Import):
        for a in s.names:
            self.pythonImport(a.name, a.asname or a.name)
        return True

    def visit_Return(self, node: ast.Return):
        if self.onDone:
            self.onDone()

        if node.value != None:
            ret = self.checked_visit_expr(node.value)
        else:
            ret = self.funTranslator.none_value()
        self.returnOp(ret)

        return False

    # See https://docs.python.org/3/reference/compound_stmts.html#with
    def visit_With(self, w: ast.With):
        #FIXME. Add try/finally blocks
        exitMethods = []
        for item in w.items:
            ctx = self.checked_visit_expr(item.context_expr)
            assert(ctx != None)
            enter = self.get_method(ctx, '__enter__')
            exit = self.get_method(ctx, '__exit__')
            r  = self.invoke(enter, [])
            var = item.optional_vars
            if var != None:
                assert(isinstance(var, ast.Name))
                cell_store(self.block, self.map[var.id], r)
            exitMethods.append(exit)
        prevDone = self.onDone
        def onDone():
            for exit in reversed(exitMethods):
                self.invoke(exit, [])
            if prevDone:
                prevDone()
        self.onDone = onDone
        cont = self.visit_stmts(w.body)
        self.onDone = prevDone
        if cont:
            onDone()
        return cont

    def visit_While(self, s: ast.While) -> bool:
        if len(s.orelse) > 0:
            raise Exception('While orelse unsupported.')

        testBlock = self.block.create_after()
        with mlir.InsertionPoint(self.block):
            cf_d.BranchOp([], testBlock)

        self.block = testBlock
        testExpr = self.checked_visit_expr(s.test)
        c = truthy(self.block, testExpr)
        bodyBlock = self.block.create_after()
        doneBlock = bodyBlock.create_after()
        with mlir.InsertionPoint(self.block):
            cf_d.CondBranchOp(c, [], [], bodyBlock, doneBlock)

        # Execute loop body
        self.block = bodyBlock
        bodyCont = self.visit_stmts(s.body)
        if bodyCont:
            with mlir.InsertionPoint(self.block):
                cf_d.BranchOp([], testBlock)

        self.block = doneBlock
        return True

def translateModule(tree) -> mlir.Module:
    m = mlir.Module.create()

    scope_map = {}
    varCapture = VariableCapture(scope_map)
    varCapture.visit_stmts(tree.body)
    if len(varCapture.references) > 0:
        msg = "Unknown variables:\n"
        for a in varCapture.references.values():
            msg = f'{msg}  {a.lineno}:{a.col_offset}: {a.id}\n'
        raise Exception(msg)
    var_scope = varCapture.mkScope()

    mod = Module(m, scope_map)

    with mlir.InsertionPoint(mod.mlir.body):
        returnValueType = python_d.ReturnValueType.get()
        tp = mlir.FunctionType.get([], [returnValueType])
        fun = builtin_d.FuncOp("script_main", tp)
    fun_block = mlir.Block.create_at_start(fun.regions[0], [])
    if len(var_scope.parent_vars) > 0:
        raise Exception(f"Did not expect unknown variables {var_scope.parent_vars}")

    global_cell_map = {}
    fun_name_attrs = []
    fun_cells = []
    alloc_variable_cells(global_cell_map, fun_name_attrs, fun_cells, fun_block, var_scope.vars)
    with mlir.InsertionPoint(fun_block):
        fun_scope = python_d.ScopeInit(mlir.ArrayAttr.get(fun_name_attrs), fun_cells)
    t = Translator(mod, fun_block, fun_scope, global_cell_map)
    cont = t.visit_stmts(tree.body)
    if cont:
        with mlir.InsertionPoint(t.block):
            func_d.ReturnOp([python_d.MkReturnOp(python_d.NoneOp())])
    return m

def main():
    if not len(sys.argv) in [2,3]:
        sys.stderr.write("Please specify input file.\n")
        sys.exit(-1)
    path = sys.argv[1]
    with open(path, "r") as source:
        tree = ast.parse(source.read())

    with mlir.Context() as ctx, mlir.Location.file("f.mlir", line=42, col=1, context=ctx):
        python_d.register_dialect()
#        ctx.allow_unregistered_dialects = True
        m = translateModule(tree)

    if len(sys.argv) >= 3:
        out_path = sys.argv[2]
        with open(out_path, "w") as tgt:
            tgt.write(str(m))
    else:
        print(str(m))

if __name__ == "__main__":
    main()