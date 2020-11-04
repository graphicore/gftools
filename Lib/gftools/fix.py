"""
Functions to fix fonts so they conform to the Google Fonts
specification:
https://github.com/googlefonts/gf-docs/tree/master/Spec
"""
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables import ttProgram
from fontTools.ttLib.tables._f_v_a_r import NamedInstance
from fontTools.otlLib.builder import buildStatTable
from gftools.util.google_fonts import _KNOWN_WEIGHTS
from gftools.utils import download_family_from_Google_Fonts, Google_Fonts_has_family
from gftools.axisreg import axis_registry
from copy import deepcopy
import logging


log = logging.getLogger(__name__)


__all__ = [
    "remove_tables",
    "add_dummy_dsig",
    "fix_unhinted_font",
    "fix_hinted_font",
    "fix_fs_type",
    "fix_weight_class",
    "fix_fs_selection",
    "fix_mac_style",
    "font_stylename",
    "font_familyname",
    "fix_fvar_instances",
    "update_nametable",
    "fix_nametable",
    "inherit_vertical_metrics",
    "fix_vertical_metrics",
    "fix_font",
    "fix_family",
    "gen_stat_tables",
]


# The _KNOWN_WEIGHT_VALUES constant is used internally by the GF Engineering
# team so we cannot update ourselves. TODO (Marc F) unify this one day
WEIGHT_NAMES = _KNOWN_WEIGHTS
del WEIGHT_NAMES[""]
WEIGHT_NAMES["Hairline"] = 1
WEIGHT_NAMES["ExtraBlack"] = 1000
WEIGHT_VALUES = {v: k for k, v in WEIGHT_NAMES.items()}


UNWANTED_TABLES = frozenset(
    ["FFTM", "TTFA", "TSI0", "TSI1", "TSI2", "TSI3", "TSI5", "prop", "MVAR",]
)


ELIDABLE_AXIS_VALUE_NAME = 0x2


# TODO we may have more of these. Please note that some applications may not
# implement variable font style linking.
LINKED_VALUES = {
    "wght": (400, 700),
    "ital": (0.0, 1.0),
}



def remove_tables(ttFont, tables=None):
    """Remove unwanted tables from a font. The unwanted tables must belong
    to the UNWANTED_TABLES set.

    Args:
        ttFont: a TTFont instance
        tables: an iterable containing tables remove
    """
    tables_to_remove = UNWANTED_TABLES if not tables else frozenset(tables)
    font_tables = frozenset(ttFont.keys())

    tables_not_in_font = tables_to_remove - font_tables
    if tables_not_in_font:
        log.warning(
            f"Cannot remove tables '{list(tables_not_in_font)}' since they are "
            f"not in the font."
        )

    required_tables = tables_to_remove - UNWANTED_TABLES
    if required_tables:
        log.warning(
            f"Cannot remove tables '{list(required_tables)}' since they are required"
        )

    tables_to_remove = UNWANTED_TABLES & font_tables & tables_to_remove
    if not tables_to_remove:
        return
    log.info(f"Removing tables '{list(tables_to_remove)}' from font")
    for tbl in tables_to_remove:
        del ttFont[tbl]


def add_dummy_dsig(ttFont):
    """Add a dummy dsig table to a font. Older versions of MS Word
    require this table.

    Args:
        ttFont: a TTFont instance
    """
    newDSIG = newTable("DSIG")
    newDSIG.ulVersion = 1
    newDSIG.usFlag = 0
    newDSIG.usNumSigs = 0
    newDSIG.signatureRecords = []
    ttFont.tables["DSIG"] = newDSIG


def fix_unhinted_font(ttFont):
    """Improve the appearance of an unhinted font on Win platforms by:
        - Add a new GASP table with a newtable that has a single
          range which is set to smooth.
        - Add a new prep table which is optimized for unhinted fonts.
    
    Args:
        ttFont: a TTFont instance
    """
    gasp = newTable("gasp")
    # Set GASP so all sizes are smooth
    gasp.gaspRange = {0xFFFF: 15}

    program = ttProgram.Program()
    assembly = ["PUSHW[]", "511", "SCANCTRL[]", "PUSHB[]", "4", "SCANTYPE[]"]
    program.fromAssembly(assembly)

    prep = newTable("prep")
    prep.program = program

    ttFont["gasp"] = gasp
    ttFont["prep"] = prep


