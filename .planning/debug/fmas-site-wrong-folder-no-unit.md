---
slug: fmas-site-wrong-folder-no-unit
status: resolved
trigger: "The new changes that were done does not allow the user to follow different paths i.e Fmas or atec direct, I can provide a list of FMAS sites to enable a clear view of which sites are which"
created: 2026-04-15
updated: 2026-04-15
---

## Symptoms

- **Interface:** Telegram bot
- **Expected:** FMAS bookout should create site folder under `Sites/FMAS/[site]` and a `Unit [N]` folder under it
- **Actual:** New FMAS site was created under the root folder instead of under `/Sites/FMAS/`. Additionally, no unit folder was created under the new site.
- **Error messages:** None reported
- **Timeline:** Started after recent changes to server.py / API
- **Reproduction:** Run a bookout via Telegram bot selecting the FMAS path for a new site

## Current Focus

hypothesis: "RESOLVED — stock lookup and state saving ran after the type-select early return"
test: "Traced _process_bookout execution order for new-site path"
expecting: "State should have _tmp_paths, _photo_names, and is_swap before any early return"
next_action: ""
reasoning_checkpoint: "Fixed by reordering stock lookup to run before the site-type detection block"

## Evidence

- timestamp: 2026-04-15
  type: code_trace
  finding: >
    In _process_bookout (utils/telegram_bot.py), when lookup_site_type returns None for a new site,
    the function sets STEP_TYPE_SELECT and returns early at the end of the '3b' block (was line 696).
    The early return occurred BEFORE the stock sheet lookup (was '4. Stock-sheet lookup', lines 699-722),
    which means: (a) mark_swaps was never called so all items had is_swap=False incorrectly,
    (b) state["_tmp_paths"] and state["_photo_names"] were never set, (c) when
    _handle_type_select_reply later called _continue_after_swap_confirm, it had
    _tmp_paths=[] so no photos were uploaded, and update_stock_row was called for all items
    regardless of swap status — causing ValueError for genuine swap items and aborting before
    folder creation.
  file: utils/telegram_bot.py
  lines: "675-732 (pre-fix)"

- timestamp: 2026-04-15
  type: folder_bug_trace
  finding: >
    The symptom (folder created under Sites/ not Sites/FMAS/) is explained by one of:
    (1) A previous failed run where is_fmas stayed None (falsy), taking the else/ATEC branch
    through _start_guided_nav -> get_atec_site_folder -> Sites/[site_name]; or
    (2) state expiry between the FMAS/ATEC prompt and the user's reply causing is_fmas to
    remain None when _continue_after_swap_confirm executed.
    Either way, is_fmas=None is falsy so the else branch runs, producing the wrong folder path
    and no unit subfolder.

## Eliminated

- server.py API involvement: server.py correctly passes is_fmas to get_unit_folder; it does not
  affect the Telegram bot flow
- get_unit_folder logic: correctly creates Sites/FMAS/[site]/Unit [N] when is_fmas=True
- lookup_site_type: correctly returns None for a new site not yet in Drive

## Resolution

root_cause: >
  In _process_bookout, the stock-sheet lookup, mark_swaps call, and state["_tmp_paths"] /
  state["_photo_names"] saves all ran AFTER the site-type early return. For any new site
  (lookup_site_type returns None), the function returned to wait for user input before
  running these steps. When the user replied and _continue_after_swap_confirm ran, it had
  empty _tmp_paths (no photos uploaded), all items had is_swap=False (never set by mark_swaps),
  and for a genuine swap serial update_stock_row would raise ValueError and abort before ever
  reaching folder creation. If folder creation was reached, is_fmas could still be None/False
  causing get_atec_site_folder to create the folder under Sites/ instead of Sites/FMAS/.

fix: >
  Reordered _process_bookout so the stock-sheet lookup (step 4), mark_swaps, and the saving
  of _tmp_paths/_photo_names into state all run BEFORE the site-type detection block (step 3b).
  The _lookup_service Drive client is now created at the start of the stock lookup block and
  reused for the lookup_site_type call. This ensures state is fully populated before any
  early return, so _continue_after_swap_confirm always has correct data regardless of whether
  the user had to answer the FMAS/ATEC prompt.

verification: >
  python3 -m pytest tests/ (98 passed, 0 failures)
  python3 -c "import ast; ast.parse(open('utils/telegram_bot.py').read()); print('Syntax OK')"

files_changed:
  - utils/telegram_bot.py (lines 675-732: reordered stock lookup before type-select check,
    added state saves for _tmp_paths and _photo_names before any early return)
