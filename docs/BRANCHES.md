# Branches: generic and variants

| Branch | What it is | Releases |
|---|---|---|
| `main` | The **generic** plugin: no country- or office-specific settings. All shared development lands here. | `v<version>`, `QGIS2VectorTilesFork-<version>.zip` |
| `hu-hesz` | **Hungarian zoning-plan variant**: `main` plus the Hungarian HÉSZ / `szab_ov` preset (layer and field conventions, Hungarian restriction catalogue with legal references, building-code fields), later the HÉSZ import. | `v<version>-hu`, `QGIS2VectorTilesFork-<version>-hu.zip` |

## Rules that keep the two in sync

1. **Shared work goes to `main`** (features, fixes, viewer, tests). Never develop shared code on
   a variant branch.
2. **A variant only adds files**: its preset module in `src/publishing/presets/`, `variant.json`
   (zip suffix and plugin name), its own docs (`docs/hu/`), tests
   (`tests/**/test_preset_*.py`) and release notes (`releases/RELEASE_NOTES_<version>-hu.md`).
   It does not edit files that exist on `main`. Then merging `main` into it never conflicts.
3. If a variant needs a hook that does not exist yet, add the **generic** hook on `main` first
   (like `publishing/presets`, discovered automatically), then use it from the variant.
4. **Sync is automatic**: after every push to `main` the *Sync variant branches* workflow
   merges `main` into `hu-hesz` and pushes. If the merge has conflicts, or `main` changed a
   file under `.github/workflows/` (the Actions token may not push those), it opens an
   issue instead. Then merge by hand:
   `git checkout hu-hesz && git merge main && git push`.
5. **Releases**: bump `metadata.txt` on `main` and release `main`. After the sync, build the
   variant zip on `hu-hesz` (`python3 tools/build_release.py`, which reads `variant.json`), add
   its notes, commit, and run the *Release* workflow on `hu-hesz` with the same version. Both
   zips install into the same plugin folder, so installing one replaces the other. Profiles
   saved in projects are the same for both.