def fix_hinted_font(ttFont):
    """Improve the appearance of a hinted font on Win platforms by enabling
    the head table's flag 3.

    Args:
        ttFont: a TTFont instance
    """
    ttFont["head"].flags |= 1 << 3


def fix_fs_type(ttFont):
    """Set the OS/2 table's fsType flag to 0 (Installable embedding).

    Args:
        ttFont: a TTFont instance
    """
    ttFont["OS/2"].fsType = 0


def fix_weight_class(ttFont):
    """Set the OS/2 table's usWeightClass so it conforms to GF's supported
    styles table:
    https://github.com/googlefonts/gf-docs/tree/master/Spec#supported-styles

    Args:
        ttFont: a TTFont instance
    """
    stylename = font_stylename(ttFont)
    tokens = stylename.split()
    # Order WEIGHT_NAMES so longest names are first
    for style in sorted(WEIGHT_NAMES, key=lambda k: len(k), reverse=True):
        if style in tokens:
            ttFont["OS/2"].usWeightClass = WEIGHT_NAMES[style]
            return

    if "Italic" in tokens:
        ttFont["OS/2"].usWeightClass = 400
        return
    raise ValueError(
        f"Cannot determine usWeightClass because font style, '{stylename}' "
        "doesn't have a weight token which is in our known "
        "weights, '{WEIGHT_VALUES.keys()}'"
    )


def fix_fs_selection(ttFont):
    """Fix the OS/2 table's fsSelection so it conforms to GF's supported
    styles table:
    https://github.com/googlefonts/gf-docs/tree/master/Spec#supported-styles

    Args:
        ttFont: a TTFont instance
    """
    stylename = font_stylename(ttFont)
    tokens = set(stylename.split())
    fs_selection = ttFont["OS/2"].fsSelection

    # turn off all bits except for bit 7 (USE_TYPO_METRICS)
    fs_selection &= 1 << 7

    if "Italic" in tokens:
        fs_selection |= 1 << 0
    if "Bold" in tokens:
        fs_selection |= 1 << 5
    # enable Regular bit for all other styles
    if not tokens & set(["Bold", "Italic"]):
        fs_selection |= 1 << 6
    ttFont["OS/2"].fsSelection = fs_selection


def fix_mac_style(ttFont):
    """Fix the head table's macStyle so it conforms to GF's supported
    styles table:
    https://github.com/googlefonts/gf-docs/tree/master/Spec#supported-styles

    Args:
        ttFont: a TTFont instance
    """
    stylename = font_stylename(ttFont)
    tokens = set(stylename.split())
    mac_style = 0
    if "Italic" in tokens:
        mac_style |= 1 << 1
    if "Bold" in tokens:
        mac_style |= 1 << 0
    ttFont["head"].macStyle = mac_style


def font_stylename(ttFont):
    """Get a font's stylename using the name table. Since our fonts use the
    RIBBI naming model, use the Typographic SubFamily Name (NAmeID 17) if it
    exists, otherwise use the SubFamily Name (NameID 2).

    Args:
        ttFont: a TTFont instance
    """
    return get_name_record(ttFont, 17, fallbackID=2)


def font_familyname(ttFont):
    """Get a font's familyname using the name table. since our fonts use the
    RIBBI naming model, use the Typographic Family Name (NameID 16) if it
    exists, otherwise use the Family Name (Name ID 1).

    Args:
        ttFont: a TTFont instance
    """
    return get_name_record(ttFont, 16, fallbackID=1)


def get_name_record(ttFont, nameID, fallbackID=None, platform=(3, 1, 0x409)):
    """Return a name table record which has the specified nameID.

    Args:
        ttFont: a TTFont instance
        nameID: nameID of name record to return,
        fallbackID: if nameID doesn't exist, use this nameID instead
        platform: Platform of name record. Default is Win US English

    Returns:
        str
    """
    name = ttFont["name"]
    record = name.getName(nameID, 3, 1, 0x409)
    if not record and fallbackID:
        record = name.getName(fallbackID, 3, 1, 0x409)
    if not record:
        raise ValueError(f"Cannot find record with nameID {nameID}")
    return record.toUnicode()


