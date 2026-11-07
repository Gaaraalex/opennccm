"""RouterOS 6: los comandos que muestran su avance («/certificate sign») no cortan la solicitud de cambio
(salida real de un CHR 6.49.23 en GNS3)."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app.services import PROMPT_END, _push_stepwise  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + ("" if cond else f"  [{extra}]"))
    if not cond:
        fails.append(name)


SIGN = ("[admin@R6-A] <SAFE> /certificate sign webfig\n\n  progress: \\\n-- [Q quit|D dump|C-z pause]\n\n"
        "  progress: /\n-- [Q quit|D dump|C-z pause]")


class Conn:
    def __init__(self):
        self.sent = []
        self.pending = ""

    def config_mode(self):
        return ""

    def exit_config_mode(self):
        return ""

    def find_prompt(self, pattern=None):
        return "[admin@R6-A] <SAFE>"

    def send_command(self, cmd, expect_string=None, **_k):
        assert not self.pending, f"«{cmd}» se envió mientras el equipo mostraba el avance"
        self.sent.append(cmd)
        if cmd.startswith("/certificate sign"):
            self.pending = "\n  progress: done\n\n[admin@R6-A] <SAFE> "
            out = SIGN
        else:
            out = f"[admin@R6-A] <SAFE> {cmd}\n\n[admin@R6-A] <SAFE> "
        assert re.search(expect_string, out), "el avance ya no parece el prompt"
        return out

    def read_until_pattern(self, pattern, read_timeout=0):
        out, self.pending = self.pending, ""
        assert re.search(pattern, out)
        return out


class Prof:
    commit = False
    key = "mikrotik_routeros"


c = Conn()
cmds = ["/certificate add name=webfig common-name=r6-a", "/certificate sign webfig",
        "/ip service set www-ssl certificate=webfig disabled=no", "/ip service disable www"]
try:
    out = _push_stepwise(c, cmds, Prof())
    check("los 4 comandos se envían, cada uno después de que el anterior terminó", c.sent == cmds, c.sent)
    check("la salida incluye el final del avance", "progress: done" in out)
except AssertionError as e:
    check("no se envía nada mientras se ve el avance", False, e)
check("la línea de avance de RouterOS termina en «]» (por eso parecía el prompt)", bool(re.search(PROMPT_END, SIGN)))

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
