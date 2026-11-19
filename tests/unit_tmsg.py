"""Avisos guardados con su texto original (historial de Alta disponibilidad, avisos de la campana) en el idioma de quien
los lee (i18n.tmsg): frase exacta, frase con huecos y valores que también son frases; lo desconocido, tal cual; una
frase corta tipo «{0}: {1}» no se usa para reconocer; y los textos que un módulo Pro suma al catálogo también valen."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import i18n  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# como lo hace el módulo de Alta disponibilidad al cargarse (sus locales/en.json se suman al catálogo del núcleo)
i18n.catalog("en").update({
    "{0} pasó a activo: {1}": "{0} became active: {1}",
    "{0} pasó a activo a la fuerza: {1}": "{0} was forced to active: {1}",
    "el servidor activo no respondía desde hacía {0} s y el testigo sí.": "the active server had not responded for {0} s and the witness had.",
    "Se quitó el par.": "The pair was removed.",
})
hist = "NCM-B pasó a activo: el servidor activo no respondía desde hacía 90 s y el testigo sí."
forced = "NCM-B pasó a activo a la fuerza: lo que sea"

i18n.set_lang("es")
check("en español se ve tal cual", i18n.tmsg(hist) == hist and i18n.tmsg("Se quitó el par.") == "Se quitó el par.")
check("…y un aviso que quedó guardado en inglés, en español", i18n.tmsg("The pair was removed.") == "Se quitó el par.")
i18n.set_lang("en")
check("frase exacta en inglés", i18n.tmsg("Se quitó el par.") == "The pair was removed.")
check("frase con huecos y un valor que también es frase", i18n.tmsg(hist)
      == "NCM-B became active: the active server had not responded for 90 s and the witness had.", i18n.tmsg(hist))
check("la frase más específica gana («a la fuerza»)", i18n.tmsg(forced) == "NCM-B was forced to active: lo que sea", i18n.tmsg(forced))
check("varias líneas: cada una por su lado", i18n.tmsg("Se quitó el par.\n" + hist).splitlines()[0] == "The pair was removed.")
check("lo que no se reconoce queda tal cual", i18n.tmsg("Texto libre sin traducción 123") == "Texto libre sin traducción 123")
check("vacío y None", i18n.tmsg("") == "" and i18n.tmsg(None) == "")
i18n.catalog("en")["{0}: {1}"] = "{0}: {1}"
check("una frase corta tipo «{0}: {1}» no se usa para reconocer", i18n.tmsg("CORE1: algo raro") == "CORE1: algo raro")
i18n.set_lang("es")

print("\n" + ("TODO OK" if not fails else f"{len(fails)} FALLOS: {fails}"))
sys.exit(1 if fails else 0)