def fix_fvar_instances(ttFont):
    """Replace a variable font's fvar instances with a set of new instances
    that conform to the Google Fonts instance spec:
    https://github.com/googlefonts/gf-docs/tree/master/Spec#fvar-instances

    Args:
        ttFont: a TTFont instance
    """
    if "fvar" not in ttFont:
        raise ValueError("ttFont is not a variable font")

    fvar = ttFont["fvar"]
    default_axis_vals = {a.axisTag: a.defaultValue for a in fvar.axes}

    stylename = font_stylename(ttFont)
    is_italic = "Italic" in stylename
    is_roman_and_italic = any(a for a in ("slnt", "ital") if a in default_axis_vals)

    wght_axis = next((a for a in fvar.axes if a.axisTag == "wght"), None)
    wght_min = int(wght_axis.minValue)
    wght_max = int(wght_axis.maxValue)

    nametable = ttFont["name"]

    def gen_instances(is_italic):
        results = []
        for wght_val in range(wght_min, wght_max + 100, 100):
            name = (
                WEIGHT_VALUES[wght_val]
                if not is_italic
                else f"{WEIGHT_VALUES[wght_val]} Italic".strip()
            )
            name = name.replace("Regular Italic", "Italic")

            coordinates = default_axis_vals
            coordinates["wght"] = wght_val

            inst = NamedInstance()
            inst.subfamilyNameID = nametable.addName(name)
            inst.coordinates = coordinates
            results.append(inst)
        return results

    instances = []
    if is_roman_and_italic:
        for bool_ in (False, True):
            instances += gen_instances(is_italic=bool_)
    elif is_italic:
        instances += gen_instances(is_italic=True)
    else:
        instances += gen_instances(is_italic=False)
    fvar.instances = instances


def update_nametable(ttFont, family_name=None, style_name=None):
    """Update a static font's name table. The updated name table will conform
    to the Google Fonts support styles table:
    https://github.com/googlefonts/gf-docs/tree/master/Spec#supported-styles

    If a style_name includes tokens other than wght and ital, these tokens
    will be appended to the family name e.g

    Input:
    family_name="MyFont"
    style_name="SemiCondensed SemiBold"

    Output:
    familyName (nameID 1) = "MyFont SemiCondensed SemiBold
    subFamilyName (nameID 2) = "Regular"
    typo familyName (nameID 16) = "MyFont SemiCondensed"
    typo subFamilyName (nameID 17) = "SemiBold"

    Google Fonts has used this model for several years e.g
    https://fonts.google.com/?query=cabin

    Args:
        ttFont:
        family_name: New family name
        style_name: New style name
    """
    if "fvar" in ttFont:
        raise ValueError("Cannot update the nametable for a variable font")
    nametable = ttFont["name"]

    # Remove nametable records which are not Win US English
    # TODO this is too greedy. We should preserve multilingual
    # names in the future. Please note, this has always been an issue.
    platforms = set()
    for rec in nametable.names:
        platforms.add((rec.platformID, rec.platEncID, rec.langID))
    platforms_to_remove = platforms ^ set([(3, 1, 0x409)])
    if platforms_to_remove:
        log.warning(
            f"Removing records which are not Win US English, {list(platforms_to_remove)}"
        )
        for platformID, platEncID, langID in platforms_to_remove:
            nametable.removeNames(
                platformID=platformID, platEncID=platEncID, langID=langID
            )

    # Remove any name records which contain linebreaks
    contains_linebreaks = []
    for r in nametable.names:
        for char in ("\n", "\r"):
            if char in r.toUnicode():
                contains_linebreaks.append(r.nameID)
    for nameID in contains_linebreaks:
        nametable.removeNames(nameID)

    if not family_name:
        family_name = font_familyname(ttFont)

    if not style_name:
        style_name = font_stylename(ttFont)

    is_ribbi = style_name in ("Regular", "Bold", "Italic", "Bold Italic")

    nameids = {}
    if is_ribbi:
        nameids[1] = family_name
        nameids[2] = style_name
    else:
        tokens = style_name.split()
        family_name_suffix = " ".join([t for t in tokens if t not in ["Italic"]])
        nameids[1] = f"{family_name} {family_name_suffix}".strip()
        nameids[2] = "Regular" if "Italic" not in tokens else "Italic"

        typo_family_suffix = " ".join(
            t for t in tokens if t not in list(WEIGHT_NAMES) + ["Italic"]
        )
        nameids[16] = f"{family_name} {typo_family_suffix}".strip()
        typo_style = " ".join(t for t in tokens if t in list(WEIGHT_NAMES) + ["Italic"])
        nameids[17] = typo_style

    family_name = nameids.get(16) or nameids.get(1)
    style_name = nameids.get(17) or nameids.get(2)

    # create NameIDs 3, 4, 6
    nameids[4] = f"{family_name} {style_name}"
    nameids[6] = f"{family_name.replace(' ', '')}-{style_name.replace(' ', '')}"
    nameids[3] = _unique_name(ttFont, nameids)

    # Pass through all records and replace occurences of the old family name
    # with the new family name
    current_family_name = font_familyname(ttFont)
    for record in nametable.names:
        string = record.toUnicode()
        if current_family_name in string:
            nametable.setName(
                string.replace(current_family_name, family_name),
                record.nameID,
                record.platformID,
                record.platEncID,
                record.langID,
            )

    # Remove previous typographic names
    for nameID in (16, 17):
        nametable.removeNames(nameID=nameID)

    # Update nametable with new names
    for nameID, string in nameids.items():
        nametable.setName(string, nameID, 3, 1, 0x409)


