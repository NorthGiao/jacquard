"""job: a collection of pattern examples (gallery) = regression battery. Each VI demonstrates the spec pattern of one structure/type; all were built on-site and passed.
Usage: before writing a new VI, copy the closest one; on-site `scripts/vi_job.sh gallery` = one regression pass on the current writer.
Note the actions of W10/ev_insert: click uses the control's name (vi_job converts it to the visible label automatically)."""

from lablib import BASE

OUT = BASE / "write2" / "jobs" / "gallery"

VIS = [
    # W01_while_sr: While + shift register + comparison stop
    {
        "name": "w01_while_sr",
        "desc": "While + 移位寄存器 + 比较停止",
        "spec": {
            "controls": [{"name": "n", "type": "i32"}],
            "indicators": [{"name": "cnt", "type": "i32"}],
            "constants": [{"name": "z", "type": "i32", "value": 0}],
            "structures": [
                {
                    "name": "W",
                    "type": "while_loop",
                    "shift_registers": ["s"],
                    "functions": [{"name": "inc", "function": "Increment"}, {"name": "ge", "function": "Greater Or Equal?"}],
                }
            ],
            "wires": [
                ["z", "s.left"],
                ["s.left", "inc.x"],
                ["inc.x+1", "s.right"],
                ["inc.x+1", "ge.x"],
                ["n", "ge.y"],
                ["ge.x >= y?", "W.stop"],
                ["s.right", "cnt"],
            ],
        },
        "tests": [{"inputs": {"n": 7}, "expect": {"cnt": 7}}, {"inputs": {"n": 1}, "expect": {"cnt": 1}}],
    },
    # W02_array_io: array input/output + For auto-indexing
    {
        "name": "w02_array_io",
        "desc": "数组输入/输出 + For 自动索引",
        "spec": {
            "controls": [{"name": "a", "type": "dbl[]"}],
            "indicators": [{"name": "b", "type": "dbl[]"}],
            "constants": [{"name": "k", "type": "dbl", "value": 2}],
            "structures": [{"name": "L", "type": "for_loop", "functions": [{"name": "m", "function": "Multiply"}]}],
            "wires": [["a", "m.x"], ["k", "m.y"], ["m.x*y", "b"]],
        },
        "tests": [{"inputs": {"a": [1, 2, 3]}, "expect": {"b": [2, 4, 6]}}, {"inputs": {"a": []}, "expect": {"b": []}}],
    },
    # W03_cluster_in: cluster as input (the docstring says give a list in element order)
    {
        "name": "w03_cluster_in",
        "desc": "簇作为输入（docstring 说按元素序给 list）",
        "spec": {
            "controls": [{"name": "cl", "type": {"cluster": [{"name": "a", "type": "dbl"}, {"name": "b", "type": "dbl"}]}}],
            "indicators": [{"name": "y", "type": "dbl"}],
            "functions": [{"name": "u", "function": "Unbundle", "size": 2}, {"name": "add", "function": "Add"}],
            "wires": [["cl", "u.cluster"], ["u.element[1]", "add.x"], ["u.element[2]", "add.y"], ["add.x+y", "y"]],
        },
        "tests": [{"inputs": {"cl": [1.5, 2.0]}, "expect": {"y": 3.5}}],
    },
    # W04_enum_case: enum input + case structure
    {
        "name": "w04_enum_case",
        "desc": "枚举输入 + 条件结构",
        "spec": {
            "controls": [{"name": "st", "type": {"enum": ["Init", "Run", "Stop"]}}],
            "indicators": [{"name": "v", "type": "i32"}],
            "structures": [
                {
                    "name": "C",
                    "type": "case",
                    "output_tunnels": ["t"],
                    "cases": [
                        {"value": "Init", "constants": [{"name": "c0", "type": "i32", "value": 0}]},
                        {"value": "Run", "constants": [{"name": "c1", "type": "i32", "value": 1}]},
                        {"value": "Stop", "default": True, "constants": [{"name": "c2", "type": "i32", "value": 2}]},
                    ],
                }
            ],
            "wires": [["st", "C.selector"], ["c0", "t"], ["c1", "t"], ["c2", "t"], ["t", "v"]],
        },
        "tests": [{"inputs": {"st": "Run"}, "expect": {"v": 1}}, {"inputs": {"st": "Stop"}, "expect": {"v": 2}}],
    },
    # W05_queue: queue enqueue/dequeue
    {
        "name": "w05_queue",
        "desc": "队列 入队/出队",
        "spec": {
            "controls": [{"name": "x", "type": "dbl"}],
            "indicators": [{"name": "y", "type": "dbl"}],
            "constants": [{"name": "typ", "type": "dbl", "value": 0}],
            "functions": [
                {"name": "oq", "function": "Obtain Queue"},
                {"name": "eq", "function": "Enqueue Element"},
                {"name": "dq", "function": "Dequeue Element"},
                {"name": "rq", "function": "Release Queue"},
            ],
            "wires": [
                ["typ", "oq.element data type"],
                ["oq.queue out", "eq.queue"],
                ["x", "eq.element"],
                ["eq.queue out", "dq.queue"],
                ["dq.element", "y"],
                ["dq.queue out", "rq.queue"],
            ],
        },
        "tests": [{"inputs": {"x": 3.5}, "expect": {"y": 3.5}}],
    },
    # W06_string_num: number to string + Concatenate Strings (file-name concatenation)
    {
        "name": "w06_string_num",
        "desc": "数值转字符串 + 连接字符串（文件名拼接）",
        "spec": {
            "controls": [{"name": "pre", "type": "string"}, {"name": "n", "type": "i32"}],
            "indicators": [{"name": "s", "type": "string"}],
            "functions": [
                {"name": "nd", "function": "Number To Decimal String"},
                {"name": "cat", "function": "Concatenate Strings", "size": 2},
            ],
            "wires": [
                ["n", "nd.number"],
                ["pre", "cat.element[1]"],
                ["nd.decimal integer string", "cat.element[2]"],
                ["cat.concatenated string", "s"],
            ],
        },
        "tests": [{"inputs": {"pre": "ch", "n": 5}, "expect": {"s": "ch5"}}],
    },
    # W07_build_path: Build Path
    {
        "name": "w07_build_path",
        "desc": "创建路径",
        "spec": {
            "controls": [{"name": "dir", "type": "path"}, {"name": "name", "type": "string"}],
            "indicators": [{"name": "p", "type": "path"}],
            "functions": [{"name": "bp", "function": "Build Path"}],
            "wires": [["dir", "bp.base path"], ["name", "bp.name or relative path"], ["bp.appended path", "p"]],
        },
        "tests": [
            {
                "inputs": {"dir": r"D:\agent_sandbox\x", "name": "ch5.txt"},
                "expect": {"p": r"D:\agent_sandbox\x\ch5.txt"},
            }
        ],
    },
    # W08_error_case: error cluster + case structure (No Error/Error)
    {
        "name": "w08_error_case",
        "desc": "错误簇 + 条件结构（No Error/Error）",
        "spec": {
            "controls": [{"name": "err", "type": "error"}],
            "indicators": [{"name": "code", "type": "i32"}],
            "structures": [
                {
                    "name": "C",
                    "type": "case",
                    "output_tunnels": ["t"],
                    "cases": [
                        {"value": "No Error", "constants": [{"name": "c0", "type": "i32", "value": 0}]},
                        {"value": "Error", "constants": [{"name": "c1", "type": "i32", "value": 1}]},
                    ],
                }
            ],
            "wires": [["err", "C.selector"], ["c0", "t"], ["c1", "t"], ["t", "code"]],
        },
        "tests": [
            {"inputs": {"err": [False, 0, ""]}, "expect": {"code": 0}},
            {"inputs": {"err": [True, 7, "x"]}, "expect": {"code": 1}},
        ],
    },
    # W09_select_coerce: Select + In Range and Coerce
    {
        "name": "w09_select_coerce",
        "desc": "选择 + 范围强制",
        "spec": {
            "controls": [{"name": "x", "type": "dbl"}, {"name": "flag", "type": "bool"}],
            "indicators": [{"name": "y", "type": "dbl"}],
            "constants": [
                {"name": "lo", "type": "dbl", "value": 0},
                {"name": "hi", "type": "dbl", "value": 10},
                {"name": "neg1", "type": "dbl", "value": -1},
            ],
            "functions": [{"name": "rc", "function": "In Range and Coerce"}, {"name": "sel", "function": "Select"}],
            "wires": [
                ["x", "rc.x"],
                ["lo", "rc.lower limit"],
                ["hi", "rc.upper limit"],
                ["flag", "sel.s"],
                ["rc.coerced(x)", "sel.t"],
                ["neg1", "sel.f"],
                ["sel.s? t:f", "y"],
            ],
        },
        "tests": [
            {"inputs": {"x": 12, "flag": True}, "expect": {"y": 10}},
            {"inputs": {"x": 3, "flag": False}, "expect": {"y": -1}},
        ],
    },
    # W10_event_ui: event structure: set value → click button → read result → click stop (UI interaction: runs.actions; check looks at _reads)
    {
        "name": "w10_event_ui",
        "desc": "事件结构：设值→点按钮→读结果→点停止",
        "spec": {
            "controls": [
                {"name": "stop", "type": "bool", "style": "Stop Button"},
                {"name": "go", "type": "bool", "label": "Double it", "style": "OK Button"},
                {"name": "x", "type": "dbl"},
            ],
            "indicators": [{"name": "y", "type": "dbl"}],
            "structures": [
                {
                    "name": "W",
                    "type": "while_loop",
                    "structures": [
                        {
                            "name": "E",
                            "type": "event",
                            "events": [
                                {"control": "stop"},
                                {
                                    "control": "go",
                                    "functions": [{"name": "m", "function": "Multiply"}],
                                    "constants": [{"name": "k", "type": "dbl", "value": 2}],
                                },
                            ],
                        }
                    ],
                }
            ],
            "wires": [["stop", "W.stop"], ["x", "m.x"], ["k", "m.y"], ["m.x*y", "y"]],
        },
        "runs": {
            "actions": [{"set": {"x": 21}}, {"click": "go"}, {"wait": 0.5}, {"read": ["y"]}, {"click": "stop"}],
            "timeout": 15.0,
            "check": "check_w10",
        },
    },
    # ev_insert: Insert Into Array in an event case (the index terminal only appears after array is wired → the writer wires it in a second pass)
    {
        "name": "ev_insert",
        "desc": "事件分支里 Insert Into Array",
        "spec": {
            "controls": [
                {"name": "stop", "type": "bool", "style": "Stop Button"},
                {"name": "go", "type": "bool", "label": "Insert it", "style": "OK Button"},
                {"name": "a", "type": "dbl[]"},
                {"name": "i", "type": "i32"},
                {"name": "x", "type": "dbl"},
            ],
            "indicators": [{"name": "b", "type": "dbl[]"}],
            "structures": [
                {
                    "name": "W",
                    "type": "while_loop",
                    "structures": [
                        {
                            "name": "E",
                            "type": "event",
                            "events": [
                                {"control": "stop"},
                                {"control": "go", "functions": [{"name": "ins", "function": "Insert Into Array"}]},
                            ],
                        }
                    ],
                }
            ],
            "wires": [
                ["stop", "W.stop"],
                ["a", "ins.array"],
                ["i", "ins.index"],
                ["x", "ins.new element/subarray"],
                ["ins.output array", "b"],
            ],
        },
        "runs": {
            "actions": [
                {"set": {"a": [1.0, 2.0, 3.0], "i": 1, "x": 9.0}},
                {"click": "go"},
                {"wait": 0.5},
                {"read": ["b"]},
                {"click": "stop"},
            ],
            "timeout": 15.0,
            "check": "check_ev_insert",
        },
    },
    # substr: fixed-length substring (the length terminal of String Subset can't be wired → String To Byte Array → Array Subset → Byte Array To String)
    {
        "name": "substr",
        "desc": "定长子串替代链",
        "spec": {
            "controls": [{"name": "s", "type": "string"}, {"name": "off", "type": "i32"}, {"name": "len", "type": "i32"}],
            "indicators": [{"name": "o", "type": "string"}],
            "functions": [
                {"name": "sb", "function": "String To Byte Array"},
                {"name": "as", "function": "Array Subset"},
                {"name": "bs", "function": "Byte Array To String"},
            ],
            "wires": [
                ["s", "sb.string"],
                ["sb.unsigned byte array", "as.array"],
                ["off", "as.index"],
                ["len", "as.length"],
                ["as.subarray", "bs.unsigned byte array"],
                ["bs.string", "o"],
            ],
        },
        "tests": [
            {"inputs": {"s": "abcdef", "off": 1, "len": 3}, "expect": {"o": "bcd"}},
            {"inputs": {"s": "abcdef", "off": 4, "len": 9}, "expect": {"o": "ef"}},
        ],
    },
]


def check_w10(v):
    r = v.get("_reads") or [{}]
    return {"reads": r, "pass": bool(v.get("_completed")) and abs(r[0].get("y", 0) - 42.0) < 1e-9}


def check_ev_insert(v):
    r = v.get("_reads") or [{}]
    return {"reads": r, "pass": bool(v.get("_completed")) and r[0].get("b") == [1.0, 9.0, 2.0, 3.0]}
