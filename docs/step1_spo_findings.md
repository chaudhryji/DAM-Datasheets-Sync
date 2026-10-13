# Step 1 findings: SharePoint datasheet library

Source: `sites/Datasheets`, library `PDF`, read on 13 Oct 2026 through Microsoft Graph
(`src/s1_spo_inventory.py`, then `src/s1_spo_profile.py`). Read-only.

## 1. What is there

| | |
|---|---|
| Files | **72,532** (72,531 PDF + 1 `.doc`), 14.87 GB |
| Folders | None. The library is flat. |
| File names | Unique, including case-insensitive. The name is a safe join key. |
| Distinct item codes | 6,704 (between 1 and 44 files each: languages × page format × type) |
| Languages (`LCode`) | ENU 29,743 · NLD 10,906 · DEU 6,382 · ESP 6,382 · CHS 6,377 · FRA 6,374 · ITA 6,367 |
| Page format | A4 57,661 · LTR 14,870 |
| Type (`TypeCode`) | DS 31,258 · ½A4 26,270 · FG_FDA 9,472 · FG_EC 4,864 · FR_ISO 258 · FR_ASTM 166 · ATEX_II 146 · ATEX_III 64 · FG_USDA 32 · ES_ESD 1 |
| Created | 2017 to 2026 (11,564 created in 2026) |
| Last modified by | The generator app for 72,530 files (`DatasheetMigration` 49,217, `AAD-AMM-DatasheetMigration` 23,313). Two files were modified by hand. |
| Last bulk republish | 23,313 files on 11–14 Sep 2026 |

The file name always follows `<ItemCode>_<TypeCode>_<LCode>_<Pageformat>.PDF` and matches the
metadata fields, with only the exceptions listed in section 4.

## 2. Metadata available per file

**Business fields, filled by the generator:**

| Field (internal name) | Fill | Notes |
|---|---|---|
| Item Code (`Item_x0020_Code`) | 100% | Text, e.g. `SBAM577501`. No scientific-notation problem when read through the API. |
| Item Description | 99.97% | 24 rows contain `??` in the source itself, e.g. `Flexam EM 12/2 0+R12 ?? AS` |
| Product Group | 97.3% | Localised per language. See section 3. |
| LCode / LDescription / LMLID | 100% | Clean language keys (`ENU`/`English`/`1033`) |
| Language | 100% | Native language name (`Deutsch`, `简体中国`) |
| Language (second column, GUID name) | 46.5% | Older duplicate column. Ignore it. |
| Pageformat | 100% | `A4` / `LTR` |
| TypeCode | 100% | 10 values, including `½A4` |
| Template | 100% | 89 generator templates, e.g. `SB001`, `FDA001` |
| Proofread | 98.1% | Localised yes/no in 7 languages (`no`, `nee`, `否`, `non`, `nein`, `nei`, `ja`…). Normalise to yes/no. |
| Item No (`Code`, `Item_x0020_No`) | 100% | Two calculated columns, both `RIGHT(Item Code, 6)` |
| Title | 100% | Same as the name, except it keeps the real `½` character (see section 4) |
| Orientation, Pitch, Availability, US Availability | 1 file | Only on the uni Modular Belts file, which suggests they are meant for uni belts |

**File facts, from SharePoint itself:** size (always equals `File Size`), created and modified dates,
version (up to 3,354), and a **content hash (`quickXorHash`) on every file**. The hash lets us
detect content changes without downloading files.

Empty on every file: retention and sensitivity labels, check-in comments, likes, colour tag and description.

## 3. Product Group, which drives the DAM folders

- Only **ENU and NLD** carry the full range of product groups. The other five languages only have Synthetic
  Belts, High Performance Flat Belts, Ultrasync and Roll covering.
- **The cleanest way to derive the folder for every file is the ENU row of the same Item Code.**
  This covers **72,245 of 72,532 files**. No item code has two different ENU product groups.
  It removes all translation and case problems in one step: there is no need to map `合成带`,
  `Kunststoffbänder` and so on.
- ENU values already contain the folder levels as `L1, L2`, e.g. `Engineered belts, Cover` and
  `Timing belts, PU Linear`. These split cleanly into **16 L1 and 9 L2 folders** (Timing belts: 3; Engineered belts: 6; 20 files are plain `Engineered belts`).
- **Kunststoffbänder** has 6,151 rows, exactly the same as every other Synthetic Belts translation. The data
  confirms it is **Synthetic Belts**, not uni Modular Belts.
- **Sausage** is not truncated in the source: ENU is `Engineered belts, Sausage manufacturing belts`.
- Values that need case folding: `Ziplink` (340+145) / `ZipLink` (292+127), and `High performance flat
  belts` (416) / `High Performance Flat Belts` (198, CHS rows).
- Translations are cut at 30 characters (`Correas planas de alto rendimi`), and NLD has typos
  (`pull douwn banden`). These do not matter if we use the ENU route.

**Blank Product Group: 1,977 files.** 1,690 can be recovered from the same item code in another row.
**287 files (104 item codes) cannot be recovered from the data.** Proposals for the business owner:

| Group | Files | Evidence | Proposed |
|---|---|---|---|
| `RMFA*`, template EW002 | 240 | Every description starts with "Endless Woven…" | endless woven |
| `SFVB*` / `SFPU*`, template EC008 | 46 | The other SF* item codes are Soliflex | Soliflex |
| `Tech data 066803.doc` | 1 | Not a datasheet | Exclude |

## 4. Exceptions to handle

| Issue | Files | Handling |
|---|---|---|
| `½` stored URL-encoded in the file name (`SBRY544003_%C2%BDA4_ENU_A4.PDF`), while Title shows `½A4` | **8,381** | Decide which form the DAM uses. It affects file names and long-term links, and must stay stable once links exist. |
| Zero-byte files | 3 | `SBVL589649_FG_FDA_CHS_A4.PDF`, `TBUM000112_DS_NLD_A4.PDF`, `SBCO012218_%C2%BDA4_CHS_A4.PDF`. Skip them and report back to the generator owner. |
| Non-PDF file | 1 | `Tech data 066803.doc`. Exclude. |
| Name breaks the pattern | 1 | `uni_Flex_ONE O R1.6_DS_ENU_A4.pdf` (item code UNIB000359, lowercase `.pdf`, spaces). Keep the name as is. |
| Case variants in Item Description | 3 groups | `black`/`Black` and similar. Harmless, keep as is. |

## 5. What this means for the next steps

- **Change detection:** compare the SharePoint `quickXorHash` and size with what the DAM holds.
  `Modified` is useless as a signal, because the generator rewrites it for every file.
  Still open: whether a republish without content change still changes the hash (the "Printed:" date
  inside the PDF). We can check this on the next bulk republish by comparing hashes.
- **DAM metadata** can be filled 1:1 from: Item Code, Item Description, derived Product Group (L1/L2),
  LCode, LDescription, Pageformat, TypeCode, Template, Proofread (normalised), Created, Modified, size and hash.