def _unique_name(ttFont, nameids):
    font_version = _font_version(ttFont)
    vendor = ttFont["OS/2"].achVendID.strip()
    ps_name = nameids[6]
    return f"{font_version};{vendor};{ps_name}"


def _font_version(font, platEncLang=(3, 1, 0x409)):
    nameRecord = font["name"].getName(5, *platEncLang)
    if nameRecord is None:
        return f'{font["head"].fontRevision:.3f}'
    # "Version 1.101; ttfautohint (v1.8.1.43-b0c9)" --> "1.101"
    # Also works fine with inputs "Version 1.101" or "1.101" etc
    versionNumber = nameRecord.toUnicode().split(";")[0]
    return versionNumber.lstrip("Version ").strip()


def fix_nametable(ttFont):
    """Fix a static font's name table so it conforms to the Google Fonts
    supported styles table:
    https://github.com/googlefonts/gf-docs/tree/master/Spec#supported-styles

    Args:
        ttFont: a TTFont instance
    """
    if "fvar" in ttFont:
        # TODO, regen the nametable so it reflects the default fvar axes
        # coordinates. Implement once https://github.com/fonttools/fonttools/pull/2078
        # is merged.
        return
    family_name = font_familyname(ttFont)
    style_name = font_stylename(ttFont)
    update_nametable(ttFont, family_name, style_name)


def _validate_family(ttFonts):
    family_is_vf(ttFonts)
    family_names = set(font_familyname(f) for f in ttFonts)
    if len(family_names) != 1:
        raise ValueError(f"Multiple families found {family_names}")
    return True


def inherit_vertical_metrics(ttFonts, family_name=None):
    """Inherit the vertical metrics from the same family which is
    hosted on Google Fonts.

    Args:
        ttFonts: a list of TTFont instances which belong to a family
        family_name: Optional string which allows users to specify a
            different family to inherit from e.g "Maven Pro".
    """
    family_name = (
        font_familyname(ttFonts[0])
        if not family_name
        else family_name
    )

    gf_fonts = list(map(TTFont, download_family_from_Google_Fonts(family_name)))
    gf_fonts = {font_stylename(f): f for f in gf_fonts}
    # TODO (Marc F) use Regular font instead. If VF use font which has Regular
    # instance
    gf_fallback = list(gf_fonts.values())[0]

    fonts = {font_stylename(f): f for f in ttFonts}
    for style, font in fonts.items():
        if style in gf_fonts:
            src_font = gf_fonts[style]
        else:
            src_font = gf_fallback
        copy_vertical_metrics(src_font, font)

        if typo_metrics_enabled(src_font):
            font["OS/2"].fsSelection |= 1 << 7


