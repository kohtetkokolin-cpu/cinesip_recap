"""Turn the downloaded Noto Sans Myanmar variable font into static TTFs.

Run once; the app only ever reads the static files in static/fonts.
"""
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

SRC = r"D:\Cinesip Recap\static\fonts\NotoSansMyanmar-VF.ttf"

for weight, name in ((400, "Regular"), (700, "Bold")):
    font = TTFont(SRC)
    instance = instantiateVariableFont(
        font, {"wght": weight, "wdth": 100}, updateFontNames=True
    )
    out = rf"D:\Cinesip Recap\static\fonts\NotoSansMyanmar-{name}.ttf"
    instance.save(out)
    print(out, "ok")