def fix_vertical_metrics(ttFonts):
    """Fix a family's vertical metrics based on:
    https://github.com/googlefonts/gf-docs/tree/master/VerticalMetrics

    Args:
        ttFonts: a list of TTFont instances which belong to a family
    """
    src_font = next((f for f in ttFonts if font_stylename(f) == "Regular"), ttFonts[0])

    # TODO (Marc F) CJK Fonts?

    # If OS/2.fsSelection bit 7 isn't enabled, enable it and set the typo metrics
    # to the previous win metrics.
    if not typo_metrics_enabled(src_font):
        src_font["OS/2"].fsSelection |= 1 << 7  # enable USE_TYPO_METRICS
        src_font["OS/2"].sTypoAscender = src_font["OS/2"].usWinAscent
        src_font["OS/2"].sTypoDescender = -src_font["OS/2"].usWinDescent
        src_font["OS/2"].sTypoLineGap = 0

    # Set the hhea metrics so they are the same as the typo
    src_font["hhea"].ascent = src_font["OS/2"].sTypoAscender
    src_font["hhea"].descent = src_font["OS/2"].sTypoDescender
    src_font["hhea"].lineGap = src_font["OS/2"].sTypoLineGap

    # Set the win Ascent and win Descent to match the family's bounding box
    win_desc, win_asc = family_bounding_box(ttFonts)
    src_font["OS/2"].usWinAscent = win_asc
    src_font["OS/2"].usWinDescent = abs(win_desc)

    # Set all fonts vertical metrics so they match the src_font
    for ttFont in ttFonts:
        ttFont["OS/2"].fsSelection |= 1 << 7
        copy_vertical_metrics(src_font, ttFont)


def copy_vertical_metrics(src_font, dst_font):
    for table, key in [
        ("OS/2", "usWinAscent"),
        ("OS/2", "usWinDescent"),
        ("OS/2", "sTypoAscender"),
        ("OS/2", "sTypoDescender"),
        ("OS/2", "sTypoLineGap"),
        ("hhea", "ascent"),
        ("hhea", "descent"),
        ("hhea", "lineGap"),
    ]:
        val = getattr(src_font[table], key)
        setattr(dst_font[table], key, val)


def family_bounding_box(ttFonts):
    y_min = min(f["head"].yMin for f in ttFonts)
    y_max = max(f["head"].yMax for f in ttFonts)
    return y_min, y_max


def typo_metrics_enabled(ttFont):
    return True if ttFont["OS/2"].fsSelection & (1 << 7) else False


def family_is_vf(ttFonts):
    has_fvar = ["fvar" in ttFont for ttFont in ttFonts]
    if any(has_fvar):
        if all(has_fvar):
            return True
        raise ValueError("Families cannot contain both static and variable fonts")
    return False


def fix_italic_angle(ttFont):
    style_name = font_stylename(ttFont)
    if "Italic" not in style_name and ttFont["post"].italicAngle != 0:
        ttFont["post"].italicAngle = 0
    # TODO (Marc F) implement for italic fonts


def fix_font(font, include_source_fixes=False):
    font["OS/2"].version = 4
    if "DSIG" not in font:
        add_dummy_dsig(font)

    if "fpgm" in font:
        fix_hinted_font(font)
    else:
        fix_unhinted_font(font)

    if "fvar" in font:
        remove_tables(font, ["MVAR"])

    if include_source_fixes:
        log.warning(
            "include-source-fixes is enabled. Please consider fixing the "
            "source files instead."
        )
        remove_tables(font)
        fix_nametable(font)
        fix_fs_type(font)
        fix_fs_selection(font)
        fix_mac_style(font)
        fix_weight_class(font)
        fix_italic_angle(font)

        if "fvar" in font:
            fix_fvar_instances(font)
            # TODO (Marc F) add gen-stat once merged
            # https://github.com/googlefonts/gftools/pull/263


def fix_family(fonts, include_source_fixes=False):
    """Fix all fonts in a family"""
    _validate_family(fonts)
    family_name = font_familyname(fonts[0])

    for font in fonts:
        fix_font(font, include_source_fixes=include_source_fixes)

    if include_source_fixes:
        try:
            if Google_Fonts_has_family(family_name):
                inherit_vertical_metrics(fonts)
            else:
                log.warning(
                    f"{family_name} is not on Google Fonts. Skipping "
                    "regression fixes"
                )
        except FileNotFoundError:
            log.warning(
                f"Google Fonts api key not found so we can't regression "
                "fix fonts. See Repo readme to add keys."
            )
        fix_vertical_metrics(fonts)


def stylename_to_axes(font_style, axisreg=axis_registry):
    """Get axis names for stylename particles using the axis registry e.g

    "Condensed Bold Italic" --> ["wdth", "wght", "ital"]

    Args:
        axisreg: gf axis registry
        font_style: str

    Returns: list(str,...)
    """
    axes = []
    seen = set()

    style_tokens = font_style.split()
    for token in style_tokens:
        for axis_tag, axis_object in axisreg.items():
            if token in [f.name for f in axis_object.fallback]:
                seen.add(token)
                axes.append(axis_tag)

    unparsed_tokens = set(style_tokens) - seen
    if unparsed_tokens:
        log.warning(
            "Following style tokens were not found in Axis Registry "
            "{list(unparsed_tokens)}. Axis Values will not be created "
            "for these tokens"
        )
    return axes


def _default_axis_value(axis, axisreg=axis_registry):
    axis_record = axisreg[axis]
    default_value = axis_record.default_value
    default_name = next(
        (i.name for i in axis_record.fallback if i.value == default_value), None
    )
    return _add_axis_value(default_name, default_value, flags=ELIDABLE_AXIS_VALUE_NAME)


def _gen_stat_using_fvar(axis_reg, font):
    """Generate a STAT table using a font's fvar and the GF axis registry.

    This function will not determine the relationship between font in a
    variable font family.

    Args:
        axis_reg: gf axis registry
        font: TTFont instance
    """
    fvar = font["fvar"]

    axis_defaults = {a.axisTag: a.defaultValue for a in fvar.axes}
    results = {}
    for axis in fvar.axes:
        axis_tag = axis.axisTag
        if axis_tag not in axis_reg:
            log.warning(
                f"'{axis_tag}' isn't in our axis registry. Please open an issue "
                "to discuss the inclusion of this axis, "
                "https://github.com/google/fonts/issues"
            )
            continue
        results[axis_tag] = {
            "tag": axis_tag,
            "name": axis_reg[axis_tag].display_name,
            "values": [],
        }
        # Add Axis Values
        min_value = axis.minValue
        max_value = axis.maxValue
        for fallback in axis_reg[axis_tag].fallback:
            if fallback.value >= min_value and fallback.value <= max_value:
                axis_value = _add_axis_value(fallback.name, fallback.value)
                # set elided axis_values
                # if axis is opsz, we want to use the fvar default
                if axis_tag == "opsz" and axis_defaults[axis_tag] == fallback.value:
                    axis_value["flags"] |= ELIDABLE_AXIS_VALUE_NAME
                # for all other weights, we want to use the axis reg default
                elif fallback.value == axis_reg[axis_tag].default_value:
                    axis_value["flags"] |= ELIDABLE_AXIS_VALUE_NAME
                results[axis_tag]["values"].append(axis_value)
    return results


def _add_axis_value(style_name, value, flags=0x0, linked_value=None):
    value = {"value": value, "name": style_name, "flags": flags}
    if linked_value:
        value["linkedValue"] = linked_value
    return value



def gen_stat_tables(
    fonts, axis_order, elided_axis_values=None, axis_reg=axis_registry
):
    """
    Generate a stat table for each font in a family using the Google Fonts
    Axis Registry.

    Heuristic:
    - Collect all the axes which exist in all font stylenames e.g
      "Bold Italic" --> set(["wght", "ital"])
    - Iterate through each font
        - Build a STAT table for each font using the axis registry and
          each font's fvar table
        - If a font's stylename features an axis which isn't in the fvar,
          add an Axis record and include an Axis Value for the found
          stylename token e.g
        "Bold Italic" --> axis=Ital, axis_value={"name": "Italic", "value": 1}
        - If a font's stylename doesn't include a found axis and isn't in
          the fvar, create a new Axis record for this axis and include an
          Axis Value for the default value and set it to elided e.g
          "Bold" --> axis=Ital, axis_value={
              "name": "Italics", "value": 0, "flags": 0x2
          }

    Args:
        fonts: [TTFont]
        axis_reg: dict
    """
    # Which axes exist in all font stylenames
    family_axes = set()
    for font in fonts:
        font_style = font_stylename(font)
        family_axes |= set(stylename_to_axes(font_style))

    stat_tbls = []
    for font in fonts:
        stat_tbl = _gen_stat_using_fvar(axis_reg, font)

        font_style = font_stylename(font)
        font_style_axes = stylename_to_axes(font_style)
        # {"wght": "Regular", "ital": "Roman", ...}
        font_style_tokens = {k: v for k, v in zip(font_style_axes, font_style.split())}

        # Add axes to font which exist across the family but are not in the font's fvar
        axes_missing = family_axes - set(stat_tbl)
        for axis in axes_missing:
            axis_record = {
                "tag": axis,
                "name": axis_reg[axis].display_name,
                "values": [],
            }
            # Add axis value for axis which isn't in the fvar or font stylename
            if axis not in font_style_tokens:
                axis_record["values"].append(_default_axis_value(axis, axis_reg))
            # Add axis value for axis which isn't in the fvar but does exist in
            # the font stylename
            else:
                style_name = font_style_tokens[axis]
                value = next(
                    (i.value for i in axis_reg[axis].fallback if i.name == style_name),
                    None,
                )
                axis_value = _add_axis_value(style_name, value)
                axis_record["values"].append(axis_value)
            stat_tbl[axis] = axis_record
        stat_tbls.append(stat_tbl)

    # add linkedValues to Axis Values in each stat table
    seen_axis_values = {}
    for stat_tbl in stat_tbls:
        for axis in stat_tbl:
            if axis not in seen_axis_values:
                seen_axis_values[axis] = set()
            seen_axis_values[axis] |= set(
                [i["value"] for i in stat_tbl[axis]["values"]]
            )
    for stat_tbl in stat_tbls:
        for axis_tag, axis_obj in stat_tbl.items():
            if axis_tag not in LINKED_VALUES:
                continue
            start, end = LINKED_VALUES[axis_tag]
            for axis_value in axis_obj["values"]:
                if axis_value["value"] == start and end in seen_axis_values[axis_tag]:
                    axis_value["linkedValue"] = end

    # add stat table to each font
    # TODO make axis_order an optional arg. We can only do this once we
    # have established an axis order in the axis registry
    seen_axes = set(axis_tag for axis in stat_tbls for axis_tag in axis)
    axes_not_ordered = seen_axes - set(axis_order)
    if axes_not_ordered:
        raise ValueError(f"Axis order arg is missing {axes_not_ordered} axes.")
    axis_order = [a for a in axis_order if a in seen_axes]
    for stat_tbl, font in zip(stat_tbls, fonts):
        axes = [stat_tbl[a] for a in axis_order]

        if elided_axis_values:
            _update_elided_axis_values(axes, elided_axis_values)
        buildStatTable(font, axes)



def _update_elided_axis_values(stat, elided_values):
    """Overwrite which axis values should be elided.

    Args:
        stat: a stat table
        elided_values: dict structured as {"axisTag": [100,200 ...]}"""
    for axis in stat:
        if axis["tag"] not in elided_values:
            continue
        for val in axis["values"]:
            if val["value"] in elided_values[axis["tag"]]:
                val["flags"] |= ELIDABLE_AXIS_VALUE_NAME
            else:
                val["flags"] &= ~ELIDABLE_AXIS_VALUE_NAME

